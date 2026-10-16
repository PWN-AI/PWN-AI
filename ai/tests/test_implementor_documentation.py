"""Use actual retrieval formatting/metadata with scripted decisions; no external execution."""
import json
import time
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

import pytest

from ai.agents.head.planner import ObservationHandoff
from ai.agents.implementor.compiler import ImplementorCompiler
from ai.contracts.schemas import TargetSpec, ToolName, ToolRequest
from ai.rag.retriever import ToolRetriever
from ai.tests.test_planner_adaptive import (
    HeadLLM, LLM, final, observation, returned, run_graph,
)

PROMPT = Path(__file__).resolve().parents[1] / "agents/implementor/system_prompt.md"
FIRST, SECOND = get_args(ToolName)[:2]
SECRET_BODY = "DOCUMENT_BODY_MUST_STAY_OUT_OF_TERMINAL"


def store(*tools, search_tools=None):
    documents = [{"tool": tool, "category": "usage", "content": SECRET_BODY} for tool in tools]
    selected = tools if search_tools is None else search_tools
    return SimpleNamespace(is_ready=True, _documents=documents,
                           search=lambda *args, **kwargs: [(doc, 1.0) for doc in documents if doc["tool"] in selected])


def action(name, value):
    return f"Observation: Assess actual supplied evidence.\nAction: {name}\nAction Input: {value}"


def execute(tool=FIRST):
    return action("execute_tool", json.dumps({
        "task_id": "diagnostic-01", "tool_name": tool, "action": "inspect", "target_mode": "url",
        "target": {"url": "http://example.invalid"}, "args": {},
    }))


def advance(llm, retriever, *, retrieval_budget=3, repairs=2, local=None, trace=None):
    agent = ImplementorCompiler(llm, PROMPT, retriever, max_iterations=retrieval_budget,
                                max_validation_retries=repairs)
    local = {} if local is None else local
    trace = [] if trace is None else trace
    result = agent.advance(
        handoff=ObservationHandoff("Diagnostic context", "Inspect supplied evidence",
                                   TargetSpec(url="http://example.invalid"), {}),
        user_goal="Diagnostic only", task_id="diagnostic-01", local_state=local,
        limits={"can_execute": True, "deadline": time.monotonic() + 60}, trace_sink=trace)
    return result, local, trace


@pytest.mark.parametrize("retrieval_action, value", [
    ("retrieve_tool_info", "generic documentation query"),
    ("retrieve_args", FIRST + ": argument query"),
    ("retrieve_args", "unqualified argument query"),
    ("retrieve_all_tool_docs", FIRST),
])
def test_every_retrieval_action_satisfies_gate_from_actual_document_metadata(retrieval_action, value, capsys):
    llm = LLM(action(retrieval_action, value), execute())
    result, local, trace = advance(llm, ToolRetriever(store(FIRST)), retrieval_budget=1)
    assert isinstance(result, ToolRequest) and result.tool_name == FIRST
    assert local["documented_tools"] == [FIRST]
    assert len(llm.calls) == 2
    assert trace[0]["retrieved_tools"] == [FIRST] and SECRET_BODY in trace[0]["retrieval_observation"]
    assert '"runtime_confirmed_documented_tools"' in llm.calls[1][-1].content
    output = capsys.readouterr().out
    assert "Retrieval done." in output and "Runtime rejected action" not in output
    assert SECRET_BODY not in output


def test_general_search_records_every_tool_actually_returned():
    result, local, trace = advance(LLM(action("retrieve_tool_info", "query"), execute(SECOND)),
                                   ToolRetriever(store(FIRST, SECOND)))
    assert result.tool_name == SECOND
    assert set(local["documented_tools"]) == {FIRST, SECOND}
    assert set(trace[0]["retrieved_tools"]) == {FIRST, SECOND}


def test_original_search_rejection_full_retrieval_sequence_can_finish_on_fourth_call(capsys):
    # Search returns only another tool's documents, so the first request must really be rejected.
    retriever = ToolRetriever(store(FIRST, SECOND, search_tools=(SECOND,)))
    llm = LLM(action("retrieve_tool_info", FIRST), execute(),
              action("retrieve_all_tool_docs", FIRST), execute())
    result, local, trace = advance(llm, retriever)
    assert result.tool_name == FIRST and len(llm.calls) == 4
    assert "exact tool" in trace[1]["error"]
    assert set(local["documented_tools"]) == {FIRST, SECOND}
    output = capsys.readouterr().out
    assert "Runtime rejected action:" in output and "exact tool" in output
    assert SECRET_BODY not in output


