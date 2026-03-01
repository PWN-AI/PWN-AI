from pathlib import Path

from ai.agents.head_qwen3_8b.planner import HeadPlanner
from ai.contracts.schemas import ExecutionResult, TargetSpec, TaskSpec


class _FailingLLM:
    def invoke(self, _messages):  # pragma: no cover - deterministic fallback trigger
        raise RuntimeError("LLM unavailable")


def _planner() -> HeadPlanner:
    prompt_path = Path(__file__).resolve().parents[1] / "agents" / "head_qwen3_8b" / "system_prompt.md"
    return HeadPlanner(llm=_FailingLLM(), prompt_path=prompt_path)


def test_dump_goal_fallback_plan_builds_sql_chain():
    planner = _planner()
    decision = planner.plan(
        user_goal="Dump the database after identifying SQL injection",
        target=TargetSpec.model_validate({"url": "http://example.com"}),
        operator_context={},
        max_tasks=6,
    )
    actions = [task.action_hint for task in decision.tasks if task.tool_hint == "sql_injection"]
    assert actions[:3] == ["scan", "enumerate_dbs", "dump"]


def test_replan_adds_enumerate_and_dump_after_positive_scan_signal():
    planner = _planner()
    current_task = TaskSpec.model_validate(
        {
            "id": "task-01",
            "title": "Baseline SQL scan",
            "objective": "Detect SQLi",
            "rationale": "start",
            "priority": 1,
            "tool_hint": "sql_injection",
            "action_hint": "scan",
            "target_mode": "url",
            "target": {"url": "http://example.com"},
            "args": {},
        }
    )
    current_result = ExecutionResult.model_validate(
        {
            "tool_name": "sql_injection",
            "action": "scan",
            "success": True,
            "returncode": 0,
            "stdout": "parameter 'id' appears to be injectable and backend database is MySQL",
            "stderr": "",
            "raw_output": {},
            "artifacts": {},
            "duration_seconds": 1.0,
        }
    )
    decision = planner.replan(
        user_goal="Dump database records",
        target=TargetSpec.model_validate({"url": "http://example.com"}),
        operator_context={},
        current_task=current_task,
        current_result=current_result,
        findings=[],
        remaining_tasks=[],
        max_new_tasks=3,
    )
    actions = [task.action_hint for task in decision.tasks]
    assert "enumerate_dbs" in actions
    assert "dump" in actions
