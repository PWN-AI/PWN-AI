from pathlib import Path

from ai.agents.head_qwen3_8b.planner import HeadPlanner
from ai.contracts.schemas import ExecutionResult, TargetSpec, TaskSpec


class _FailingLLM:
    def invoke(self, _messages):  # pragma: no cover - deterministic fallback trigger
        raise RuntimeError("LLM unavailable")


def _planner() -> HeadPlanner:
    prompt_path = Path(__file__).resolve().parents[1] / "agents" / "head_qwen3_8b" / "system_prompt.md"
    return HeadPlanner(llm=_FailingLLM(), prompt_path=prompt_path)


def test_fallback_plan_produces_baseline_scan():
    """When the LLM is unavailable the planner must still produce a
    minimal fallback plan with at least one baseline scan task."""
    planner = _planner()
    decision = planner.plan(
        user_goal="Dump the database after identifying SQL injection",
        target=TargetSpec.model_validate({"url": "http://example.com"}),
        operator_context={},
        max_tasks=6,
    )
    assert len(decision.tasks) >= 1
    first = decision.tasks[0]
    assert first.tool_hint == "sql_injection"
    assert first.action_hint == "scan"
    assert decision.should_stop is False


def test_replan_returns_valid_decision_when_llm_fails():
    """When the LLM is unavailable the replan must still return a
    valid PlannerDecision (stop or continue) without crashing."""
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
    # Decision should be valid (may have tasks, stop, or empty-continue)
    assert isinstance(decision, HeadPlanner.PlannerDecision)
    # Replan fallback returns empty tasks + should_stop=False meaning
    # "continue with remaining queue" — this is valid graceful degradation
    assert isinstance(decision.tasks, list)
    assert isinstance(decision.should_stop, bool)
