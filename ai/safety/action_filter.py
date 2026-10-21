from __future__ import annotations

from typing import Callable

from ai.contracts.schemas import ActionDecision, ToolRequest


class ActionFilter:
    """
    Phase 1 behavior:
    - asks for operator permission before each tool action
    - does not hard-block with static policy logic yet
    """

    def __init__(
        self,
        *,
        ask_permission: bool = True,
        auto_approve: bool = False,
        input_func: Callable[[str], str] = input,
    ):
        self.ask_permission = ask_permission
        self.auto_approve = auto_approve
        self.input_func = input_func

    def review(self, request: ToolRequest) -> ActionDecision:
        if self.auto_approve:
            return ActionDecision(approved=True, reason="Auto-approved by runtime configuration.")

        if not self.ask_permission:
            return ActionDecision(approved=True, reason="Permission prompt disabled by runtime configuration.")

        summary = (
            f"\n[Action Review]\n"
            f"- task_id: {request.task_id}\n"
            f"- tool: {request.tool_name}\n"
            f"- action: {request.action}\n"
            f"- target_mode: {request.target_mode}\n"
            f"- target: {request.target.model_dump()}\n"
            f"- args: {request.args}\n"
            f"- reasoning: {request.reasoning}\n"
        )
        print(summary)
        answer = self.input_func("Approve this action? [y/N]: ").strip().lower()
        approved = answer in {"y", "yes"}
        reason = "Approved by operator." if approved else "Denied by operator."
        return ActionDecision(approved=approved, reason=reason)

