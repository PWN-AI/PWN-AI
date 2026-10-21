"""Head planning delegates one objective, without a candidate tree or scoring phase."""
import json

import pytest
from pydantic import ValidationError

from ai.agents.head.contracts import ObjectivePlan
from ai.contracts.schemas import TargetSpec, TaskSpec
from ai.tests.test_planner_adaptive import LLM, URL, observation, planner, lesson, reviewer


def proposal(**changes):
    return {**json.loads(observation()), **changes}


def task():
    return TaskSpec(id="task-01", title="Check page", objective="Check page", tool_hint="nuclei_xss",
                    target_mode="url", target=TargetSpec(url=URL))


def reflection(outcome="negative", progress=True):
    return {**lesson(outcome=outcome), "progress": progress}


def initial(llm):
    return planner(llm).plan(user_goal="Check target", target=TargetSpec(url=URL), operator_context={})


def test_single_head_call_produces_a_supported_bounded_objective_without_evaluation():
    llm = LLM(observation())
    decision = initial(llm)
    assert not decision.should_stop
    assert len(llm.calls) == 1
    assert [entry["phase"] for entry in decision.search_trace] == ["plan_objective"]
    handoff = decision.handoff.model_dump()
    assert handoff["next_step"] == proposal()["objective"]
    assert handoff["success_criteria"] and handoff["evidence_refs"] == ["context"]
    assert handoff["verified_facts"] != handoff["hypotheses"]
    assert not {"candidates", "score", "evaluations", "memory_note"} & handoff.keys()


@pytest.mark.parametrize("changes", [
    {"objective": ""}, {"success_criteria": []}, {"evidence_refs": []}, {"observation_summary": ""},
    {"success_criteria": [""]}, {"decision": "stop"}, {"candidates": []},
])
def test_invalid_handoff_contract_is_rejected(changes):
    with pytest.raises(ValidationError):
        ObjectivePlan.model_validate(proposal(**changes))


def test_unknown_evidence_cannot_be_delegated():
    bad = json.dumps(proposal(evidence_refs=["attempt:invented"]))
    decision = initial(LLM(bad, bad))
    assert decision.should_stop and decision.handoff is None
    assert "Evidence references" in decision.thinking_summary[0]


def test_context_is_selected_and_important_values_preserved():
    decision = planner(LLM(observation())).plan(user_goal="Check", target=TargetSpec(url=URL), operator_context={
        "auth_cookie": "Session=Exact%2FValue", "auth_verified": False, "discovered_endpoints": [URL],
        "password": "secret", "llm_notes": ["head only"], "constraints": ["Exact operator constraint"]})
    context = decision.handoff.context
    assert context["auth_cookie"] == "Session=Exact%2FValue" and context["auth_verified"] is False
    assert "password" not in context and "llm_notes" not in context
    assert "Exact operator constraint" in decision.handoff.constraints


@pytest.mark.parametrize("forced,attempt,error", [
    ("denied", {"status": "denied"}, ""),
    ("timeout", {"result": {"error": "Tool timed out after 30s"}}, ""),
    ("authentication_failure", {"result": {"error": "Authentication failed"}}, ""),
    ("execution_failure", {"result": {"error": "Unsupported argument"}}, ""),
    ("compilation_failure", {}, "No retrieval documentation"),
])
def test_reflection_cannot_turn_execution_problems_into_negative_evidence(forced, attempt, error):
    wrong = json.dumps(reflection())
    record = reviewer(LLM(wrong, wrong)).reflect(user_goal="Check", attempt_id="task-01", attempt=attempt,
                                              findings=[], prior_reflections=[], compilation_error=error)
    assert record.outcome == forced and not record.progress and record.source == "runtime"


def test_failed_feedback_keeps_actual_outcomes_and_uncertainty():
    feedback_agent = reviewer(LLM(RuntimeError("offline")))
    record = feedback_agent.reflect_objective(user_goal="Check", attempts=[
        {"attempt_id": "task-01", "result": {"success": False, "error": "Unsupported argument"}},
        {"attempt_id": "task-02", "result": {"success": True, "returncode": 0, "stdout": "Unclassified"}},
    ], objective={}, findings=[], prior_reflections=[], open_questions=[])
    assert [item.outcome for item in record.lessons] == ["execution_failure", "inconclusive"]
    assert all(not item.progress and item.source == "runtime" for item in record.lessons)


@pytest.mark.parametrize("bad_lessons", [[], [reflection(), reflection()], [reflection(outcome="positive")]])
def test_objective_feedback_must_cover_each_actual_attempt_and_respect_execution_status(bad_lessons):
    bad = json.dumps({"summary": "Unsupported assessment", "lessons": bad_lessons})
    result = reviewer(LLM(bad, bad)).reflect_objective(
        user_goal="Check", attempts=[{"attempt_id": "task-01", "result": {"error": "Invalid argument"}}],
        objective={}, findings=[], prior_reflections=[], open_questions=[])
    assert result.lessons[0].outcome == "execution_failure" and result.lessons[0].source == "runtime"


def test_final_summary_cannot_rewrite_original_lessons():
    wrong = '{"summary": "Invented", "lessons": []}'
    result = reviewer(LLM(wrong, wrong)).consolidate(user_goal="Check", reflections=[reflection()], stop_reason="Budget reached")
    assert result.source == "runtime"
    assert result.lessons[0].model_dump() == {**reflection(), "resolved_question": ""}
