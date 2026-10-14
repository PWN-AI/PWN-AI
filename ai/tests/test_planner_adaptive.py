import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from ai.agents.head.planner import HeadPlanner
from ai.agents.implementor.compiler import ImplementorCompiler
from ai.agents.react_agent import run_react_loop
from ai.contracts.schemas import ActionDecision, ExecutionResult
from ai.orchestration.langgraph_flow import build_phase1_graph

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
        return SimpleNamespace(content=response)


class Retriever:
    def __init__(self):
        self.queries = []

    def retrieve_all_for_tool(self, tool):
        self.queries.append(tool)
        return "REAL DOCUMENTATION: nuclei_xss supports scan with tags."


def observation(note="private memory", decision="continue", next_step="Check the observed page for XSS"):
    return (f"Observation: Observed endpoint {URL}; auth unverified.\n"
            "Observation: Public assessment retained only by the head; inspect remaining gaps.\n"
            f"Summary: Endpoint {URL}; auth unverified; check XSS using documented arguments.\n"
            f"Next step: {next_step}\nMemory note: {note}\nDecision: {decision}")


def planner(llm):
    return HeadPlanner(llm, ROOT / "agents/head/system_prompt.md")


def compiler(llm, retriever):
    return ImplementorCompiler(llm, ROOT / "agents/implementor/system_prompt.md", retriever)


def final(task_id="task-01", tags="xss"):
    return "Observation: Actual documentation supports this request.\nAction: final_answer\nAction Input: " + json.dumps({
        "task_id": task_id, "tool_name": "nuclei_xss", "action": "scan",
        "target_mode": "url", "target": {"url": URL}, "args": {"tags": tags},
        "reasoning": "Use documented scanning arguments.",
    })


RETRIEVE = "Observation: Need scan docs.\nAction: retrieve_all_tool_docs\nAction Input: nuclei_xss"


@pytest.mark.parametrize("target_mode, target_value", [("url", URL), ("request_file", "capture.req")])
@pytest.mark.parametrize("string_target", [True, False])
def test_implementor_normalizes_target_format(target_mode, target_value, string_target):
    from ai.agents.head.planner import ObservationHandoff
    from ai.contracts.schemas import TargetSpec

    target = {target_mode: target_value}
    handoff = ObservationHandoff("Observed target", "Scan the observed target", TargetSpec(**target), {})
    response = "Observation: Actual documentation supports this request.\nAction: final_answer\nAction Input: " + json.dumps({
        "task_id": "task-01", "tool_name": "nuclei_xss", "action": "scan",
        "target_mode": target_mode, "target": target_value if string_target else target,
        "args": {"tags": "xss"},
    })
    request = compiler(LLM(RETRIEVE, response), Retriever()).compile(
        handoff=handoff, user_goal="Check target", task_id="task-01")
    assert request.target.model_dump(exclude_none=True) == target
    assert request.target_mode == target_mode
    assert request.args == {"tags": "xss"}


@pytest.mark.parametrize("responses", [(RuntimeError("offline"),), ('{"tasks": []}', '{"tasks": []}'),
                                         (observation().replace("Check the observed page for XSS", '{"tool": "x"}'),) * 2])
def test_head_never_converts_failed_or_json_output_into_actions(responses):
    from ai.contracts.schemas import TargetSpec
    decision = planner(LLM(*responses)).plan(user_goal="Check XSS", target=TargetSpec(url=URL), operator_context={})
    assert decision.should_stop
    assert decision.handoff is None


def test_head_corrects_invalid_format_and_preserves_context():
    from ai.contracts.schemas import TargetSpec
    head = planner(LLM('{"tasks": []}', observation()))
    decision = head.plan(user_goal="Check XSS", target=TargetSpec(url=URL), operator_context={
        "auth_cookie": "session=current", "auth_verified": True,
        "discovered_endpoints": [URL], "password": "secret", "llm_notes": ["not forwarded"],
    })
    assert not hasattr(head, "retriever")
    assert not hasattr(decision, "tasks")
    assert decision.handoff.target.url == URL
    assert decision.handoff.context["auth_cookie"] == "session=current"
    assert decision.handoff.context["discovered_endpoints"] == [URL]
    assert "password" not in decision.handoff.context
    assert "llm_notes" not in decision.handoff.context


