"""The feedback role reviews evidence independently and cannot take over planning."""
import json

import pytest

from ai.agents.feedback.agent import FeedbackAgent
from ai.agents.head.planner import HeadPlanner
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.tests.test_planner_adaptive import (
    Analyzer, Approver, FeedbackLLM, HeadLLM, LLM, RETRIEVE, Reporter, Retriever, Runner, URL,
    compiler, final, lesson, observation, planner, returned, reviewer, run_graph,
)


def attempt(index=1, **result):
    return {"attempt_id": f"task-{index:02d}", "task": {"id": f"task-{index:02d}"},
            "result": {"success": True, "returncode": 0, "stdout": "Observed_Exact_Value=A%2FB", **result}}


def review(agent, attempts=None, trace=None):
    return agent.reflect_objective(user_goal="Inspect supplied evidence", attempts=[attempt()] if attempts is None else attempts,
                                   objective={"handoff": {"next_step": "Inspect one input"}}, findings=[],
                                   prior_reflections=[], open_questions=[], trace_sink=trace)


def test_head_and_feedback_calls_are_separate_and_feedback_precedes_next_plan(tmp_path, capsys):
    events = []
    class OrderedHead(HeadLLM):
        def invoke(self, messages):
            events.append("head:" + json.loads(messages[1].content)["phase"])
            return super().invoke(messages)
    class OrderedFeedback(FeedbackLLM):
        def invoke(self, messages):
            events.append("feedback:" + json.loads(messages[1].content)["phase"])
            return super().invoke(messages)
    head = OrderedHead(observation(), observation(next_step="Inspect remaining evidence"),
                       observation(decision="stop", next_step="No further supported work"))
    feedback_model = OrderedFeedback()
    impl = LLM(RETRIEVE, final(), returned(), RETRIEVE, final("task-02", "other"), returned(ids=("task-02",)))
    state, runner, _, _ = run_graph(head, impl, tmp_path, Retriever(), feedback_llm=feedback_model, max_replans=3)
    assert len(runner.requests) == 2
    assert events == ["head:plan_objective", "feedback:reflect_objective", "head:plan_objective",
                      "feedback:reflect_objective", "head:plan_objective", "feedback:reflect_session"]
    next_input = json.loads(head.calls[1][1].content)["working_memory"]
    assert next_input["feedback"][0]["lesson"] == "Retain the actual execution evidence."
    assert next_input["objective_feedback"][0]["summary"]
    assert next_input["latest_result"]["result"]["stdout"] == "execution evidence"
    assert "Phase reflect_objective:" not in head.calls[0][0].content
    assert "[Head Thinking] reflect" not in capsys.readouterr().out
    assert state["head_iterations"][0]["feedback_steps"][0]["accepted"]
    assert state["session_feedback_steps"][0]["phase"] == "reflect_session"


def test_feedback_receives_actual_records_even_when_implementor_report_claims_success():
    llm = FeedbackLLM()
    agent = reviewer(llm)
    result = agent.reflect_objective(
        user_goal="Inspect", attempts=[{**attempt(error="Unsupported argument", success=False),
                                         "required_outcome": "execution_failure"}],
        objective={"implementor_report": {"status": "completed", "summary": "Unverified claim"}},
        findings=[], prior_reflections=[], open_questions=[])
    payload = json.loads(llm.calls[0][1].content)
    assert payload["attempts"][0]["result"]["stdout"] == "Observed_Exact_Value=A%2FB"
    assert result.lessons[0].outcome == "execution_failure" and not result.lessons[0].progress


@pytest.mark.parametrize("bad_record", [
    {"summary": "Unsupported", "lessons": []},
    {"summary": "Unsupported", "lessons": [lesson(), lesson()]},
    {"summary": "Unsupported", "lessons": [lesson("invented")]},
    {"summary": "Unsupported", "lessons": [lesson()], "objective": "Change the goal"},
    {"summary": "Unsupported", "lessons": [{**lesson(), "evidence_refs": ["attempt:invented"]}]},
])
def test_feedback_cannot_omit_invent_or_duplicate_attempts_or_emit_a_new_objective(bad_record):
    text = json.dumps(bad_record)
    llm, trace = LLM(text, text), []
    result = review(reviewer(llm), trace=trace)
    assert len(llm.calls) == 2
    assert result.lessons[0].source == "runtime" and result.lessons[0].outcome == "inconclusive"
    assert all(not event["accepted"] for event in trace)


