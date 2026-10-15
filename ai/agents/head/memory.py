"""Build goal-scoped working memory from the current session's raw archive.

No filesystem access or cross-session recall. Raw runs and head iterations stay in
SessionState; only structured facts, an attempt ledger, feedback, and selected
verbatim evidence are included in planning prompts.
"""
from __future__ import annotations

import json
import re
from copy import deepcopy
from typing import Any, Mapping, Sequence
from urllib.parse import parse_qsl, urlsplit


MAX_OLDER_ATTEMPTS = 3
EXCERPT_CHARS = 1200
_CONTEXT_KEYS = (
    "auth_verified", "auth_cookie", "authenticated_page_url", "discovered_endpoints",
    "navigation_structure", "username",
)
_STOP_WORDS = {
    "the", "and", "for", "from", "with", "that", "this", "are", "was", "not", "http", "https",
    "target", "context", "attempt", "check", "scan", "observed", "endpoint", "documentation",
}


def _dump(value: Any) -> Any:
    return value.model_dump() if hasattr(value, "model_dump") else value


def _tokens(value: Any) -> set[str]:
    # JSON field names would create false relevance between every pair of records.
    if isinstance(value, Mapping):
        return set().union(*(_tokens(item) for item in value.values()))
    if isinstance(value, (list, tuple)):
        return set().union(*(_tokens(item) for item in value))
    if not isinstance(value, str):
        return set()
    return {word for word in re.findall(r"[\w%.-]{3,}", value.lower()) if word not in _STOP_WORDS}


def _excerpt(text: str, terms: set[str], *, field: str) -> dict[str, Any]:
    # Match in the original string so Unicode case conversion cannot shift offsets.
    matches = [(len(term), match.start()) for term in terms
               if (match := re.search(re.escape(term), text, re.IGNORECASE))]
    # Prefer a specific technical identifier over a short generic word near the header.
    position = max(matches, key=lambda item: (item[0], -item[1]))[1] if matches else 0
    start = max(0, position - 200)
    end = min(len(text), start + EXCERPT_CHARS)
    return {"field": field, "text": text[start:end], "start": start, "end": end,
            "total_chars": len(text), "truncated": start != 0 or end != len(text)}


def select_older_evidence(runs: Sequence[Mapping[str, Any]], *, goal: str,
                          candidates: Sequence[Mapping[str, Any]],
                          feedback: Sequence[Mapping[str, Any]],
                          focus: Sequence[Mapping[str, Any]] = ()) -> list[dict[str, Any]]:
    """Retrieve relevant verbatim windows; never modify the underlying archive.

    Explicit candidate citations rank first, followed by overlap with objectives,
    unresolved prerequisites and recent feedback. Recency breaks ties. The newest
    execution is supplied separately in full and is excluded here.
    """
    objectives = [node.get("candidate", node) for node in candidates] + list(focus)
    refs = {ref for candidate in objectives for ref in candidate.get("evidence_refs", [])}
    terms = _tokens([goal, objectives, list(feedback)[-3:]])
    ranked = []
    for index, run in enumerate(runs[:-1]):
        task = run.get("task", {})
        ref = f"attempt:{task.get('id', '')}"
        result = run.get("result", {})
        text = {key: result.get(key, "") for key in ("stdout", "stderr", "error")}
        # Structured output can contain evidence absent from stdout.
        if result.get("raw_output"):
            text["raw_output"] = json.dumps(result["raw_output"], ensure_ascii=False)
        score = (1000 if ref in refs else 0) + len(terms & _tokens([task, text]))
        if score:
            ranked.append((score, index, ref, run, text))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    # Extract windows only for selected records, not every output in the archive.
    return [{"evidence_ref": ref, "request": deepcopy(run.get("request", {})),
             "excerpts": [_excerpt(value, terms, field=key) for key, value in text.items() if value],
             "artifacts": deepcopy(run.get("result", {}).get("artifacts", {}))}
            for _, _, ref, run, text in ranked[:MAX_OLDER_ATTEMPTS]]