def test_real_retrieval_observation_precedes_final_request():
    llm = LLM(RETRIEVE + "\nObservation: FAKE DOCS\n" + final(tags="fabricated"), final())
    retriever = Retriever()
    result = run_react_loop(llm=llm, system_prompt="", user_message="", retriever=retriever)
    assert result.total_llm_calls == 2
    assert retriever.queries == ["nuclei_xss"]
    continuation = "\n".join(message.content for message in llm.calls[1])
    assert "REAL DOCUMENTATION" in continuation
    assert "FAKE DOCS" not in continuation
    assert "fabricated" not in result.final_output


def test_final_answer_without_retrieval_is_rejected():
    with pytest.raises(RuntimeError, match="grounded request"):
        run_react_loop(llm=LLM(final(), final(), final()), system_prompt="", user_message="",
                       retriever=Retriever())


@pytest.mark.parametrize("with_summary", [False, True])
def test_head_requires_separate_observations_and_handoff_summary(with_summary):
    from ai.contracts.schemas import TargetSpec
    legacy = f"Observation: {URL}\nNext step: Scan\nMemory note: Keep\nDecision: continue"
    if with_summary:
        legacy = legacy.replace("\nNext step:", "\nSummary: Scan the observed endpoint\nNext step:")
    decision = planner(LLM(legacy, legacy)).plan(
        user_goal="Check XSS", target=TargetSpec(url=URL), operator_context={})
    assert decision.should_stop
    assert decision.handoff is None


def test_implementor_can_assess_multiple_real_retrievals_before_tool_request(capsys):
    trace = []
    llm = LLM(RETRIEVE, RETRIEVE.replace("Need scan docs.", "Need more argument documentation."), final())
    result = run_react_loop(llm=llm, system_prompt="", user_message="", retriever=Retriever(),
                            trace_sink=trace)
    assert result.total_llm_calls == 3
    assert [event["action"] for event in trace] == [
        "retrieve_all_tool_docs", "retrieve_all_tool_docs", "final_answer"]
    assert all(event["retrieval_observation"].startswith("REAL DOCUMENTATION") for event in trace[:2])
    assert trace[1]["assessment"] == "Need more argument documentation."
    assert "REAL DOCUMENTATION" in llm.calls[2][-1].content
    output = capsys.readouterr().out
    assert "REAL DOCUMENTATION" not in output
    assert "Action: retrieve_all_tool_docs" in output
    assert "Retrieving all docs for: nuclei_xss" in output
    assert output.count("Retrieval done.") == 2


@pytest.mark.parametrize("retriever", [None, Retriever()])
def test_implementor_never_guesses_on_unavailable_or_empty_rag(retriever):
    from ai.contracts.schemas import TargetSpec
    handoff = planner(LLM(observation())).plan(user_goal="Check XSS", target=TargetSpec(url=URL), operator_context={}).handoff
    if retriever:
        retriever.retrieve_all_for_tool = lambda _tool: ""
    with pytest.raises(RuntimeError):
        compiler(LLM(RETRIEVE), retriever).compile(handoff=handoff, user_goal="Check XSS", task_id="task-01")


class Approver:
    def __init__(self):
        self.requests = []

    def review(self, request):
        self.requests.append(request)
        return ActionDecision(approved=True)


class Runner:
    def __init__(self):
        self.requests = []

    def execute(self, request):
        self.requests.append(request)
        return ExecutionResult(tool_name=request.tool_name, action=request.action, success=True,
                               returncode=0, stdout="execution evidence")


class Analyzer:
    def analyze(self, request, result):
        return []


class Reporter:
    def write_markdown_report(self, **kwargs):
        self.report = kwargs
        return Path("report.md")


