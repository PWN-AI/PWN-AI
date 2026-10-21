"""Repeated requests return to the head; rejection never pretends a tool ran."""
import json

from ai.tests.test_planner_adaptive import (
    HeadLLM, LLM, RETRIEVE, Retriever, Runner, final, observation, returned, run_graph,
)


def test_duplicate_within_objective_returns_to_head_and_corrected_next_objective_executes(tmp_path):
    head = HeadLLM(observation(), observation(next_step="Correct the unsupported arguments"),
                   observation(decision="stop", next_step="Required checks complete"))
    impl = LLM(RETRIEVE, final(tags="unsupported"), final("task-02", "unsupported"),
               RETRIEVE, final("task-03", "corrected"), returned(ids=("task-03",)))
    state, runner, approver, _ = run_graph(head, impl, tmp_path, Retriever(), max_replans=3,
        runner=Runner({"success": False, "returncode": 1, "error": "Unsupported argument"}))
    assert [request.task_id for request in runner.requests] == ["task-01", "task-03"]
    assert [request.task_id for request in approver.requests] == ["task-01", "task-03"]
    assert [item["outcome"] for item in state["attempt_reflections"]] == [
        "execution_failure", "duplicate_rejected", "negative"]
    assert state["attempt_reflections"][1]["source"] == "runtime"
    plans = [json.loads(call[1].content) for call in head.calls if json.loads(call[1].content)["phase"] == "plan_objective"]
    memory = plans[1]["working_memory"]
    assert memory["feedback"][-1]["outcome"] == "duplicate_rejected"
    assert memory["attempt_ledger"][-1]["execution_status"] == "not_executed"
    assert memory["attempt_ledger"][-1]["duplicate_of"] == "task-01"
    assert memory["latest_result"]["evidence_ref"] == "attempt:task-01"
    assert [row["attempt_id"] for row in state["working_memory"]["attempt_ledger"]] == ["task-01", "task-02", "task-03"]
    assert "task-02" not in state["prepared_requests"]


def test_duplicates_across_objectives_consume_planning_budget_without_reexecution(tmp_path):
    head = HeadLLM(*[observation()] * 3)
    impl = LLM(RETRIEVE, final(), returned(), RETRIEVE, final("task-02"), RETRIEVE, final("task-03"))
    state, runner, approver, _ = run_graph(head, impl, tmp_path, Retriever(), max_replans=2)
    assert len(runner.requests) == len(approver.requests) == 1
    assert state["replan_count"] == 2 and state["stop_reason"] == "Reached max re-plan limit."
    assert [item["outcome"] for item in state["attempt_reflections"]] == ["negative", "duplicate_rejected", "duplicate_rejected"]


def test_rejection_then_invalid_request_preserves_distinct_ids_and_feedback(tmp_path):
    head = HeadLLM(observation(), observation(), observation())
    bad = final("invented")
    impl = LLM(RETRIEVE, final(), final("task-02"), RETRIEVE, bad, bad, bad,
               returned("blocked", ids=()))
    state, runner, _, _ = run_graph(head, impl, tmp_path, Retriever(), max_replans=2)
    assert len(runner.requests) == 1
    assert [item["attempt_id"] for item in state["attempt_reflections"]] == ["task-01", "task-02", "task-03"]
    assert [item["outcome"] for item in state["attempt_reflections"]] == ["negative", "duplicate_rejected", "compilation_failure"]
