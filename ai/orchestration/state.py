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

    prepared_requests: Dict[str, Dict[str, Any]]
    tasks: List[TaskSpec]
    current_task: Optional[TaskSpec]
    current_request: Optional[ToolRequest]
    current_decision: Optional[ActionDecision]
    current_result: Optional[ExecutionResult]

    runs: List[TaskRunRecord]
    findings: List[Finding]
    notes: List[str]
    planning_trace: List[str]
    head_iterations: List[Dict[str, Any]]  # Current-session planning, execution, retrieval, and feedback archive.
    attempt_reflections: List[Dict[str, Any]]
    session_reflection: Dict[str, Any]
    session_feedback_steps: List[Dict[str, Any]]
    working_memory: Dict[str, Any]  # Current-session facts/ledger/feedback; no cross-session recall.
    replan_count: int
    stop_reason: str
    finished: bool
    report_path: str
    max_actions_per_objective: int
    max_consecutive_failures: int
    objective_timeout_seconds: float
    next_task_number: int
    objective_id: str
    objective_start_run: int
    objective_start_attempt: int
    objective_started_at: float
    active_handoff: Dict[str, Any]
    implementor_state: Dict[str, Any]
    objective_report: Dict[str, Any]
    objective_feedback: List[Dict[str, Any]]
    attempt_records: List[Dict[str, Any]]
    return_reason: str
    consecutive_failures: int
    result_fingerprints: List[str]
    consecutive_unchanged_results: int