def run_graph(head_llm, impl_llm, tmp_path, retriever=None):
    runner, approver, reporter = Runner(), Approver(), Reporter()
    graph = build_phase1_graph(planner=planner(head_llm), implementor=compiler(impl_llm, retriever),
                              action_filter=approver, run_manager=runner, analyzer=Analyzer(), reporter=reporter).compile()
    state = graph.invoke({"session_id": "test", "user_goal": "Check XSS", "target": {"url": URL},
                          "operator_context": {"discovered_endpoints": [URL], "password": "secret"},
                          "output_dir": str(tmp_path), "max_replans": 4})
    return state, runner, approver, reporter


def test_graph_head_only_memory_and_stateless_implementor(tmp_path):
    head = LLM(observation("initial private memory"), observation("second private memory", next_step="Check a second template"),
               observation("finished memory", "stop", "Goal satisfied"))
    impl = LLM(RETRIEVE, final(), RETRIEVE, final("task-02", "other"))
    state, runner, approver, reporter = run_graph(head, impl, tmp_path, Retriever())
    assert len(runner.requests) == len(approver.requests) == 2
    assert state["stop_reason"] == "Goal satisfied"
    assert state["llm_notes"] == ["initial private memory", "second private memory", "finished memory"]
    assert len(state["head_iterations"]) == 3
    assert state["head_iterations"][0]["observations"] == [
        f"Observed endpoint {URL}; auth unverified.",
        "Public assessment retained only by the head; inspect remaining gaps.",
    ]
    assert state["head_iterations"][0]["implementor_steps"][0]["retrieval_observation"].startswith("REAL DOCUMENTATION")
    final_head_input = json.loads(head.calls[2][1].content)["head_memory_and_evidence"]
    assert len(final_head_input["execution_history"]) == 2
    assert [run["request"]["task_id"] for run in final_head_input["execution_history"]] == ["task-01", "task-02"]
    assert final_head_input["execution_history"][0]["result"]["stdout"] == "execution evidence"
    assert final_head_input["iteration_history"] == state["head_iterations"][:2]
    assert "initial private memory" in head.calls[1][1].content
    assert "execution evidence" in head.calls[1][1].content
    assert "second private memory" in head.calls[2][1].content
    for index in (0, 2):
        assert len(impl.calls[index]) == 2  # Fresh system + current handoff.
        user_input = json.loads(impl.calls[index][1].content)
        assert user_input["head_handoff"]["target"] == {"url": URL}
        assert user_input["head_handoff"]["context"]["discovered_endpoints"] == [URL]
        assert "private memory" not in impl.calls[index][1].content
        assert "execution evidence" not in impl.calls[index][1].content
        assert "Public assessment retained only by the head" not in impl.calls[index][1].content
        assert "observation" not in user_input["head_handoff"]
        assert user_input["head_handoff"]["observation_summary"].startswith("Endpoint")
    assert reporter.report["stop_reason"] == "Goal satisfied"


def test_graph_stops_and_reports_when_rag_is_missing(tmp_path):
    state, runner, approver, reporter = run_graph(LLM(observation()), LLM(), tmp_path)
    assert not runner.requests
    assert not approver.requests
    assert "Implementor could not produce" in state["stop_reason"]
    assert reporter.report["stop_reason"] == state["stop_reason"]
    assert state["head_iterations"][0]["compilation_error"] == state["stop_reason"]


def test_graph_retains_failed_rag_action_without_executing(tmp_path):
    retriever = Retriever()
    retriever.retrieve_all_for_tool = lambda _tool: ""
    state, runner, approver, _ = run_graph(LLM(observation()), LLM(RETRIEVE), tmp_path, retriever)
    assert not runner.requests
    assert not approver.requests
    step = state["head_iterations"][0]["implementor_steps"][0]
    assert step["action"] == "retrieve_all_tool_docs"
    assert step["error"] == "RAG returned no documentation."


