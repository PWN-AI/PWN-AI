"""The implementor returns assessments; execution records remain authoritative."""
from typing import Literal

from pydantic import Field

from ai.contracts.records import Record


class ObjectiveReport(Record):
    status: Literal["completed", "blocked", "strategy_change", "inconclusive"]
    summary: str = Field(min_length=1)
    evidence_refs: list[str] = Field(default_factory=list)
    remaining_uncertainty: list[str] = Field(default_factory=list)
