"""Runtime-owned outcome constraints; independent of any model response."""
from typing import Any, Dict, Sequence

from ai.agents.feedback.contracts import AttemptReflection


def required_outcome(attempt: Dict[str, Any]) -> str | None:
    if attempt.get("rejection"):
        return "duplicate_rejected"
    if attempt.get("compilation_error"):
        return "compilation_failure"
    if attempt.get("status") == "denied":
        return "denied"
    result = attempt.get("result", {})
    error = str(result.get("error") or "").lower()
    if result.get("raw_output", {}).get("timed_out") is True or "timed out" in error or "timeoutexpired" in error:
        return "timeout"
    output = (str(result.get("stdout", "")) + str(result.get("stderr", ""))).lower()
    if any(signal in output for signal in ("redirect to login", "got a 302 redirect", "session expired",
                                           "session has expired", "location: /login", "location: /signin",
                                           "location: /sign-in", "303 see other")):
        return "authentication_failure"
    if result.get("raw_output", {}).get("auth_failed") is True or any(
        signal in error for signal in ("authentication failed", "session expired", "redirect to login")):
        return "authentication_failure"
    if error or result.get("success") is False or result.get("returncode", 0) != 0:
        return "execution_failure"
    return None


def validate_reflection(record: AttemptReflection, attempt: Dict[str, Any],
                         open_questions: Sequence[str]) -> None:
    attempt_id = attempt["attempt_id"]
    required = required_outcome(attempt)
    if record.attempt_id != attempt_id:
        raise ValueError("Preserve the supplied attempt_id.")
    if not record.evidence_refs or set(record.evidence_refs) != {f"attempt:{attempt_id}"}:
        raise ValueError("Evidence references must identify this exact supplied attempt.")
    if required and record.outcome != required:
        raise ValueError(f"Recorded execution status requires outcome={required}.")
    if not attempt.get("rejection") and record.outcome == "duplicate_rejected":
        raise ValueError("Only the runtime can establish duplicate rejection.")
    if record.source != ("runtime" if attempt.get("rejection") else "model"):
        raise ValueError("Use source=model, except source=runtime for a recorded duplicate rejection.")
    if record.resolved_question and (record.resolved_question not in open_questions or
                                     record.outcome not in {"positive", "negative"}):
        raise ValueError("Only conclusive evidence can resolve an exact supplied open question.")
    if record.outcome not in {"positive", "negative"} and record.progress:
        raise ValueError("An incomplete/failed attempt cannot claim verified progress.")


def rejection_feedback(attempt: Dict[str, Any]) -> AttemptReflection:
    attempt_id = attempt["attempt_id"]
    return AttemptReflection(
        attempt_id=attempt_id, outcome="duplicate_rejected", evidence_refs=[f"attempt:{attempt_id}"],
        lesson=attempt["rejection"]["message"],
        next_adjustment="Correct the executable arguments or choose a supported alternative objective.",
        retry_condition="New evidence or changed tool, action, target, or arguments is required.",
        progress=False, source="runtime")


def fallback_reflection(attempt: Dict[str, Any], error: str) -> AttemptReflection:
    attempt_id = attempt["attempt_id"]
    if attempt.get("rejection"):
        return rejection_feedback(attempt)
    return AttemptReflection(
        attempt_id=attempt_id, outcome=required_outcome(attempt) or "inconclusive",
        evidence_refs=[f"attempt:{attempt_id}"], lesson=f"Retain actual evidence; feedback unavailable: {error}",
        next_adjustment="Review actual execution evidence and prerequisites before choosing another objective.",
        retry_condition="Retry only with new evidence or corrected inputs.", progress=False, source="runtime")
