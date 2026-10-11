from types import SimpleNamespace

from ai.agents.react_agent import run_react_loop


class Retriever:
    def retrieve(self, query, top_k):
        return "ACTUAL DATABASE DOCUMENTATION"


def test_generated_observations_are_replaced_with_actual_retrieval():
    calls = []

    def invoke(messages):
        calls.append(messages)
        if len(calls) == 1:
            return SimpleNamespace(content=(
                'Action: retrieve_tool_info\nAction Input: sqlmap\n'
                'Observation: Invented documentation\nAction: final_answer\nAction Input: {"draft": true}'
            ))
        assert "ACTUAL DATABASE DOCUMENTATION" in messages[-1].content
        return SimpleNamespace(content='Action: final_answer\nAction Input: {"grounded": true}')

    result = run_react_loop(llm=SimpleNamespace(invoke=invoke), system_prompt="test",
                            user_message="test", retriever=Retriever())
    assert result.final_output == '{"grounded": true}'
    assert result.total_llm_calls == 2


def test_direct_final_answer_needs_no_continuation():
    llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(
        content='Action: final_answer\nAction Input: {"ok": true}'))
    result = run_react_loop(llm=llm, system_prompt="", user_message="", retriever=Retriever())
    assert result.total_llm_calls == 1
    assert result.final_output == '{"ok": true}'


def test_new_retrieval_in_final_round_does_not_accept_a_fabricated_draft():
    outputs = iter([
        "Action: retrieve_tool_info\nAction Input: sqlmap",
        'Action: retrieve_tool_info\nAction Input: other flags\n'
        'Observation: invented facts\nAction: final_answer\nAction Input: {"ungrounded": true}',
    ])
    llm = SimpleNamespace(invoke=lambda messages: SimpleNamespace(content=next(outputs)))
    result = run_react_loop(llm=llm, system_prompt="", user_message="", retriever=Retriever())
    assert result.total_llm_calls == 2
    assert result.final_output == "{}"


def test_plain_json_is_grounded_by_initial_retrieval():
    def invoke(messages):
        assert "ACTUAL DATABASE DOCUMENTATION" in messages[0].content
        return SimpleNamespace(content='{"grounded": true}')
    result = run_react_loop(llm=SimpleNamespace(invoke=invoke), system_prompt="",
                            user_message="", retriever=Retriever(), initial_query="sqlmap")
    assert result.final_output == '{"grounded": true}'
    assert result.retrieved_context == ["ACTUAL DATABASE DOCUMENTATION"]


def test_compiler_initial_retrieval_uses_the_selected_tool():
    class ToolRetriever:
        def retrieve_for_tool(self, tool, query, top_k):
            assert tool == "sql_injection" and query == "arguments"
            return "SELECTED TOOL DOCUMENTATION"
    def invoke(messages):
        assert "SELECTED TOOL DOCUMENTATION" in messages[0].content
        return SimpleNamespace(content='{"ok": true}')
    result = run_react_loop(
        llm=SimpleNamespace(invoke=invoke), system_prompt="", user_message="",
        retriever=ToolRetriever(), initial_query="arguments", initial_tool_filter="sql_injection",
    )
    assert result.retrieved_context == ["SELECTED TOOL DOCUMENTATION"]
