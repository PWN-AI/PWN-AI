from __future__ import annotations

import importlib.util
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict

from ai.contracts.schemas import ExecutionResult, ToolRequest


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(dt: datetime) -> str:
    return dt.isoformat()


def load_module_from_file(module_name: str, file_path: Path) -> Any:
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module spec from {file_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ToolAdapter(ABC):
    def __init__(self, project_root: Path):
        self.project_root = project_root

    @abstractmethod
    def execute(self, request: ToolRequest) -> ExecutionResult:
        raise NotImplementedError

    def _build_result(
        self,
        *,
        request: ToolRequest,
        started_at: datetime,
        success: bool,
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        raw_output: Dict[str, Any] | None = None,
        artifacts: Dict[str, str] | None = None,
        error: str | None = None,
    ) -> ExecutionResult:
        finished_at = utc_now()
        duration = (finished_at - started_at).total_seconds()
        return ExecutionResult(
            tool_name=request.tool_name,
            action=request.action,
            success=success,
            returncode=returncode,
            stdout=stdout,
            stderr=stderr,
            raw_output=raw_output or {},
            artifacts=artifacts or {},
            error=error,
            started_at=to_iso(started_at),
            finished_at=to_iso(finished_at),
            duration_seconds=duration,
        )

