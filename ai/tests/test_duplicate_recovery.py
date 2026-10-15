"""Duplicate rejection is a planning event, not execution or goal completion."""
import json

from ai.contracts.schemas import ExecutionResult
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.tests.test_planner_adaptive import (
    Analyzer, Approver, HeadLLM, LLM, RETRIEVE, Reporter, Retriever, URL,
    compiler, final, observation, planner,
)


class RecoverableRunner:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        failed = request.args.get("tags") == "unsupported"
        return ExecutionResult(
            tool_name=request.tool_name, action=request.action, success=not failed,
            returncode=1 if failed else 0, error="Unsupported argument" if failed else None,
            stdout="" if failed else "Corrected request executed",
        )


def execute(tmp_path, proposals, requests, *, budget):
    head, impl = HeadLLM(*proposals), LLM(*[item for request in requests for item in (RETRIEVE, request)])
    runner, approver, reporter = RecoverableRunner(), Approver(), Reporter()
    graph = build_phase1_graph(
        planner=planner(head), implementor=compiler(impl, Retriever()), run_manager=runner,
        action_filter=approver, analyzer=Analyzer(), reporter=reporter,
    ).compile()
    state = graph.invoke({
        "session_id": "duplicate-recovery", "user_goal": "Validate observed input", "target": {"url": URL},
        "operator_context": {}, "output_dir": str(tmp_path), "max_replans": budget,
    }, config={"recursion_limit": 12 + 9 * (budget + 1)})
    calls = [json.loads(call[1].content) for call in head.calls]
    return state, runner, approver, reporter, calls


def test_duplicate_after_failure_returns_to_head_and_corrected_request_executes(tmp_path):
    state, runner, approver, reporter, calls = execute(tmp_path, [
        observation(), observation(next_step="Different title but unchanged executable request"),
        observation(next_step="Correct the documented arguments"),
        observation(decision="stop", next_step="Required checks complete"),
    ], [final("task-01", "unsupported"), final("task-02", "unsupported"), final("task-03", "corrected")], budget=4)
    assert [request.task_id for request in runner.requests] == ["task-01", "task-03"]
    assert [request.task_id for request in approver.requests] == ["task-01", "task-03"]
    assert [run.task.id for run in state["runs"]] == ["task-01", "task-03"]
    assert [item["outcome"] for item in state["attempt_reflections"]] == [
        "execution_failure", "duplicate_rejected", "negative"]
    assert state["attempt_reflections"][1]["source"] == "runtime"
    assert not state["attempt_reflections"][1]["progress"]
    proposals = [call for call in calls if call["phase"] == "propose"]
    retry_memory = proposals[2]["working_memory"]
    assert retry_memory["feedback"][-1]["outcome"] == "duplicate_rejected"
    assert retry_memory["attempt_ledger"][-1]["attempt_id"] == "task-02"
    assert retry_memory["attempt_ledger"][-1]["execution_status"] == "not_executed"
    assert retry_memory["attempt_ledger"][-1]["duplicate_of"] == "task-01"
    assert retry_memory["attempt_ledger"][-1]["request"]["args"] == {"tags": "unsupported"}
    assert retry_memory["latest_result"]["evidence_ref"] == "attempt:task-01"
    assert any(node["status"] == "rejected" for node in retry_memory["candidate_objectives"])
    assert any(node["status"] in {"viable", "defer"} for node in retry_memory["candidate_objectives"])
    assert retry_memory["goal_and_constraints"]["runtime_limits"]["remaining_replans"] == 3
    assert proposals[2]["force_branch"]
    assert [row["attempt_id"] for row in proposals[3]["working_memory"]["attempt_ledger"]] == [
        "task-01", "task-02", "task-03"]
    assert state["stop_reason"] == reporter.report["stop_reason"] == "Required checks complete"
    assert "task-02" not in state["prepared_requests"]
    assert state["prepared_requests"]["task-03"]["task_id"] == "task-03"
    assert len(state["session_reflection"]["lessons"]) == 3


