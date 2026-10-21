from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ai.agents.feedback.contracts import (
    AttemptReflection, ObjectiveReflection, SessionAssessment, SessionReflection,
)
from ai.agents.feedback.validation import (
    fallback_reflection, rejection_feedback, required_outcome, validate_reflection,
)
from ai.agents.react_agent import _extract_message_text
from ai.contracts.schemas import Finding
from ai.llm.structured import field_contract

R = TypeVar("R", bound=BaseModel)


class FeedbackAgent:
    """Stateless review calls with bounded output repairs, no planning or tool loop."""

    def __init__(self, llm: Any, prompt_path: Path, max_validation_retries: int = 1):
        if max_validation_retries < 0:
            raise ValueError("Feedback validation retries must be nonnegative.")
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")
        self.max_validation_retries = max_validation_retries

    def _structured(self, phase: str, payload: Dict[str, Any], schema: type[R],
                    validate: Callable[[R], None] | None = None,
                    trace_sink: List[Dict[str, Any]] | None = None) -> R:
        messages = [SystemMessage(content=self.system_prompt +
                    "\nReturn exactly one JSON object using this field contract:\n" + field_contract(schema)),
                    HumanMessage(content=json.dumps({"phase": phase, **payload}, ensure_ascii=False))]
        for call in range(self.max_validation_retries + 1):
            print(f"[Feedback] {phase} | response {call + 1}", flush=True)
            event: Dict[str, Any] = {"response": call + 1, "phase": phase}
            text = None
            try:
                response = self.llm.invoke(messages)
                text = _extract_message_text(getattr(response, "content", response)).strip()
                fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
                record = schema.model_validate(json.loads(fenced.group(1) if fenced else text))
                if validate:
                    validate(record)
            except (ValueError, TypeError) as exc:
                event.update(accepted=False, error=str(exc))
                if trace_sink is not None:
                    trace_sink.append(event)
                if text is None or call == self.max_validation_retries:
                    raise ValueError(f"Invalid feedback record: {exc}") from exc
                messages.extend([AIMessage(content=text), HumanMessage(content=(
                    f"Correct this record: {exc}. Return exactly one JSON object. "
                    "Preserve supplied evidence and runtime-required outcomes; do not invent causes or results."))])
                continue
            except Exception as exc:
                event.update(accepted=False, error=str(exc))
                if trace_sink is not None:
                    trace_sink.append(event)
                raise
            event.update(accepted=True, record=record.model_dump())
            if trace_sink is not None:
                trace_sink.append(event)
            return record
        raise RuntimeError("Feedback response budget exhausted.")

    def reflect_objective(self, *, user_goal: str, attempts: Sequence[Dict[str, Any]],
                          objective: Dict[str, Any], findings: Sequence[Finding],
                          prior_reflections: Sequence[Dict[str, Any]], open_questions: Sequence[str],
                          trace_sink: List[Dict[str, Any]] | None = None) -> ObjectiveReflection:
        """One dedicated feedback call at the objective boundary, covering each actual/rejected attempt."""
        by_id = {item["attempt_id"]: item for item in attempts}
        runtime_lessons = {key: rejection_feedback(item) for key, item in by_id.items() if item.get("rejection")}
        model_ids = set(by_id) - runtime_lessons.keys()

        def validate(record: ObjectiveReflection) -> None:
            ids = [lesson.attempt_id for lesson in record.lessons]
            if len(ids) != len(set(ids)) or set(ids) != model_ids:
                raise ValueError("Reflect every reflection_attempt_id exactly once; runtime rejection lessons are attached separately.")
            for lesson in record.lessons:
                validate_reflection(lesson, by_id[lesson.attempt_id], open_questions)

        try:
            result = self._structured("reflect_objective", {
                "user_goal": user_goal, "objective": objective, "attempts": list(attempts),
                "reflection_attempt_ids": [key for key in by_id if key in model_ids],
                "runtime_lessons": [lesson.model_dump() for lesson in runtime_lessons.values()],
                "findings": [finding.model_dump() for finding in findings],
                "prior_reflections": list(prior_reflections), "open_questions": list(open_questions),
            }, ObjectiveReflection, validate, trace_sink=trace_sink)
            lessons = {lesson.attempt_id: lesson for lesson in result.lessons}
            result.lessons = [runtime_lessons[key] if key in runtime_lessons else lessons[key] for key in by_id]
            return result
        except Exception as exc:
            return ObjectiveReflection(summary=f"Objective returned; feedback unavailable: {exc}", lessons=[
                fallback_reflection(item, str(exc)) for item in attempts])

    def reflect(self, *, user_goal: str, attempt_id: str, attempt: Dict[str, Any],
                findings: Sequence[Finding], prior_reflections: Sequence[Dict[str, Any]],
                compilation_error: str = "", open_questions: Sequence[str] = (),
                trace_sink: List[Dict[str, Any]] | None = None) -> AttemptReflection:
        ref = f"attempt:{attempt_id}"
        attempt = {**attempt, "attempt_id": attempt_id, "compilation_error": compilation_error}
        forced = required_outcome(attempt)

        def validate(record: AttemptReflection) -> None:
            validate_reflection(record, attempt, open_questions)

        try:
            reflection = self._structured("reflect_attempt", {
                "user_goal": user_goal, "attempt_id": attempt_id, "attempt": attempt,
                "findings": [finding.model_dump() for finding in findings],
                "prior_reflections": list(prior_reflections), "compilation_error": compilation_error,
                "open_questions": list(open_questions),
                "available_evidence_refs": [ref], "required_outcome": forced,
            }, AttemptReflection, validate, trace_sink=trace_sink)
        except Exception as exc:
            reflection = fallback_reflection(attempt, str(exc))
        print(f"[Feedback] {attempt_id}: {reflection.outcome} — {reflection.lesson}", flush=True)
        return reflection

    def consolidate(self, *, user_goal: str, reflections: Sequence[Dict[str, Any]],
                    stop_reason: str, objective_feedback: Sequence[Dict[str, Any]] = (), trace_sink: List[Dict[str, Any]] | None = None) -> SessionReflection:
        # Bind lessons in the runtime; the model may summarize but cannot rewrite outcomes.
        lessons = [AttemptReflection.model_validate(item) for item in reflections]
        try:
            assessment = self._structured("reflect_session", {
                "user_goal": user_goal, "reflections": list(reflections), "stop_reason": stop_reason,
                "objective_feedback": list(objective_feedback),
            }, SessionAssessment, trace_sink=trace_sink)
            result = SessionReflection(**assessment.model_dump(), lessons=lessons)
        except Exception:
            result = SessionReflection(
                summary=f"Session ended: {stop_reason or 'No further objectives queued.'}",
                lessons=lessons,
                unresolved_questions=["Review inconclusive or unsuccessful attempts before reuse."], source="runtime")
        print(f"[Feedback] Session: {result.summary}", flush=True)
        return result