def test_invalid_feedback_gets_one_repair_with_original_evidence_then_returns():
    good = {"summary": "Completed check reviewed", "lessons": [lesson()]}
    llm, trace = LLM('{"summary": "Missing lessons"}', json.dumps(good)), []
    result = review(reviewer(llm), trace=trace)
    assert result.summary == good["summary"] and result.lessons[0].source == "model"
    assert len(llm.calls) == 2 and [event["accepted"] for event in trace] == [False, True]
    assert llm.calls[1][1].content == llm.calls[0][1].content
    assert "Observed_Exact_Value=A%2FB" in llm.calls[1][1].content


@pytest.mark.parametrize("response", ["Status: need_more_thinking", "Action: execute_tool", "Extra prose\n{}"])
def test_feedback_does_not_enter_an_assessment_or_tool_loop(response):
    llm = LLM(response, response, response)
    result = review(reviewer(llm))
    assert len(llm.calls) == 2 and result.lessons[0].source == "runtime"


@pytest.mark.parametrize("failure", [RuntimeError("Unavailable"), ValueError("Connection configuration invalid")])
def test_transport_failure_preserves_uncertainty_without_invented_lessons_or_retries(failure):
    llm, trace = LLM(failure), []
    result = review(reviewer(llm), attempts=[attempt(success=False, error="Process failed")], trace=trace)
    assert len(llm.calls) == 1 and trace[0]["accepted"] is False
    assert result.lessons[0].outcome == "execution_failure"
    assert result.lessons[0].source == "runtime" and not result.lessons[0].progress


def test_feedback_failure_does_not_prevent_head_from_receiving_fallback_and_replanning(tmp_path):
    head = HeadLLM(observation(), observation(decision="stop", next_step="Review the unavailable feedback"))
    # Objective review fails; session consolidation then succeeds through the same isolated role.
    feedback_model = LLM(RuntimeError("Reviewer unavailable"), json.dumps({"summary": "Uncertain", "unresolved_questions": []}))
    state, runner, _, _ = run_graph(head, LLM(RETRIEVE, final(), returned()), tmp_path, Retriever(),
                                   feedback_llm=feedback_model, max_replans=1)
    assert len(runner.requests) == 1 and len(head.calls) == 2
    fallback = json.loads(head.calls[1][1].content)["working_memory"]["feedback"][0]
    assert fallback["source"] == "runtime" and fallback["outcome"] == "inconclusive"
    assert state["stop_reason"] == "Review the unavailable feedback"


def test_feedback_conversation_is_fresh_for_every_review():
    llm = FeedbackLLM()
    agent = reviewer(llm)
    review(agent, [attempt(1, stdout="FIRST_REVIEW_ONLY")])
    review(agent, [attempt(2, stdout="SECOND_REVIEW_ONLY")])
    assert all(len(call) == 2 for call in llm.calls)
    assert "FIRST_REVIEW_ONLY" not in llm.calls[1][1].content
    assert "SECOND_REVIEW_ONLY" in llm.calls[1][1].content


def test_new_session_cannot_inherit_feedback_through_reused_agent_or_final_state(tmp_path):
    feedback_model = FeedbackLLM()
    app = build_phase1_graph(
        planner=planner(HeadLLM(observation(), observation())), feedback=reviewer(feedback_model),
        implementor=compiler(LLM(RETRIEVE, final(), returned(), RETRIEVE, final(), returned()), Retriever()),
        run_manager=Runner(), action_filter=Approver(), analyzer=Analyzer(), reporter=Reporter()).compile()
    state = {"session_id": "one", "user_goal": "Inspect", "target": {"url": URL},
             "output_dir": str(tmp_path), "max_replans": 0}
    first = app.invoke(state)
    second = app.invoke({**first, "session_id": "two"})
    reviews = [json.loads(call[1].content) for call in feedback_model.calls
               if json.loads(call[1].content)["phase"] == "reflect_objective"]
    assert len(reviews) == 2 and reviews[0]["prior_reflections"] == reviews[1]["prior_reflections"] == []
    assert len(second["attempt_reflections"]) == 1
    assert second["working_memory"]["scope"]["session_id"] == "two"


def test_head_has_no_feedback_methods_and_feedback_has_no_planner_or_tool_methods():
    assert not any(hasattr(HeadPlanner, name) for name in ("reflect", "reflect_objective", "consolidate"))
    assert not any(hasattr(FeedbackAgent, name) for name in ("plan", "replan", "advance", "execute"))
