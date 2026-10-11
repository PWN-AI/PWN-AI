"""Full bounded LangGraph flows without external model or scanner calls."""
from types import SimpleNamespace
from pathlib import Path

import pytest

from ai.agents.head_qwen3_8b.planner import HeadPlanner
from ai.analysis.result_analyzer import ResultAnalyzer
from ai.contracts.schemas import ExecutionResult, TargetSpec, TaskSpec, ToolRequest
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.reporting.report_generator import ReportGenerator
from ai.safety.action_filter import ActionFilter


def task(index):
    return TaskSpec(
        id=f"task-{index}", title=f"Local test {index}", objective="Verify orchestration",
        tool_hint="sql_injection", target_mode="url",
        target=TargetSpec(url="http://127.0.0.1/test"), args={"index": index},
    )


@pytest.mark.parametrize("approved", [True, False])
def test_eight_replans_finish_and_write_report(tmp_path, approved):
    count = 0

    def replan(**kwargs):
        nonlocal count
        count += 1
        return HeadPlanner.PlannerDecision(tasks=[task(count)], thinking_summary=["Continue"])

    def compile_request(task, **kwargs):
        return ToolRequest(
            task_id=task.id, tool_name=task.tool_hint, action="scan", target_mode=task.target_mode,
            target=task.target, args=task.args,
        )

    executions = []

    def execute(request):
        executions.append(request)
        return ExecutionResult(
            tool_name=request.tool_name, action=request.action, success=True, returncode=0,
            stdout="sqlmap identified the following injection point(s)",
        )

    graph = build_phase1_graph(
        planner=SimpleNamespace(
            plan=lambda **kwargs: HeadPlanner.PlannerDecision(tasks=[task(0)], thinking_summary=["Start"]),
            replan=replan,
        ),
        implementor=SimpleNamespace(compile=compile_request),
        action_filter=ActionFilter(ask_permission=True, input_func=lambda prompt: "y" if approved else "n"),
        run_manager=SimpleNamespace(execute=execute), analyzer=ResultAnalyzer(), reporter=ReportGenerator(),
    ).compile()
    state = graph.invoke({
        "session_id": "test-replanning", "user_goal": "test", "target": {"url": "http://127.0.0.1/test"},
        "operator_context": {}, "output_dir": str(tmp_path), "max_tasks": 1, "max_replans": 8,
    })
    assert len(state["runs"]) == 9
    assert len(executions) == (9 if approved else 0)
    assert all(run.status == ("executed" if approved else "denied") for run in state["runs"])
    assert state["replan_count"] == 8
    assert Path(state["report_path"]).is_file()
    assert "Task Execution Log" in Path(state["report_path"]).read_text(encoding="utf-8")