def build_working_memory(*, session_id: str, user_goal: str, target: Mapping[str, Any],
                         operator_context: Mapping[str, Any],
                         runs: Sequence[Any] = (), reflections: Sequence[Any] = (),
                         findings: Sequence[Any] = (), iterations: Sequence[Mapping[str, Any]] = (),
                         candidates: Sequence[Mapping[str, Any]] = (),
                         runtime_notes: Sequence[str] = (), constraints: Mapping[str, Any] | None = None,
                         iteration: int = 0, focus: Sequence[Mapping[str, Any]] = ()) -> dict[str, Any]:
    """Rebuild from authoritative records, avoiding recursive summary drift.

    Facts retain exact supplied values and provenance. Requests are attempts, not
    proof that their parameters or hypotheses are valid. Model notes and candidate
    assessments remain separate from runtime-observed context and finding evidence.
    """
    records = [_dump(run) for run in runs]
    feedback = [_dump(item) for item in reflections]
    found = [_dump(item) for item in findings]
    by_attempt = {item["attempt_id"]: item for item in feedback}
    context = {key: operator_context[key] for key in _CONTEXT_KEYS if key in operator_context}
    if operator_context.get("authenticated_page_html"):
        context["page_sample"] = str(operator_context["authenticated_page_html"])[:3000]
    urls = [target.get("url"), context.get("authenticated_page_url")]
    urls.extend(item for item in (context.get("discovered_endpoints") or []) if isinstance(item, str))
    observed_urls = []
    for url in dict.fromkeys(url for url in urls if url):
        try:
            query = urlsplit(url).query
        except ValueError:
            query = ""
        observed_urls.append({"url": url, "raw_query": query,
                              "parameters": [{"name": name, "value": value}
                                             for name, value in parse_qsl(query, keep_blank_values=True)],
                              "evidence_refs": ["context"]})
    facts = {
        "target": {"value": dict(target), "provenance": "operator_supplied", "evidence_refs": ["context"]},
        "application_context": {"value": context, "provenance": "runtime_observed_or_operator_supplied",
                                "evidence_refs": ["context"]},
        "observed_urls": observed_urls,
        "findings": [{"evidence_ref": f"finding:{item['finding_id']}",
                      "provenance": "analyzer_reported", **item} for item in found],
    }
    ledger = []
    for run in records:
        task = run.get("task", {})
        attempt_id = task.get("id", "")
        result = run.get("result", {})
        assessment = by_attempt.get(attempt_id, {})
        ledger.append({
            "attempt_id": attempt_id, "evidence_ref": f"attempt:{attempt_id}",
            "objective": task.get("objective", ""), "request": run.get("request", {}),
            "execution_status": run.get("status", "unknown"), "success": result.get("success"),
            "returncode": result.get("returncode"), "error": result.get("error"),
            "outcome": assessment.get("outcome", "awaiting_reflection"),
            "progress": assessment.get("progress", False),
            "resolved_question": assessment.get("resolved_question", ""),
            "assessment_source": assessment.get("source"),
        })
    # Failed compilation has feedback but no executable run; retain it in the ledger too.
    for entry in iterations:
        attempt_id = entry.get("task_id", "")
        if entry.get("compilation_error") and not any(item["attempt_id"] == attempt_id for item in ledger):
            assessment = by_attempt.get(attempt_id, {})
            ledger.append({"attempt_id": attempt_id, "evidence_ref": f"attempt:{attempt_id}",
                           "objective": entry.get("next_step", ""), "execution_status": "not_executed",
                           "error": entry["compilation_error"], "outcome": "compilation_failure",
                           "progress": False, "resolved_question": "", "assessment_source": assessment.get("source")})
    proposals = [record for entry in reversed(iterations)
                 for record in reversed(entry.get("search_trace", [])) if record.get("phase") == "propose"]
    latest_proposal = proposals[0] if proposals else {}
    remaining = next((record["remaining_objectives"] for record in proposals
                      if record.get("remaining_objectives") is not None), None)
    if remaining is None:
        remaining = [] if latest_proposal.get("mode") == "stop" else [
            node["candidate"]["objective"] for node in candidates if node.get("status") not in {"attempted", "prune"}]
    questions = list(next((record["open_questions"] for record in proposals
                           if record.get("open_questions") is not None), []))
    for node in candidates:
        if node.get("status") not in {"attempted", "prune"}:
            questions.extend(node.get("unmet_prerequisites", node.get("evaluation", {}).get(
                "unmet_prerequisites", node.get("candidate", {}).get("missing_prerequisites", []))))
    latest = records[-1] if records else None
    resolved = {item.get("resolved_question", "").casefold() for item in feedback
                if item.get("resolved_question") and item.get("outcome") in {"positive", "negative"}}
    return deepcopy({
        "scope": {"session_id": session_id, "iteration": iteration},
        "goal_and_constraints": {"goal": user_goal, "target": dict(target),
                                 "runtime_limits": dict(constraints or {}),
                                 "operator_constraints": operator_context.get("constraints", []),
                                 "remaining_objectives": remaining,
                                 "planning_status": {"mode": latest_proposal.get("mode", "initial"),
                                                     "stop_reason": latest_proposal.get("stop_reason", "")}},
        "verified_facts": facts,
        "attempt_ledger": ledger,
        "feedback": feedback,
        "open_questions": list(dict.fromkeys(question for question in questions if question.casefold() not in resolved)),
        "candidate_objectives": list(candidates),
        "latest_result": {"evidence_ref": f"attempt:{latest['task']['id']}",
                          "request": latest.get("request", {}), "result": latest.get("result", {})} if latest else None,
        "relevant_older_evidence": select_older_evidence(records, goal=user_goal, candidates=candidates,
                                                        feedback=feedback, focus=focus),
        "planning_notes": [{"task_id": entry.get("task_id"), "notes": entry.get("memory_notes", [])}
                           for entry in list(iterations)[-3:]],
        "runtime_notes": list(runtime_notes)[-8:],
        "history_index": {"attempt_refs": [item["evidence_ref"] for item in ledger],
                          "iteration_count": len(iterations), "raw_history_available_in_session": True},
    })
