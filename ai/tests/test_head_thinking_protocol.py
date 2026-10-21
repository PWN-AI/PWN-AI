"""Exercise the real status/prose parser without adapting JSON-only fixtures."""
import json
from pathlib import Path

import pytest

from ai.agents.head.planner import HeadPlanner
from ai.contracts.schemas import TargetSpec
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.tests.test_head_search import proposal, reflection
from ai.tests.test_planner_adaptive import (
    Analyzer, Approver, LLM, RETRIEVE, Reporter, Retriever, Runner, URL, compiler, final, returned, reviewer,
)

PROMPT = Path(__file__).resolve().parents[1] / "agents/head/system_prompt.md"


def ready(record, assessment=""):
    return assessment + ("\n" if assessment else "") + "Status: ready\n" + json.dumps(record)


def head(llm, rounds=3):
    return HeadPlanner(llm, PROMPT, max_thinking_rounds=rounds)


def stop_proposal():
    return proposal(decision="stop", objective="", stop_reason="Required evidence unavailable")


def plan(llm, rounds=3):
    return head(llm, rounds).plan(user_goal="Validate observed input", target=TargetSpec(url=URL), operator_context={})


@pytest.mark.parametrize("assessment", [
    "", "Thought: Compare the supplied alternatives.", "Observation: Input remains unverified.",
    "Correction: The earlier assumption is unsupported.", "The supplied evidence is insufficient.",
    "Observation: Endpoint exists.\nObservation: Authentication remains unknown.",
])
def test_labels_are_optional_and_no_fixed_sections_are_required(assessment):
    result = plan(LLM(ready(stop_proposal(), assessment)))
    assert result.stop_reason == "Required evidence unavailable"
    step = result.search_trace[0]["thinking_steps"][0]
    assert step == {"response": 1, "assessment": assessment, "status": "ready", "accepted": True}


def test_more_thinking_continues_same_phase_with_prior_assessments_before_ready():
    first = "Observation: The supplied endpoint is known, but the next objective needs review.\nStatus: need_more_thinking"
    second = "Thought: Missing evidence prevents a grounded next action.\nStatus: need_more_thinking"
    llm = LLM(first, second, ready(stop_proposal(), "Observation: A concrete prerequisite is missing."))
    result = plan(llm)
    assert result.stop_reason == "Required evidence unavailable"
    assert len(llm.calls) == 3
    assert first in [message.content for message in llm.calls[1]]
    assert second in [message.content for message in llm.calls[2]]
    assert [json.loads(call[1].content)["phase"] for call in llm.calls] == ["plan_objective"] * 3
    assert [step["status"] for step in result.search_trace[0]["thinking_steps"]] == [
        "need_more_thinking", "need_more_thinking", "ready"]


@pytest.mark.parametrize("malformed", [
    json.dumps(stop_proposal()),
    "Observation: Unknown.\nStatus: maybe",
    "Status: need_more_thinking",
    "Observation: Need evidence.\nStatus: need_more_thinking\n" + json.dumps(stop_proposal()),
    "Observation: Done.\nStatus: ready",
    "Status: ready\n" + json.dumps(stop_proposal()) + "\nTrailing commentary",
    "Status: need_more_thinking\nStatus: ready\n" + json.dumps(stop_proposal()),
    json.dumps(stop_proposal()) + "\nStatus: ready\n" + json.dumps(stop_proposal()),
])
def test_invalid_envelope_never_generates_a_handoff(malformed):
    llm = LLM(malformed, malformed)
    result = plan(llm)
    assert result.handoff is None and result.should_stop
    assert "Head planning failed" in result.thinking_summary[0]
    assert len(llm.calls) == 2
    assert all(not step["accepted"] for step in result.search_trace[0]["thinking_steps"])


