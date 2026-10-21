from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator


ToolName = Literal["brute_force", "command_injection", "sql_injection", "nuclei_xss"]
TargetMode = Literal["url", "request_file"]
TaskStatus = Literal["pending", "approved", "denied", "executed", "failed", "skipped"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: Optional[str] = None
    request_file: Optional[str] = None

    @model_validator(mode="after")
    def _validate_one_target(self) -> "TargetSpec":
        has_url = bool(self.url)
        has_req = bool(self.request_file)
        if has_url == has_req:
            raise ValueError("Provide exactly one target field: url or request_file")
        return self


class TaskSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    objective: str
    rationale: str = ""
    priority: int = Field(default=1, ge=1, le=10)
    tool_hint: ToolName
    action_hint: str = "scan"
    target_mode: TargetMode
    target: TargetSpec
    args: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_target_mode(self) -> "TaskSpec":
        if self.target_mode == "url" and not self.target.url:
            raise ValueError("target_mode=url requires target.url")
        if self.target_mode == "request_file" and not self.target.request_file:
            raise ValueError("target_mode=request_file requires target.request_file")
        return self


class ToolRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: str
    tool_name: ToolName
    action: str
    target_mode: TargetMode
    target: TargetSpec
    args: Dict[str, Any] = Field(default_factory=dict)
    reasoning: str = ""


    @model_validator(mode="after")
    def _validate_target_mode(self) -> "ToolRequest":
        if self.target_mode == "url" and not self.target.url:
            raise ValueError("target_mode=url requires target.url")
        if self.target_mode == "request_file" and not self.target.request_file:
            raise ValueError("target_mode=request_file requires target.request_file")
        return self


class ExecutionResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tool_name: ToolName
    action: str
    success: bool
    returncode: int
    stdout: str = ""
    stderr: str = ""
    raw_output: Dict[str, Any] = Field(default_factory=dict)
    artifacts: Dict[str, str] = Field(default_factory=dict)
    error: Optional[str] = None
    started_at: str = Field(default_factory=utc_now_iso)
    finished_at: str = Field(default_factory=utc_now_iso)
    duration_seconds: float = 0.0


class ActionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    reason: str = ""
    decided_at: str = Field(default_factory=utc_now_iso)


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_id: str
    tool_name: ToolName
    title: str
    severity: Literal["info", "low", "medium", "high", "critical"] = "info"
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    evidence: str = ""
    references: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=utc_now_iso)


class TaskRunRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task: TaskSpec
    request: ToolRequest
    decision: ActionDecision
    result: ExecutionResult
    status: TaskStatus
    created_at: str = Field(default_factory=utc_now_iso)