def test_graph_executes_implementor_request_with_string_url_target(tmp_path):
    response = final().replace(json.dumps({"url": URL}), json.dumps(URL))
    state, runner, approver, _ = run_graph(
        LLM(observation(), observation(decision="stop", next_step="Done")),
        LLM(RETRIEVE, response), tmp_path, Retriever())
    assert len(runner.requests) == len(approver.requests) == 1
    assert runner.requests[0].target.url == URL
    assert state["stop_reason"] == "Done"


def test_head_stop_never_calls_implementor(tmp_path):
    impl = LLM()
    state, runner, _, _ = run_graph(LLM(observation(decision="stop", next_step="Missing required input")), impl, tmp_path)
    assert not impl.calls
    assert not runner.requests
    assert state["stop_reason"] == "Missing required input"


def test_request_file_handoff_and_cookie_survive_compilation(tmp_path):
    from ai.contracts.schemas import TargetSpec
    from ai.agents.head.planner import ObservationHandoff
    handoff = ObservationHandoff("Captured request", "Scan the captured request", TargetSpec(request_file="capture.req"),
                                 {"auth_cookie": "session=current", "auth_verified": True})
    response = "Observation: Real docs support request-file mode.\nAction: final_answer\nAction Input: " + json.dumps({
        "task_id": "task-01", "tool_name": "nuclei_xss", "action": "scan",
        "target_mode": "request_file", "target": {"request_file": "capture.req"}, "args": {},
    })
    impl = LLM(RETRIEVE, response)
    request = compiler(impl, Retriever()).compile(handoff=handoff, user_goal="Check request", task_id="task-01")
    assert request.target.request_file == "capture.req"
    assert "session=current" in impl.calls[0][1].content
    assert "capture.req" in impl.calls[0][1].content


def test_graph_injects_current_cookie_and_keeps_final_state_serializable(tmp_path, monkeypatch):
    import ai.orchestration.langgraph_flow as flow
    monkeypatch.setattr(flow, "refresh_session_cookie", lambda **kwargs: "session=fresh")
    runner, approver = Runner(), Approver()
    graph = build_phase1_graph(planner=planner(LLM(observation(), observation(decision="stop", next_step="Done"))),
                              implementor=compiler(LLM(RETRIEVE, final()), Retriever()),
                              action_filter=approver, run_manager=runner, analyzer=Analyzer(), reporter=Reporter()).compile()
    state = graph.invoke({"session_id": "test", "user_goal": "Check XSS", "target": {"url": URL},
                          "operator_context": {"auth_cookie": "session=current"}, "output_dir": str(tmp_path)})
    assert approver.requests[0].args["cookie"] == "session=current"
    assert runner.requests[0].args["cookie"] == "session=fresh"
    assert state["operator_context"]["auth_cookie"] == "session=fresh"
    # Mirror --print-final-state's conversion, including prepared request mappings.
    printable = {}
    for key, value in state.items():
        if isinstance(value, list):
            printable[key] = [item.model_dump() if hasattr(item, "model_dump") else str(item) for item in value]
        elif hasattr(value, "model_dump"):
            printable[key] = value.model_dump()
        else:
            printable[key] = value
    json.dumps(printable)


def test_missing_documentation_cannot_count_as_successful_retrieval():
    from ai.rag.retriever import ToolRetriever
    unready = ToolRetriever(SimpleNamespace(is_ready=False))
    with pytest.raises(RuntimeError, match="not initialized"):
        unready.retrieve("tool docs")
    ready_empty = ToolRetriever(SimpleNamespace(is_ready=True, search=lambda *args, **kwargs: [], _documents=[]))
    with pytest.raises(LookupError):
        ready_empty.retrieve("tool docs")
    with pytest.raises(LookupError):
        ready_empty.retrieve_all_for_tool("nuclei_xss")


def test_invalid_implementor_request_stops_before_approval(tmp_path):
    response = final().replace('"target_mode": "url"', '"target_mode": "request_file"')
    state, runner, approver, reporter = run_graph(LLM(observation()), LLM(RETRIEVE, response), tmp_path, Retriever())
    assert not runner.requests
    assert not approver.requests
    assert "Implementor could not produce" in state["stop_reason"]
    assert reporter.report["stop_reason"] == state["stop_reason"]
