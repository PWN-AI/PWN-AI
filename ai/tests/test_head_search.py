"""Behavioral checks for the head search/reflection and unchanged implementor boundary."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.agents.head.contracts import AttemptReflection, CandidateObjective
from ai.contracts.schemas import ActionDecision, ExecutionResult, TargetSpec, TaskSpec
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.reporting.report_generator import ReportGenerator
from ai.tests.test_planner_adaptive import (
    Analyzer, Approver, HeadLLM, LLM, RETRIEVE, Retriever, Runner, URL,
    compiler, final, observation, planner,
)


def proposal(**changes):
    data = json.loads(observation())
    data.update(changes)
    return data


def review(ids, **changes):
    return {"evaluations": [{
        "candidate_id": key, "disposition": "viable", "evidence_refs": ["context"],
        "assessment": "Supplied endpoint supports this objective.", "evidence_strength": 2,
        "information_gain": 2, "goal_alignment": 2, "cost": 1,
        **changes,
    } for key in ids]}


def initial(llm):
    return planner(llm).plan(user_goal="Check target", target=TargetSpec(url=URL), operator_context={})


def task():
    return TaskSpec(id="task-01", title="Check page", objective="Check page", tool_hint="nuclei_xss",
                    target_mode="url", target=TargetSpec(url=URL))


def replan(head, frontier=(), result=None, reflections=()):
    result = result or ExecutionResult(tool_name="nuclei_xss", action="scan", success=True, returncode=0)
    return head.replan(
        user_goal="Check target", target=TargetSpec(url=URL), operator_context={}, current_task=task(),
        current_result=result, findings=[], remaining_tasks=[], candidate_frontier=frontier,
        reflections=reflections, execution_history=[{"task": task().model_dump(), "result": result.model_dump()}])


def reflection(outcome="negative", progress=True):
    return AttemptReflection(
        attempt_id="task-01", outcome=outcome, evidence_refs=["attempt:task-01"],
        lesson="Completed test resolved one question.", next_adjustment="Inspect remaining evidence.",
        retry_condition="New target evidence or a corrected prerequisite.", progress=progress).model_dump()


def test_separate_review_selects_stronger_alternative_and_retains_conditional_tree():
    data = proposal()
    data["candidates"][1]["followups"] = [{"condition": "Only if actual execution confirms the hypothesis",
                                           "objective": "Validate the newly observed evidence"}]
    scores = review(["branch-00-01", "branch-00-02"])
    scores["evaluations"][1]["information_gain"] = 3
    llm = LLM(json.dumps(data), json.dumps(scores))
    decision = initial(llm)
    assert decision.selected_candidate_id == "branch-00-02"
    assert decision.handoff.next_step == data["candidates"][1]["objective"]
    assert len(decision.candidate_frontier) == 2
    assert [record["phase"] for record in decision.search_trace] == ["propose", "evaluate"]
    assert [json.loads(call[1].content)["phase"] for call in llm.calls] == ["propose", "evaluate"]
    assert len(decision.candidate_frontier[1]["candidate"]["followups"]) == 1
    assert "followups" not in decision.handoff.model_dump()


@pytest.mark.parametrize("reason", ["pruned", "prerequisite"])
def test_high_score_does_not_override_pruning_or_missing_prerequisites(reason):
    data = proposal()
    scores = review(["branch-00-01", "branch-00-02"])
    scores["evaluations"][0].update(evidence_strength=3, information_gain=3, goal_alignment=3)
    if reason == "pruned":
        scores["evaluations"][0]["disposition"] = "prune"
    else:
        data["candidates"][0]["missing_prerequisites"] = ["Observed parameter name"]
    decision = initial(LLM(json.dumps(data), json.dumps(scores)))
    assert decision.selected_candidate_id == "branch-00-02"
    assert decision.candidate_frontier[0]["status"] in {"prune", "defer"}


def test_all_unsuitable_candidates_stop_without_a_handoff():
    scores = review(["branch-00-01", "branch-00-02"], disposition="defer")
    decision = initial(LLM(observation(), json.dumps(scores)))
    assert decision.should_stop and decision.handoff is None
    assert "No viable objective" in decision.stop_reason


def test_complete_fenced_json_is_accepted_but_embedded_or_trailing_prose_is_not():
    scores = json.dumps(review(["branch-00-01", "branch-00-02"]))
    decision = initial(LLM("```json\n" + observation() + "\n```", "```json\n" + scores + "\n```"))
    assert decision.handoff is not None
    for malformed in ["Here is the plan: " + observation(), observation() + "\nExtra instructions"]:
        assert initial(LLM(malformed, malformed)).should_stop


@pytest.mark.parametrize("invalid", ["unknown_reference", "single_initial_candidate", "unknown_candidate", "duplicate_review"])
def test_invalid_search_records_never_become_executable_handoffs(invalid):
    data, scores = proposal(), review(["branch-00-01", "branch-00-02"])
    if invalid == "unknown_reference":
        data["candidates"][0]["evidence_refs"] = ["attempt:never-executed"]
    elif invalid == "single_initial_candidate":
        data.update(mode="direct", candidates=data["candidates"][:1])
    elif invalid == "unknown_candidate":
        scores["evaluations"][0]["candidate_id"] = "invented"
    else:
        scores["evaluations"][1]["candidate_id"] = "branch-00-01"
    responses = [json.dumps(data)] * 2 if invalid in {"unknown_reference", "single_initial_candidate"} else [
        json.dumps(data), json.dumps(scores), json.dumps(scores)]
    decision = initial(LLM(*responses))
    assert decision.should_stop and decision.handoff is None


def test_deferred_alternative_is_reconsidered_using_actual_new_evidence():
    data = proposal()
    data["candidates"][1]["missing_prerequisites"] = ["Need the first test result"]
    first_review = review(["branch-00-01", "branch-00-02"])
    new_data = proposal()
    for index, candidate in enumerate(new_data["candidates"]):
        candidate["objective"] = f"New alternative {index}"
    second_review = review(["branch-00-02", "branch-01-01", "branch-01-02"])
    second_review["evaluations"][0].update(evidence_strength=3, information_gain=3,
                                           evidence_refs=["attempt:task-01"], unmet_prerequisites=[])
    llm = LLM(json.dumps(data), json.dumps(first_review), json.dumps(new_data), json.dumps(second_review))
    head = planner(llm)
    first = head.plan(user_goal="Check target", target=TargetSpec(url=URL), operator_context={})
    assert next(node for node in first.candidate_frontier if node["candidate_id"] == "branch-00-02")["status"] == "defer"
    next(node for node in first.candidate_frontier if node["candidate_id"] == "branch-00-01")["status"] = "attempted"
    decision = replan(head, first.candidate_frontier, reflections=[reflection(progress=False)])
    assert decision.selected_candidate_id == "branch-00-02"
    supplied = json.loads(llm.calls[3][1].content)["candidates_to_evaluate"]
    assert all(node["candidate_id"] != "branch-00-01" for node in supplied)
    assert decision.handoff.next_step == data["candidates"][1]["objective"]


def test_successful_progress_allows_direct_step_but_failure_and_stall_force_branching():
    direct = observation(mode="direct")
    success = planner(LLM(direct, json.dumps(review(["branch-01-01"]))))
    decision = replan(success, reflections=[reflection()])
    assert not decision.should_stop
    for result, refs in [
        (ExecutionResult(tool_name="nuclei_xss", action="scan", success=False, returncode=1), [reflection()]),
        (None, [reflection(progress=False)]),
    ]:
        head = planner(LLM(direct, direct))
        assert replan(head, result=result, reflections=refs).should_stop


def test_conditional_lookahead_is_bounded_and_cannot_contain_fabricated_results():
    candidate = proposal()["candidates"][0]
    with pytest.raises(ValueError):
        CandidateObjective.model_validate({**candidate, "followups": [
            {"condition": "if observed", "objective": "validate"}] * 3})
    with pytest.raises(ValueError):
        CandidateObjective.model_validate({**candidate, "followups": [
            {"condition": "if observed", "objective": "validate", "stdout": "imagined success"}]})


@pytest.mark.parametrize("forced,attempt,compilation_error", [
    ("denied", {"status": "denied"}, ""),
    ("timeout", {"result": {"error": "Tool timed out after 30s"}}, ""),
    ("authentication_failure", {"result": {"error": "Authentication failed"}}, ""),
    ("execution_failure", {"result": {"error": "Unsupported argument"}}, ""),
    ("compilation_failure", {}, "No retrieval documentation"),
])
def test_reflection_cannot_relabel_incomplete_attempt_as_verified_negative(forced, attempt, compilation_error):
    wrong = json.dumps(reflection())
    record = planner(LLM(wrong, wrong)).reflect(
        user_goal="Check target", attempt_id="task-01", attempt=attempt,
        findings=[], prior_reflections=[], compilation_error=compilation_error)
    assert record.outcome == forced
    assert not record.progress
    assert record.source == "runtime"


def test_reflection_failure_preserves_uncertainty_and_final_consolidation_preserves_lessons():
    head = planner(LLM(RuntimeError("offline"), '{"summary": "invented", "lessons": []}',
                       '{"summary": "invented", "lessons": []}'))
    record = head.reflect(user_goal="Check target", attempt_id="task-01",
                          attempt={"result": {"stdout": "unclassified output"}}, findings=[], prior_reflections=[])
    assert record.outcome == "inconclusive" and not record.progress
    session = head.consolidate(user_goal="Check target", reflections=[record.model_dump()], stop_reason="Budget reached")
    assert session.source == "runtime"
    assert session.lessons == [record]
    assert "Budget reached" in session.summary


def graph_run(tmp_path, head, *, runner=None, approver=None, max_replans=0):
    runner, approver = runner or Runner(), approver or Approver()
    graph = build_phase1_graph(planner=planner(head), implementor=compiler(LLM(RETRIEVE, final()), Retriever()),
                              run_manager=runner, action_filter=approver, analyzer=Analyzer(),
                              reporter=ReportGenerator()).compile()
    result = graph.invoke({"session_id": "blend", "user_goal": "Check target", "target": {"url": URL},
                           "operator_context": {}, "output_dir": str(tmp_path), "max_replans": max_replans})
    return result, runner


def test_last_budgeted_attempt_is_reflected_in_session_memory_and_report(tmp_path):
    head = HeadLLM(observation())
    state, runner = graph_run(tmp_path, head)
    assert len(runner.requests) == 1
    assert [json.loads(call[1].content)["phase"] for call in head.calls] == [
        "propose", "evaluate", "reflect_attempt", "reflect_session"]
    assert len(state["attempt_reflections"]) == 1
    assert "limit" in state["stop_reason"]
    assert not list(tmp_path.glob("*_feedback.json"))
    assert state["working_memory"]["feedback"] == state["attempt_reflections"]
    assert state["working_memory"]["attempt_ledger"][0]["outcome"] == "negative"
    report = Path(state["report_path"]).read_text()
    assert "## Session Reflection" in report
    assert "task-01: negative" in report
    json.dumps(state["session_reflection"])


def test_denied_action_never_runs_and_is_reflected_as_denied(tmp_path):
    class Deny:
        def review(self, request):
            return ActionDecision(approved=False, reason="Denied for this attempt")
    state, runner = graph_run(tmp_path, HeadLLM(observation()), approver=Deny())
    assert not runner.requests
    assert state["attempt_reflections"][0]["outcome"] == "denied"
    assert not state["attempt_reflections"][0]["progress"]


def test_runner_exception_is_recorded_and_still_reflected(tmp_path):
    class Broken:
        def execute(self, request):
            raise RuntimeError("Process could not start")
    state, _ = graph_run(tmp_path, HeadLLM(observation()), runner=Broken())
    assert state["runs"][0].status == "failed"
    assert state["runs"][0].result.error == "Process could not start"
    assert len(state["attempt_reflections"]) == 1
    assert state["attempt_reflections"][0]["outcome"] == "execution_failure"


def test_working_frontier_remains_bounded_across_repeated_branching():
    class Dynamic(HeadLLM):
        def invoke(self, messages):
            payload = json.loads(messages[1].content)
            if payload["phase"] == "propose":
                self.calls.append(list(messages))
                data = proposal()
                iteration = payload["working_memory"]["scope"]["iteration"]
                for index, candidate in enumerate(data["candidates"]):
                    candidate["objective"] = f"Objective {iteration}-{index}"
                return SimpleNamespace(content=json.dumps(data))
            return super().invoke(messages)
    head = planner(Dynamic())
    decision = head.plan(user_goal="Check target", target=TargetSpec(url=URL), operator_context={})
    for index in range(10):
        frontier = decision.candidate_frontier
        next(node for node in frontier if node["candidate_id"] == decision.selected_candidate_id)["status"] = "attempted"
        decision = head.replan(
            user_goal="Check target", target=TargetSpec(url=URL), operator_context={}, current_task=task(),
            current_result=ExecutionResult(tool_name="nuclei_xss", action="scan", success=False, returncode=1),
            findings=[], remaining_tasks=[], candidate_frontier=frontier, replan_iteration=index)
        assert not decision.should_stop
        assert len(decision.candidate_frontier) <= 6


def test_completed_negative_test_is_distinct_from_execution_failure():
    record = reflection(outcome="negative", progress=True)
    head = planner(LLM(json.dumps(record)))
    result = head.reflect(user_goal="Check target", attempt_id="task-01", findings=[], prior_reflections=[],
                          attempt={"result": {"success": True, "returncode": 0,
                                              "stdout": "Completed test: no finding in tested scope"}})
    assert result.outcome == "negative" and result.progress and result.source == "model"


def test_later_session_starts_fresh_and_never_reads_or_writes_feedback_files(tmp_path):
    # An artifact from the removed cross-session system must not influence new sessions.
    marker = "PRIOR_SESSION_FEEDBACK_MUST_NOT_BE_LOADED"
    old_file = tmp_path / "prior_feedback.json"
    old_file.write_text(json.dumps({"target": {"url": URL}, "user_goal": "Check target",
                                    "lessons": [{**reflection(), "lesson": marker}]}))
    first, _ = graph_run(tmp_path, HeadLLM(observation()))
    head = HeadLLM(observation(decision="stop", next_step="Required evidence is unavailable"))
    second, _ = graph_run(tmp_path, head)
    assert marker not in str(head.calls)
    initial_memory = json.loads(head.calls[0][1].content)["working_memory"]
    assert initial_memory["feedback"] == []
    assert initial_memory["attempt_ledger"] == []
    assert initial_memory["latest_result"] is None
    assert initial_memory["candidate_objectives"] == []
    assert first["working_memory"]["feedback"]
    assert second["working_memory"]["feedback"] == []
    assert list(tmp_path.glob("*_feedback.json")) == [old_file]
    assert marker in old_file.read_text()
