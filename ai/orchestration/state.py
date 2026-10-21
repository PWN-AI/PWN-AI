from __future__ import annotations

from typing import Any, Dict, List, Optional, TypedDict

from ai.contracts.schemas import ActionDecision, ExecutionResult, Finding, TaskRunRecord, TaskSpec, ToolRequest


class SessionState(TypedDict, total=False):
    session_id: str
    user_goal: str
    target: Dict[str, Any]
    operator_context: Dict[str, Any]
    output_dir: str

    max_tasks: int
    max_replans: int
    max_new_tasks_per_replan: int

    tasks: List[TaskSpec]
    task_index: int
    current_task: Optional[TaskSpec]
    current_request: Optional[ToolRequest]
    current_decision: Optional[ActionDecision]
    current_result: Optional[ExecutionResult]

    runs: List[TaskRunRecord]
    findings: List[Finding]
    notes: List[str]
    llm_notes: List[str]        # observations written by the LLM after each task
    planning_trace: List[str]
    replan_count: int
    stop_reason: str
    finished: bool
    report_path: str