def test_unending_duplicates_exhaust_planning_budget_without_reexecution(tmp_path):
    state, runner, approver, reporter, calls = execute(
        tmp_path, [observation()] * 3,
        [final(f"task-{index:02d}", "unsupported") for index in range(1, 4)], budget=2)
    assert len(runner.requests) == len(approver.requests) == len(state["runs"]) == 1
    assert state["replan_count"] == 2
    assert state["finished"]
    assert state["stop_reason"] == "Reached max re-plan limit after rejecting repeated executable requests."
    assert reporter.report["stop_reason"] == state["stop_reason"]
    assert len([call for call in calls if call["phase"] == "propose"]) == 3
    assert len([call for call in calls if call["phase"] == "reflect_attempt"]) == 1
    assert [row["attempt_id"] for row in state["working_memory"]["attempt_ledger"]] == [
        "task-01", "task-02", "task-03"]
    assert [item["attempt_id"] for item in state["attempt_reflections"]] == ["task-01", "task-02", "task-03"]
    assert len({entry["task_id"] for entry in state["head_iterations"]}) == 3
    assert all(entry["rejection"]["duplicate_of"] == "task-01" for entry in state["head_iterations"][1:])


def test_changed_request_on_last_budgeted_proposal_executes_then_stops_at_limit(tmp_path):
    state, runner, _, _, calls = execute(
        tmp_path, [observation()] * 3,
        [final("task-01", "unsupported"), final("task-02", "unsupported"), final("task-03", "corrected")], budget=2)
    assert [request.task_id for request in runner.requests] == ["task-01", "task-03"]
    assert state["stop_reason"] == "Reached max re-plan limit."
    assert state["replan_count"] == 2
    assert len([call for call in calls if call["phase"] == "reflect_attempt"]) == 2
    assert state["working_memory"]["latest_result"]["result"]["stdout"] == "Corrected request executed"


def test_head_can_explicitly_stop_after_reviewing_duplicate_feedback(tmp_path):
    state, runner, _, _, calls = execute(
        tmp_path, [observation(), observation(), observation(decision="stop", next_step="Missing required input; no supported alternative")],
        [final("task-01", "unsupported"), final("task-02", "unsupported")], budget=4)
    assert len(runner.requests) == 1
    assert state["stop_reason"] == "Missing required input; no supported alternative"
    last_proposal = [call for call in calls if call["phase"] == "propose"][-1]
    assert last_proposal["working_memory"]["feedback"][-1]["outcome"] == "duplicate_rejected"


def test_compilation_failure_after_duplicate_uses_distinct_id_and_preserves_both_records(tmp_path):
    state, runner, _, _, _ = execute(tmp_path, [observation()] * 3, [
        final("task-01", "unsupported"), final("task-02", "unsupported"), final("wrong-id", "corrected")], budget=4)
    assert len(runner.requests) == 1
    assert [item["attempt_id"] for item in state["attempt_reflections"]] == ["task-01", "task-02", "task-03"]
    assert [item["outcome"] for item in state["attempt_reflections"]] == [
        "execution_failure", "duplicate_rejected", "compilation_failure"]
    assert "Implementor could not produce" in state["stop_reason"]


def test_reflection_model_cannot_invent_runtime_duplicate_rejection():
    response = json.dumps({
        "attempt_id": "task-01", "outcome": "duplicate_rejected", "evidence_refs": ["attempt:task-01"],
        "lesson": "Invented rejection", "next_adjustment": "Choose another request",
        "retry_condition": "Change arguments", "progress": False,
    })
    result = planner(LLM(response, response)).reflect(
        user_goal="Validate observed input", attempt_id="task-01", attempt={"result": {"success": True}},
        findings=[], prior_reflections=[])
    assert result.outcome == "inconclusive" and result.source == "runtime"
