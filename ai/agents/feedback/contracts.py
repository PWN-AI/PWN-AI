"""Validated feedback records shared by the runtime and feedback agent."""
from typing import Literal

from pydantic import Field

from ai.contracts.records import Record

Outcome = Literal[
    "positive", "negative", "execution_failure", "authentication_failure", "timeout",
    "denied", "inconclusive", "compilation_failure", "duplicate_rejected",
]


class AttemptReflection(Record):
    attempt_id: str = Field(min_length=1)
    outcome: Outcome
    evidence_refs: list[str] = Field(min_length=1)
    lesson: str = Field(min_length=1)
    next_adjustment: str = Field(min_length=1)
    retry_condition: str = Field(min_length=1)
    progress: bool
    resolved_question: str = ""
    # Set by runtime when the reflection model is unavailable/invalid.
    source: Literal["model", "runtime"] = "model"


class SessionAssessment(Record):
    summary: str = Field(min_length=1)
    unresolved_questions: list[str] = Field(default_factory=list)


class SessionReflection(SessionAssessment):
    lessons: list[AttemptReflection] = Field(default_factory=list)
    source: Literal["model", "runtime"] = "model"


class ObjectiveReflection(Record):
    summary: str = Field(min_length=1)
    lessons: list[AttemptReflection] = Field(default_factory=list)