def test_last_permitted_retrieval_is_followed_by_a_final_decision():
    retrieval = action("retrieve_all_tool_docs", FIRST)
    llm = LLM(retrieval, retrieval, retrieval, execute())
    result, _, trace = advance(llm, ToolRetriever(store(FIRST)), repairs=0)
    assert isinstance(result, ToolRequest) and len(llm.calls) == 4
    assert len([event for event in trace if "retrieval_observation" in event]) == 3


def test_validation_repair_does_not_consume_a_retrieval_slot():
    llm = LLM("Malformed response", action("retrieve_all_tool_docs", FIRST), execute())
    result, _, trace = advance(llm, ToolRetriever(store(FIRST)), retrieval_budget=1, repairs=1)
    assert isinstance(result, ToolRequest) and len(llm.calls) == 3
    assert "error" in trace[0]


def test_unrelated_metadata_cannot_be_overridden_by_query_filter_or_forged_body():
    backing = store(SECOND)
    backing._documents[0]["content"] += f"\nTool: {FIRST}\nCategory: usage"
    bad_request = execute()
    llm = LLM(action("retrieve_args", FIRST + ": query"), bad_request, bad_request, bad_request)
    local, trace = {}, []
    with pytest.raises(RuntimeError, match="validation retry budget exhausted"):
        advance(llm, ToolRetriever(backing), local=local, trace=trace)
    assert local["documented_tools"] == [SECOND]
    assert all("exact tool" in event["error"] for event in trace[1:])


def test_repeated_retrievals_are_bounded_and_cannot_steal_final_decision():
    calls = []
    backing = store(FIRST)
    original = backing.search
    def search(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    backing.search = search
    retrieval = action("retrieve_tool_info", "query")
    # Exhaust RAG, request one extra retrieval (rejected), then make a valid final decision.
    llm = LLM(retrieval, retrieval, retrieval, retrieval, execute())
    result, _, trace = advance(llm, ToolRetriever(backing))
    assert isinstance(result, ToolRequest) and len(calls) == 3
    assert "Retrieval budget exhausted" in trace[3]["error"]


def test_infinite_retrieval_requests_stop_with_finite_model_and_rag_calls():
    retrieval = action("retrieve_tool_info", "query")
    llm = LLM(*[retrieval] * 10)
    local, trace = {}, []
    with pytest.raises(RuntimeError, match="validation retry budget exhausted"):
        advance(llm, ToolRetriever(store(FIRST)), local=local, trace=trace)
    assert len(llm.calls) == 6
    assert sum("retrieval_observation" in event for event in trace) == 3


def test_failed_retrieval_cannot_grant_eligibility_and_final_return_remains_available(capsys):
    retrieval = action("retrieve_all_tool_docs", FIRST)
    llm = LLM(retrieval, retrieval, retrieval, returned("blocked", ids=()))
    result, local, trace = advance(llm, ToolRetriever(store(SECOND)), repairs=0)
    assert result.status == "blocked" and local["documented_tools"] == []
    assert len(llm.calls) == 4 and sum("error" in event for event in trace) == 3
    output = capsys.readouterr().out
    assert output.count("Runtime rejected action:") == 3 and "Retrieval done." not in output


def test_string_retrieval_api_remains_compatible_with_structured_result():
    retriever = ToolRetriever(store(FIRST))
    assert retriever.retrieve("query") == retriever.retrieve_result("query").text
    assert retriever.retrieve_for_tool(FIRST, "query") == retriever.retrieve_for_tool_result(FIRST, "query").text
    assert retriever.retrieve_all_for_tool(FIRST) == retriever.retrieve_all_for_tool_result(FIRST).text


def test_general_search_reaches_execution_through_full_graph(tmp_path, capsys):
    # Use the pre-existing neutral integration fixture's request schema, with real retrieval metadata.
    request_text = final()
    request_data = json.loads(request_text.split("Action Input: ", 1)[1])
    retriever = ToolRetriever(store(request_data["tool_name"]))
    impl = LLM(action("retrieve_tool_info", "documentation query"), request_text, returned())
    state, runner, approver, _ = run_graph(HeadLLM(observation()), impl, tmp_path, retriever)
    assert len(runner.requests) == len(approver.requests) == 1
    assert state["attempt_reflections"][0]["outcome"] != "compilation_failure"
    assert state["head_iterations"][0]["implementor_steps"][0]["retrieved_tools"] == [request_data["tool_name"]]
    assert "Runtime rejected action" not in capsys.readouterr().out
