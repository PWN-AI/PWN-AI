"""Public planning records. These describe evidence and decisions, not private reasoning."""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from ai.contracts.records import Record


class ObjectivePlan(Record):
    decision: Literal["continue", "stop"]
    objective: str = ""
    observation_summary: str = ""
    evidence_refs: list[str] = Field(default_factory=list)
    verified_facts: list[str] = Field(default_factory=list)
    hypotheses: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    return_conditions: list[str] = Field(default_factory=list)
    memory_note: str = Field(min_length=1)
    remaining_objectives: list[str] | None = None
    open_questions: list[str] | None = None
    stop_reason: str = ""

    @model_validator(mode="after")
    def check_decision(self) -> "ObjectivePlan":
        if self.decision == "continue":
            if not self.objective or not self.observation_summary or not self.evidence_refs or not self.success_criteria:
                raise ValueError("Continue requires one objective, evidence summary/references, and success criteria.")
            if self.stop_reason:
                raise ValueError("Continue cannot contain a stop reason.")
        elif not self.stop_reason or self.objective:
            raise ValueError("Stop requires a reason and no delegated objective.")
        for name in ("verified_facts", "hypotheses", "success_criteria", "constraints", "return_conditions"):
            if any(not item.strip() for item in getattr(self, name)):
                raise ValueError(f"{name} entries must be nonempty.")
        return self
