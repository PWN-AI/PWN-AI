"""Public planning records. These describe evidence and decisions, not private reasoning."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ConditionalObjective(Record):
    condition: str = Field(min_length=1)
    objective: str = Field(min_length=1)


class CandidateObjective(Record):
    objective: str = Field(min_length=1)
    observation_summary: str = Field(min_length=1)
    evidence_refs: list[str] = Field(min_length=1)
    prerequisites: list[str] = Field(default_factory=list)
    missing_prerequisites: list[str] = Field(default_factory=list)
    expected_information: str = Field(min_length=1)
    # One conditional level below the immediate objective; never an observed result.
    followups: list[ConditionalObjective] = Field(default_factory=list, max_length=2)


class Proposal(Record):
    observations: list[str] = Field(default_factory=list, max_length=8)
    mode: Literal["direct", "branch", "stop"]
    candidates: list[CandidateObjective] = Field(default_factory=list, max_length=3)
    memory_note: str = Field(min_length=1)
    # Omitted/null preserves the previous assessment; [] explicitly clears it.
    remaining_objectives: list[str] | None = None
    open_questions: list[str] | None = None
    stop_reason: str = ""

    @model_validator(mode="after")
    def check_mode(self) -> "Proposal":
        if self.mode == "stop":
            if self.candidates or not self.stop_reason:
                raise ValueError("A stop requires a reason and no candidates.")
        elif self.mode == "direct":
            if len(self.candidates) != 1:
                raise ValueError("Direct mode requires exactly one candidate.")
        elif len(self.candidates) < 2:
            raise ValueError("Branch mode requires two or three candidates.")
        objectives = [candidate.objective.casefold() for candidate in self.candidates]
        if len(set(objectives)) != len(objectives):
            raise ValueError("Candidate objectives must be distinct.")
        if any(not item.strip() for item in self.observations):
            raise ValueError("Observations must be nonempty evidence assessments.")
        return self


class CandidateEvaluation(Record):
    candidate_id: str = Field(min_length=1)
    disposition: Literal["viable", "defer", "prune"]
    evidence_refs: list[str] = Field(min_length=1)
    assessment: str = Field(min_length=1)
    unmet_prerequisites: list[str] = Field(default_factory=list)
    evidence_strength: int = Field(ge=0, le=3)
    information_gain: int = Field(ge=0, le=3)
    goal_alignment: int = Field(ge=0, le=3)
    cost: int = Field(ge=1, le=3)

    @property
    def score(self) -> int:
        # Ordinal prioritization, not a probability of success.
        return 2 * self.evidence_strength + self.information_gain + self.goal_alignment - self.cost


class CandidateReview(Record):
    evaluations: list[CandidateEvaluation] = Field(min_length=1, max_length=6)


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
