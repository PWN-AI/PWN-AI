from __future__ import annotations

from pathlib import Path

from ai.contracts.schemas import ExecutionResult, ToolRequest
from ai.integrations.tool_registry import ToolRegistry


class RunManager:
    def __init__(self, project_root: Path):
        self.registry = ToolRegistry(project_root)

    def execute(self, request: ToolRequest) -> ExecutionResult:
        adapter = self.registry.get(request.tool_name)
        return adapter.execute(request)

