"""Fixtures and integration checks for objective-scoped planner/executor control."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.agents.head.planner import HeadPlanner, ObservationHandoff
from ai.agents.feedback.agent import FeedbackAgent
from ai.agents.implementor.compiler import ImplementorCompiler
from ai.contracts.schemas import ActionDecision, ExecutionResult, TargetSpec
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.rag.retriever import RetrievedDocumentation

ROOT = Path(__file__).resolve().parents[1]
URL = "http://example.com/page?id=1"


class LLM:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def invoke(self, messages):
        self.calls.append(list(messages))
        response = next(self.responses)
        if isinstance(response, Exception):
            raise response
        if callable(response):
            response = response(messages)
        return SimpleNamespace(content=response)


class Retriever:
    def __init__(self):
        self.queries = []

    def retrieve_all_for_tool_result(self, tool):
        self.queries.append(tool)
        return RetrievedDocumentation("REAL DOCUMENTATION: fixture arguments.", (tool,))


def observation(note="head-only memory", decision="continue", next_step="Check the observed page for XSS", **changes):
    data = {"decision": decision, "objective": next_step if decision == "continue" else "",
            "observation_summary": f"Endpoint {URL}; authentication unverified; use documented arguments.",
            "evidence_refs": ["context"], "verified_facts": [f"Operator supplied {URL}"],
            "hypotheses": ["The observed input may mishandle markup."],
            "success_criteria": ["Complete the documented check and record evidence or a conclusive negative result."],
            "constraints": ["Stay within the supplied target."],
            "return_conditions": ["New evidence changes the strategic direction."],
            "memory_note": note, "remaining_objectives": [next_step] if decision == "continue" else [],
            "open_questions": ["Is the observed input exposed?"],
            "stop_reason": next_step if decision == "stop" else ""}
    data.update(changes)
    return json.dumps(data)


def ready_response(payload):
    return "The supplied evidence supports this decision.\nStatus: ready\n" + payload


def lesson(attempt_id="task-01", outcome="negative", source="model"):
    return {"attempt_id": attempt_id, "outcome": outcome, "evidence_refs": [f"attempt:{attempt_id}"],
            "lesson": "Retain the actual execution evidence.", "next_adjustment": "Inspect remaining evidence.",
            "retry_condition": "Only retry with new evidence or corrected inputs.",
            "progress": outcome in {"positive", "negative"}, "source": source}


class HeadLLM(LLM):
    """Only planning calls may reach the head model."""
    def invoke(self, messages):
        assert json.loads(messages[1].content)["phase"] == "plan_objective"
        return super().invoke(messages)


class FeedbackLLM(LLM):
    """Independent feedback model, using only runtime-supplied attempt records."""
    def invoke(self, messages):
        self.calls.append(list(messages))
        payload = json.loads(messages[1].content)
        phase = payload["phase"]
        if phase == "reflect_objective":
            response = {"summary": "Objective attempts reviewed against actual results.", "lessons": [
                lesson(item["attempt_id"], item["required_outcome"] or "negative")
                for item in payload["attempts"] if item["attempt_id"] in payload["reflection_attempt_ids"]]}
        elif phase == "reflect_attempt":
            response = lesson(payload["attempt_id"], payload["required_outcome"] or "negative")
        else:
            assert phase == "reflect_session", f"Unexpected feedback phase: {phase}"
            response = {"summary": "Recorded attempts reviewed.", "unresolved_questions": []}
        return SimpleNamespace(content=json.dumps(response))


def reviewer(llm=None):
    return FeedbackAgent(llm if llm is not None else FeedbackLLM(), ROOT / "agents/feedback/system_prompt.md")


class HeadProtocolFixture:
    def __init__(self, llm):
        self.llm = llm

    def invoke(self, messages):
        response = self.llm.invoke(messages)
        if response.content.lstrip().startswith(("{", "```json")):
            return SimpleNamespace(content=ready_response(response.content))
        return response


def planner(llm):
    return HeadPlanner(HeadProtocolFixture(llm), ROOT / "agents/head/system_prompt.md")


def compiler(llm, retriever):
    return ImplementorCompiler(llm, ROOT / "agents/implementor/system_prompt.md", retriever)


def final(task_id="task-01", tags="xss", tool="nuclei_xss", **changes):
    data = {"task_id": task_id, "tool_name": tool, "action": "scan", "target_mode": "url",
            "target": {"url": URL}, "args": {"tags": tags}, "reasoning": "Use retrieved documentation."}
    data.update(changes)
    return "Observation: Actual documentation supports this request.\nAction: execute_tool\nAction Input: " + json.dumps(data)


def returned(status="completed", ids=("task-01",), **changes):
    data = {"status": status, "summary": "Delegated check finished; original goal still needs head review.",
            "evidence_refs": [f"attempt:{key}" for key in ids], "remaining_uncertainty": ["Other inputs remain untested."]}
    data.update(changes)
    return "Observation: Reviewed the actual execution results.\nAction: return_to_head\nAction Input: " + json.dumps(data)


RETRIEVE = "Observation: Need scan docs.\nAction: retrieve_all_tool_docs\nAction Input: nuclei_xss"


class Approver:
    def __init__(self, approved=True):
        self.requests = []
        self.approved = approved

    def review(self, request):
        self.requests.append(request)
        return ActionDecision(approved=self.approved)


class Runner:
    def __init__(self, *results):
        self.requests = []
        self.results = list(results)

    def execute(self, request, *, timeout_seconds=None):
        self.requests.append(request)
        data = self.results.pop(0) if self.results else {}
        if isinstance(data, Exception):
            raise data
        return ExecutionResult(tool_name=request.tool_name, action=request.action,
                               **{"success": True, "returncode": 0, "stdout": "execution evidence", **data})


class Analyzer:
    def analyze(self, request, result):
        return []


class Reporter:
    def write_markdown_report(self, **kwargs):
        self.report = kwargs
        return Path("report.md")


def run_graph(head_llm, impl_llm, tmp_path, retriever=None, runner=None, approver=None, feedback_llm=None, **limits):
    runner, approver, reporter = runner or Runner(), approver or Approver(), Reporter()
    graph = build_phase1_graph(planner=planner(head_llm), feedback=reviewer(feedback_llm), implementor=compiler(impl_llm, retriever),
                              action_filter=approver, run_manager=runner, analyzer=Analyzer(), reporter=reporter).compile()
    state = graph.invoke({"session_id": "test", "user_goal": "Check XSS", "target": {"url": URL},
                          "operator_context": {"discovered_endpoints": [URL], "password": "secret"},
                          "output_dir": str(tmp_path), "max_replans": 0, **limits}, config={"recursion_limit": 200})
    return state, runner, approver, reporter


def test_multiple_actual_actions_within_one_objective_then_single_head_feedback(tmp_path):
    head = HeadLLM(observation())
    impl = LLM(RETRIEVE, final(), final("task-02", "other"), returned(ids=("task-01", "task-02")))
    feedback_model = FeedbackLLM()
    state, runner, approver, reporter = run_graph(head, impl, tmp_path, Retriever(), feedback_llm=feedback_model)
    assert len(runner.requests) == len(approver.requests) == 2
    assert [json.loads(call[1].content)["phase"] for call in head.calls] == ["plan_objective"]
    assert [json.loads(call[1].content)["phase"] for call in feedback_model.calls] == ["reflect_objective", "reflect_session"]
    second_input = "\n".join(message.content for message in impl.calls[2])
    assert '"actual_execution"' in second_input and '"task-01"' in second_input
    assert "execution evidence" in second_input and "REAL DOCUMENTATION" in second_input
    assert state["working_memory"]["attempt_ledger"][1]["outcome"] == "negative"
    assert len(state["objective_feedback"]) == 1
    assert len(reporter.report["session_reflection"]["lessons"]) == 2
    reflect_input = json.loads(feedback_model.calls[0][1].content)
    assert len(reflect_input["attempts"]) == 2
    assert reflect_input["objective"]["implementor_report"]["status"] == "completed"


def test_feedback_precedes_next_objective_and_local_memory_is_reset(tmp_path):
    head = HeadLLM(observation("private planning note"), observation(next_step="Check remaining observed evidence"),
                   observation(decision="stop", next_step="Goal supported by results"))
    impl = LLM(RETRIEVE, final(), returned(), RETRIEVE, final("task-02", "other"), returned(ids=("task-02",)))
    state, runner, _, _ = run_graph(head, impl, tmp_path, Retriever(), max_replans=3)
    assert len(runner.requests) == 2 and state["stop_reason"] == "Goal supported by results"
    plans = [json.loads(call[1].content) for call in head.calls if json.loads(call[1].content)["phase"] == "plan_objective"]
    assert plans[1]["working_memory"]["feedback"][0]["attempt_id"] == "task-01"
    assert plans[1]["working_memory"]["objective_feedback"][0]["summary"]
    assert "private planning note" in json.dumps(plans[1])
    next_local = "\n".join(message.content for message in impl.calls[3][1:])
    assert "actual_execution" not in next_local and "REAL DOCUMENTATION" not in next_local
    assert "private planning note" not in next_local and "secret" not in next_local
    assert json.loads(impl.calls[3][1].content)["head_handoff"]["hypotheses"]
    assert state["user_goal"] == "Check XSS"
    json.dumps(state, default=lambda item: item.model_dump())


def test_one_action_is_enough_and_no_extra_execution_is_forced(tmp_path):
    impl = LLM(RETRIEVE, final(), returned())
    state, runner, _, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever())
    assert len(runner.requests) == 1
    assert state["objective_report"]["status"] == "completed"


@pytest.mark.parametrize("status", ["blocked", "strategy_change", "inconclusive"])
def test_early_return_with_no_execution_is_valid_and_still_gets_feedback(tmp_path, status):
    state, runner, approver, _ = run_graph(HeadLLM(observation()), LLM(returned(status, ids=())), tmp_path, Retriever())
    assert not runner.requests and not approver.requests
    assert state["objective_report"]["status"] == status
    assert state["objective_feedback"][0]["lessons"] == []


def test_ledger_is_updated_before_the_implementor_decides_again(tmp_path):
    from ai.orchestration.langgraph_flow import build_phase1_graph
    impl = LLM(RETRIEVE, final(), returned())
    app = build_phase1_graph(planner=planner(HeadLLM(observation())), feedback=reviewer(), implementor=compiler(impl, Retriever()),
                            run_manager=Runner(), action_filter=Approver(), analyzer=Analyzer(), reporter=Reporter()).compile()
    snapshots = list(app.stream({"session_id": "ledger", "user_goal": "Check XSS", "target": {"url": URL},
                                 "output_dir": str(tmp_path), "max_replans": 0}, stream_mode="values"))
    ledger_states = [state for state in snapshots if state.get("working_memory", {}).get("attempt_ledger")]
    assert ledger_states[0]["working_memory"]["attempt_ledger"][0]["outcome"] == "awaiting_reflection"
    assert ledger_states[0]["attempt_reflections"] == []
    assert ledger_states[-1]["working_memory"]["attempt_ledger"][0]["outcome"] == "negative"


@pytest.mark.parametrize("limit, expected", [("max_actions_per_objective", "action budget"), ("max_tasks", "total action")])
def test_runtime_action_limits_cannot_be_overridden(tmp_path, limit, expected):
    impl = LLM(RETRIEVE, final(), final("task-02", "other"), final("task-02", "other"), final("task-02", "other"))
    state, runner, approver, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever(), **{limit: 1})
    assert len(runner.requests) == len(approver.requests) == 1
    assert expected in state["return_reason"]
    assert not json.loads(impl.calls[2][-1].content)["runtime_limits"]["can_execute"]
    assert state["attempt_reflections"][0]["attempt_id"] == "task-01"


def test_repeated_failures_return_even_if_implementor_wants_to_continue(tmp_path):
    impl = LLM(RETRIEVE, final(), final("task-02", "corrected"), returned("blocked", ids=("task-01", "task-02")))
    runner = Runner({"success": False, "returncode": 1, "error": "Unsupported argument"},
                    {"success": False, "returncode": 1, "error": "Still unsupported"})
    state, runner, _, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever(), runner=runner)
    assert len(runner.requests) == 2
    assert "Repeated execution failures" in state["return_reason"]
    assert all(item["outcome"] == "execution_failure" and not item["progress"] for item in state["attempt_reflections"])


@pytest.mark.parametrize("runner_data, denied, outcome", [
    ({}, True, "denied"), (RuntimeError("Process could not start"), False, "execution_failure"),
    ({"success": False, "returncode": 1, "error": "Session expired"}, False, "authentication_failure"),
    ({"success": False, "returncode": 1, "raw_output": {"timed_out": True}}, False, "timeout"),
])
def test_denial_execution_error_auth_and_timeout_are_retained(tmp_path, runner_data, denied, outcome):
    impl = LLM(RETRIEVE, final(), returned("blocked"))
    state, runner, approver, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever(),
                                         runner=Runner(runner_data), approver=Approver(not denied))
    assert len(approver.requests) == 1 and len(runner.requests) == (0 if denied else 1)
    assert state["attempt_reflections"][0]["outcome"] == outcome
    assert not state["attempt_reflections"][0]["progress"]
    assert '"actual_execution"' in "\n".join(message.content for message in impl.calls[2])


def test_head_stop_never_calls_implementor(tmp_path):
    impl = LLM()
    state, runner, _, _ = run_graph(HeadLLM(observation(decision="stop", next_step="Missing prerequisite")), impl, tmp_path)
    assert not impl.calls and not runner.requests
    assert state["stop_reason"] == "Missing prerequisite"


def test_fake_results_after_first_retrieval_action_are_discarded(tmp_path, capsys):
    impl = LLM(RETRIEVE + "\nObservation: FAKE DOCS\n" + final(tags="fabricated"), final(), returned())
    state, runner, _, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever())
    assert runner.requests[0].args == {"tags": "xss"}
    assert "FAKE DOCS" not in "\n".join(message.content for message in impl.calls[1])
    assert "REAL DOCUMENTATION" not in capsys.readouterr().out
    assert state["head_iterations"][0]["implementor_steps"][0]["retrieval_observation"].startswith("REAL DOCUMENTATION")


@pytest.mark.parametrize("bad", [final(task_id="invented"), final(target_mode="request_file"),
                                 final(tool="sql_injection"), returned("completed", ids=()),
                                 returned("blocked", ids=("invented",))])
def test_invalid_action_or_unsupported_completion_never_executes(tmp_path, bad):
    impl = LLM(RETRIEVE, bad, bad, bad)
    state, runner, approver, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever())
    assert not runner.requests and not approver.requests
    assert state["attempt_reflections"][0]["outcome"] == "compilation_failure"


@pytest.mark.parametrize("empty", [False, True])
def test_missing_or_empty_rag_never_produces_execution(tmp_path, empty):
    retriever = Retriever() if empty else None
    if empty:
        retriever.retrieve_all_for_tool_result = lambda tool: RetrievedDocumentation("", ())
    state, runner, approver, _ = run_graph(HeadLLM(observation()), LLM(RETRIEVE, RETRIEVE, RETRIEVE), tmp_path, retriever)
    assert not runner.requests and not approver.requests
    assert state["attempt_reflections"][0]["outcome"] == "compilation_failure"


def test_current_cookie_is_injected_and_actual_executed_request_is_returned(tmp_path, monkeypatch):
    import ai.orchestration.langgraph_flow as flow
    monkeypatch.setattr(flow, "refresh_session_cookie", lambda **kwargs: "session=fresh")
    impl = LLM(RETRIEVE, final(), returned())
    app = build_phase1_graph(planner=planner(HeadLLM(observation())), feedback=reviewer(), implementor=compiler(impl, Retriever()),
                            run_manager=Runner(), action_filter=Approver(), analyzer=Analyzer(), reporter=Reporter()).compile()
    state = app.invoke({"session_id": "cookie", "user_goal": "Check XSS", "target": {"url": URL},
                        "output_dir": str(tmp_path), "operator_context": {"auth_cookie": "session=old"}, "max_replans": 0})
    assert state["operator_context"]["auth_cookie"] == "session=fresh"
    assert state["runs"][0].request.args["cookie"] == "session=fresh"
    assert "session=fresh" in "\n".join(message.content for message in impl.calls[2])
    json.dumps(state, default=lambda item: item.model_dump())


def test_missing_documentation_is_not_successful_retrieval():
    from ai.rag.retriever import ToolRetriever
    with pytest.raises(RuntimeError, match="not initialized"):
        ToolRetriever(SimpleNamespace(is_ready=False)).retrieve("tool docs")
    retriever = ToolRetriever(SimpleNamespace(is_ready=True, search=lambda *a, **k: [], _documents=[]))
    with pytest.raises(LookupError):
        retriever.retrieve_all_for_tool("nuclei_xss")


def test_unchanged_results_trigger_return_before_another_action(tmp_path):
    impl = LLM(RETRIEVE, final(), final("task-02", "second"), final("task-03", "third"),
               returned("inconclusive", ids=("task-01", "task-02", "task-03")))
    state, runner, _, _ = run_graph(HeadLLM(observation()), impl, tmp_path, Retriever(), max_actions_per_objective=8)
    assert len(runner.requests) == 3
    assert "unchanged execution results" in state["return_reason"]


def test_expired_deadline_during_retrieval_prevents_execution(tmp_path, monkeypatch):
    import ai.orchestration.langgraph_flow as flow
    clock = [100.0]
    monkeypatch.setattr(flow.time, "monotonic", lambda: clock[0])
    retriever = Retriever()
    def slow_retrieval(tool):
        clock[0] += 20
        return RetrievedDocumentation("REAL DOCUMENTATION", (tool,))
    retriever.retrieve_all_for_tool_result = slow_retrieval
    state, runner, approver, _ = run_graph(HeadLLM(observation()), LLM(RETRIEVE, final()), tmp_path, retriever,
                                         objective_timeout_seconds=10)
    assert not runner.requests and not approver.requests
    assert "time budget" in state["objective_report"]["summary"]


def test_analyzer_failure_does_not_lose_executed_result_or_stop_feedback(tmp_path):
    class BrokenAnalyzer:
        def analyze(self, request, result):
            raise RuntimeError("Analyzer offline")
    app = build_phase1_graph(planner=planner(HeadLLM(observation())), feedback=reviewer(),
                            implementor=compiler(LLM(RETRIEVE, final(), returned()), Retriever()),
                            run_manager=Runner(), action_filter=Approver(), analyzer=BrokenAnalyzer(), reporter=Reporter()).compile()
    state = app.invoke({"session_id": "analysis", "user_goal": "Check", "target": {"url": URL},
                        "output_dir": str(tmp_path), "max_replans": 0})
    assert state["working_memory"]["latest_result"]["result"]["stdout"] == "execution evidence"
    assert len(state["attempt_reflections"]) == 1
    assert "Analyzer offline" in state["notes"][-1]


def test_reused_session_state_and_output_directory_start_with_fresh_history(tmp_path):
    first, _, _, _ = run_graph(HeadLLM(observation()), LLM(RETRIEVE, final(), returned()), tmp_path, Retriever())
    head = HeadLLM(observation(decision="stop", next_step="Missing input"))
    stale = dict(first)
    app = build_phase1_graph(planner=planner(head), feedback=reviewer(), implementor=compiler(LLM(), Retriever()),
                            run_manager=Runner(), action_filter=Approver(), analyzer=Analyzer(), reporter=Reporter()).compile()
    result = app.invoke(stale)
    assert result["runs"] == result["attempt_reflections"] == result["objective_feedback"] == []
    assert not list(tmp_path.glob("*_feedback.json"))
    payload = json.loads(head.calls[0][1].content)
    assert payload["working_memory"]["attempt_ledger"] == []


@pytest.mark.parametrize("mode, value", [("url", URL), ("request_file", "captures/Exact Case.req")])
@pytest.mark.parametrize("scalar", [False, True])
def test_both_target_modes_preserve_exact_values_through_actual_execution(tmp_path, mode, value, scalar):
    impl = LLM(RETRIEVE, final(target_mode=mode, target=value if scalar else {mode: value}), returned())
    runner = Runner()
    app = build_phase1_graph(planner=planner(HeadLLM(observation())), feedback=reviewer(), implementor=compiler(impl, Retriever()),
                            run_manager=runner, action_filter=Approver(), analyzer=Analyzer(), reporter=Reporter()).compile()
    state = app.invoke({"session_id": "target", "user_goal": "Check", "target": {mode: value},
                        "output_dir": str(tmp_path), "max_replans": 0})
    assert runner.requests[0].target.model_dump(exclude_none=True) == {mode: value}
    assert state["working_memory"]["latest_result"]["request"]["target"][mode] == value