def test_one_format_correction_does_not_consume_the_assessment_budget():
    llm = LLM("Observation: Need to review the context.\nStatus: need_more_thinking",
              "Status: ready\n{}", ready(stop_proposal(), "Correction: The final record now matches the schema."))
    result = plan(llm, rounds=2)
    assert result.stop_reason == "Required evidence unavailable"
    assert len(llm.calls) == 3
    assert [step["accepted"] for step in result.search_trace[0]["thinking_steps"]] == [True, False, True]


def test_repeated_need_more_thinking_stops_at_configured_budget():
    more = "Observation: Additional assessment is needed.\nStatus: need_more_thinking"
    llm = LLM(more, more)
    result = plan(llm, rounds=2)
    assert len(llm.calls) == 2
    assert result.handoff is None
    assert "thinking budget exhausted" in result.thinking_summary[0]
    assert len(result.search_trace[0]["thinking_steps"]) == 2


def test_fenced_final_json_and_case_insensitive_status_are_accepted():
    text = "Observation: Missing evidence is conclusive for this planning decision.\nstatus:ready\n```json\n" + json.dumps(stop_proposal()) + "\n```"
    assert plan(LLM(text)).stop_reason == "Required evidence unavailable"


def test_ready_still_requires_valid_evidence_references_and_phase_schema():
    data = proposal()
    data["evidence_refs"] = ["attempt:never-executed"]
    result = plan(LLM(ready(data), ready(data)))
    assert result.handoff is None
    assert "Evidence references" in result.thinking_summary[0]


def test_implementor_waits_for_ready_and_assessments_are_archived(tmp_path):
    impl = LLM(RETRIEVE, final(), returned())

    class OrderedHead(LLM):
        def invoke(self, messages):
            if json.loads(messages[1].content)["phase"] == "plan_objective":
                assert not impl.calls
            return super().invoke(messages)

    llm = OrderedHead(
        "The endpoint is observed; assess missing evidence before delegating.\nStatus: need_more_thinking",
        ready(proposal(), "One bounded objective follows from context."),

    )
    runner = Runner()
    feedback_model = LLM(json.dumps({"summary": "Actual attempt reviewed", "lessons": [reflection()]}),
                         json.dumps({"summary": "Attempt reviewed", "unresolved_questions": []}))
    app = build_phase1_graph(planner=head(llm), feedback=reviewer(feedback_model), implementor=compiler(impl, Retriever()),
                             action_filter=Approver(), run_manager=runner, analyzer=Analyzer(), reporter=Reporter()).compile()
    state = app.invoke({"session_id": "thinking", "user_goal": "Validate observed input", "target": {"url": URL},
                        "operator_context": {}, "output_dir": str(tmp_path), "max_replans": 0})
    assert len(runner.requests) == 1
    initial = state["head_iterations"][0]
    assert len(initial["search_trace"]) == 1
    assert len(initial["search_trace"][0]["thinking_steps"]) == 2
    assert len(initial["feedback_steps"]) == 1
    assert len(llm.calls) == 2 and len(feedback_model.calls) == 2
    assert state["session_feedback_steps"][0]["accepted"]
    assert "assess missing evidence before delegating" not in json.dumps(state["working_memory"])


def test_invalid_thinking_budget_is_rejected():
    with pytest.raises(ValueError, match="at least one"):
        head(LLM(), rounds=0)


def test_optional_json_observations_do_not_force_assessment_sections():
    record = stop_proposal()
    result = plan(LLM(ready(record)))
    assert result.stop_reason == "Required evidence unavailable"


def test_prompt_describes_final_fields_without_embedding_a_schema_document():
    llm = LLM(ready(stop_proposal()))
    assert plan(llm).stop_reason == "Required evidence unavailable"
    instruction = llm.calls[0][0].content
    assert "- objective: string (optional" in instruction
    assert '\"$defs\":' not in instruction
    assert "Status: need_more_thinking" in instruction


def test_head_prompt_does_not_prescribe_assessment_labels_or_tree_scoring():
    text = PROMPT.read_text()
    assert all(label not in text for label in ("Thought:", "Observation:", "Correction:", "Phase evaluate:"))
    assert "without prescribed labels" in text
