from __future__ import annotations

from pathlib import Path

from ai.contracts.schemas import ToolName
from ai.integrations.base import ToolAdapter
from ai.integrations.brute_force_adapter import BruteForceAdapter
from ai.integrations.command_injection_adapter import CommandInjectionAdapter
from ai.integrations.nuclei_adapter import NucleiXssAdapter
from ai.integrations.sql_injection_adapter import SqlInjectionAdapter


class ToolRegistry:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self._adapters: dict[ToolName, ToolAdapter] = {
            "brute_force": BruteForceAdapter(project_root),
            "command_injection": CommandInjectionAdapter(project_root),
            "sql_injection": SqlInjectionAdapter(project_root),
            "nuclei_xss": NucleiXssAdapter(project_root),
        }

    def get(self, tool_name: ToolName) -> ToolAdapter:
        return self._adapters[tool_name]

