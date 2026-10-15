from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ai.agents.head.contracts import (
    AttemptReflection, CandidateObjective, CandidateReview, Proposal, SessionAssessment, SessionReflection,
)
from ai.agents.head.memory import build_working_memory, select_older_evidence
from ai.agents.react_agent import _extract_message_text
from ai.contracts.schemas import ExecutionResult, Finding, TargetSpec, TaskSpec

R = TypeVar("R", bound=BaseModel)


@dataclass
class ObservationHandoff:
    """Only the selected objective's summary crosses into the implementor."""
    observation_summary: str
    next_step: str
    target: TargetSpec
    context: Dict[str, Any]

    def model_dump(self) -> Dict[str, Any]:
        return {"observation_summary": self.observation_summary, "next_step": self.next_step,
                "target": self.target.model_dump(exclude_none=True), "context": self.context}


class HeadPlanner:
    """Bounded candidate search and evidence-based reflection; no execution or retrieval."""

    def __init__(self, llm: Any, prompt_path: Path, max_thinking_rounds: int = 3):
        if max_thinking_rounds < 1:
            raise ValueError("max_thinking_rounds must be at least one.")
        self.max_thinking_rounds = max_thinking_rounds
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    @dataclass
    class PlannerDecision:
        handoff: ObservationHandoff | None
        thinking_summary: List[str]
        should_stop: bool = False
        stop_reason: str = ""
        task_notes: List[str] = field(default_factory=list)
        observations: List[str] = field(default_factory=list)
        observation_summary: str = ""
        selected_candidate_id: str = ""
        candidate_frontier: List[Dict[str, Any]] = field(default_factory=list)
        search_trace: List[Dict[str, Any]] = field(default_factory=list)

    @staticmethod
    def _field_contract(schema: type[BaseModel]) -> str:
        """Describe the final record in prose so the model does not echo a schema document."""
        document = schema.model_json_schema()
        definitions = document.get("$defs", {})
        lines = []

        def resolve(node: Dict[str, Any]) -> Dict[str, Any]:
            return definitions[node["$ref"].split("/")[-1]] if "$ref" in node else node

        def kind(node: Dict[str, Any]) -> str:
            node = resolve(node)
            if "anyOf" in node:
                return " or ".join(kind(option) for option in node["anyOf"])
            if "enum" in node:
                return "one of " + ", ".join(repr(value) for value in node["enum"])
            if node.get("type") == "array":
                return "array of " + kind(node["items"])
            return node.get("type", "value")

        def fields(node: Dict[str, Any], prefix: str = "") -> None:
            node = resolve(node)
            for name, spec in node.get("properties", {}).items():
                path = prefix + name
                required = "required" if name in node.get("required", []) else "optional"
                limits = [f"{key}={spec[key]}" for key in (
                    "minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum") if key in spec]
                lines.append(f"- {path}: {kind(spec)} ({', '.join([required, *limits])})")
                child = resolve(spec)
                if child.get("type") == "array":
                    child = resolve(child["items"])
                    fields(child, path + "[].")
                elif child.get("type") == "object":
                    fields(child, path + ".")
        fields(document)
        return "\n".join(lines)

    @staticmethod
    def _read_assessment(text: str) -> tuple[str, str, str]:
        markers = list(re.finditer(r"(?im)^[ \t]*Status:[^\n]*$", text))
        if len(markers) != 1:
            raise ValueError("Use exactly one standalone Status: ready or Status: need_more_thinking line.")
        marker = markers[0]
        status = re.fullmatch(r"Status:\s*(ready|need_more_thinking)\s*", marker.group().strip(), re.IGNORECASE)
        if status is None:
            raise ValueError("Status must be ready or need_more_thinking.")
        assessment, final_json = text[:marker.start()].strip(), text[marker.end():].strip()
        for start in re.finditer(r"(?m)^[ \t]*\{", assessment):
            try:
                json.JSONDecoder().raw_decode(assessment[start.start():].lstrip())
            except ValueError:
                continue
            raise ValueError("Write the final JSON only after Status: ready.")
        value = status.group(1).lower()
        if value == "need_more_thinking":
            if not assessment or final_json:
                raise ValueError("need_more_thinking requires a nonempty assessment and no final JSON or trailing text.")
        elif not final_json:
            raise ValueError("ready requires the final JSON after the status line.")
        return assessment, value, final_json

    def _structured(self, phase: str, payload: Dict[str, Any], schema: type[R],
                    validate: Callable[[R], None] | None = None,
                    trace_sink: List[Dict[str, Any]] | None = None) -> R:
        messages = [SystemMessage(content=self.system_prompt +
                    f"\nYou have at most {self.max_thinking_rounds} assessment rounds in this phase, "
                    "plus one correction for invalid output. Assessment labels are optional. "
                    "Use Status: need_more_thinking to continue without JSON, or Status: ready "
                    "followed by exactly one final JSON object using this phase's field contract:\n" +
                    self._field_contract(schema) +
                    "\nThe contract describes fields; do not copy it as your answer. "
                    f"Your final record uses these top-level fields: {', '.join(schema.model_fields)}. "
                    "Fill them with actual values for the supplied goal and evidence. Never return "
                    "$defs, properties, type definitions, or placeholder examples as the final record."),
                    HumanMessage(content=json.dumps({"phase": phase, **payload}, ensure_ascii=False))]
        rounds, corrections = 0, 0
        # A malformed response gets one repair; it does not consume a valid assessment round.
        for call in range(self.max_thinking_rounds + 1):
            print(f"[Head Thinking] {phase} | response {call + 1}", flush=True)
            response = self.llm.invoke(messages)
            text = _extract_message_text(getattr(response, "content", response)).strip()
            event: Dict[str, Any] = {"response": call + 1}
            try:
                assessment, status, final_json = self._read_assessment(text)
                event.update(assessment=assessment, status=status)
                if status == "ready":
                    fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", final_json, re.DOTALL | re.IGNORECASE)
                    record = schema.model_validate(json.loads(fenced.group(1) if fenced else final_json))
                    if validate:
                        validate(record)
                event["accepted"] = True
            except (ValueError, TypeError) as exc:
                event.update(accepted=False, error=str(exc))
                if trace_sink is not None:
                    trace_sink.append(event)
                if corrections:
                    raise ValueError(f"Invalid {phase} record: {exc}") from exc
                corrections += 1
                messages.extend([AIMessage(content=text), HumanMessage(content=(
                    f"Correct the output: {exc}. Assessment labels are optional. "
                    "Finish with Status: need_more_thinking and no JSON, or Status: ready "
                    "followed by one JSON object matching the schema. Never invent evidence or executed outcomes."))])
                continue
            if trace_sink is not None:
                trace_sink.append(event)
            rounds += 1
            if status == "ready":
                return record
            if rounds >= self.max_thinking_rounds:
                raise RuntimeError(f"Head {phase} thinking budget exhausted before Status: ready; no handoff generated.")
            messages.extend([AIMessage(content=text), HumanMessage(content=(
                "Continue the same phase using the supplied evidence and your previous assessments. "
                f"You have {self.max_thinking_rounds - rounds} assessment round(s) left. "
                "Choose any assessment labels or plain prose as useful. Emit final JSON only after Status: ready."))])
        raise RuntimeError(f"Head {phase} response budget exhausted; no handoff generated.")

    @staticmethod
    def _context(operator_context: Dict[str, Any]) -> Dict[str, Any]:
        context = {key: operator_context[key] for key in (
            "auth_verified", "auth_cookie", "authenticated_page_url", "discovered_endpoints",
            "navigation_structure", "username",
        ) if key in operator_context}
        if operator_context.get("authenticated_page_html"):
            context["page_sample"] = str(operator_context["authenticated_page_html"])[:3000]
        return context

    @staticmethod
    def _check_refs(refs: Sequence[str], available: set[str]) -> None:
        if not refs or not set(refs).issubset(available):
            raise ValueError("Evidence references must identify supplied context, attempts, or findings.")

    def _search(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
                working_memory: Dict[str, Any], candidate_frontier: Sequence[Dict[str, Any]] = (),
                raw_history: Sequence[Dict[str, Any]] = (), force_branch: bool = False) -> PlannerDecision:
        context = self._context(operator_context)
        frontier = [dict(node) for node in candidate_frontier]
        trace: List[Dict[str, Any]] = []
        available = {"context"}
        available.update(working_memory["history_index"]["attempt_refs"])
        available.update(item["evidence_ref"] for item in working_memory["verified_facts"]["findings"])
        payload = {"working_memory": working_memory, "available_evidence_refs": sorted(available),
                   "force_branch": force_branch, "max_candidates": 3, "lookahead_depth": 2}

        def validate_proposal(proposal: Proposal) -> None:
            if force_branch and proposal.mode == "direct":
                raise ValueError("Initial, failed, or stalled decisions require branch mode or an evidence-based stop.")
            for candidate in proposal.candidates:
                self._check_refs(candidate.evidence_refs, available)

        try:
            proposal_trace = {"phase": "propose", "thinking_steps": []}
            trace.append(proposal_trace)
            proposal = self._structured("propose", payload, Proposal, validate_proposal,
                                        trace_sink=proposal_trace["thinking_steps"])
            proposal_trace.update(proposal.model_dump())
            summaries = [step["assessment"] for step in proposal_trace["thinking_steps"]
                         if step.get("accepted") and step.get("assessment")] + list(proposal.observations)
            if proposal.mode == "stop":
                return self.PlannerDecision(
                    None, summaries, True, proposal.stop_reason, [proposal.memory_note],
                    observations=proposal.observations, candidate_frontier=frontier, search_trace=trace)

            iteration = working_memory["scope"]["iteration"]
            parent = next((node["candidate_id"] for node in reversed(frontier)
                           if node.get("status") == "attempted"), None)
            new_nodes = [{"candidate_id": f"branch-{iteration:02d}-{index:02d}",
                          "parent_id": parent, "created_iteration": iteration,
                          "status": "available", "candidate": candidate.model_dump()}
                         for index, candidate in enumerate(proposal.candidates, 1)]
            # Direct steps need only their own review. Branch mode revisits retained alternatives
            # against CURRENT evidence, including previously deferred/pruned candidates.
            retained = [node for node in frontier if node.get("status") != "attempted"]
            pool = (retained[-(6 - len(new_nodes)):] if proposal.mode == "branch" else []) + new_nodes
            unique: Dict[str, Dict[str, Any]] = {}
            for node in pool:
                unique[node["candidate"]["objective"].strip().casefold()] = node
            pool = list(unique.values())
            ids = {node["candidate_id"] for node in pool}

            def validate_review(review: CandidateReview) -> None:
                actual = [item.candidate_id for item in review.evaluations]
                if len(actual) != len(set(actual)) or set(actual) != ids:
                    raise ValueError("Evaluate every supplied candidate exactly once using its exact ID.")
                for item in review.evaluations:
                    self._check_refs(item.evidence_refs, available)

            # New proposals may cite an older attempt that was not relevant before this call.
            # Retrieve only verbatim windows for the review; never replay the archive.
            review_memory = {**working_memory, "relevant_older_evidence": select_older_evidence(
                raw_history, goal=user_goal, candidates=pool, feedback=working_memory["feedback"])}
            review_trace = {"phase": "evaluate", "thinking_steps": []}
            trace.append(review_trace)
            review = self._structured("evaluate", {**payload, "working_memory": review_memory,
                                                   "candidates_to_evaluate": pool},
                                      CandidateReview, validate_review, trace_sink=review_trace["thinking_steps"])
            review_trace.update(review.model_dump())
            reviewed = {item.candidate_id: item for item in review.evaluations}
            viable = []
            for node in pool:
                evaluation = reviewed[node["candidate_id"]]
                node["evaluation"] = evaluation.model_dump()
                node["score"] = evaluation.score
                missing = evaluation.unmet_prerequisites or (
                    node["candidate"]["missing_prerequisites"] if node in new_nodes else [])
                node["unmet_prerequisites"] = list(missing)
                node["status"] = "defer" if missing else evaluation.disposition
                summaries.append(f"Candidate {node['candidate_id']}: {node['status']} "
                                 f"(score {node['score']}) — {evaluation.assessment}")
                if node["status"] == "viable":
                    viable.append(node)
            updated = {node["candidate_id"]: node for node in frontier}
            updated.update({node["candidate_id"]: node for node in pool})
            # Full search records remain in head_iterations; bound the working frontier.
            frontier = list(updated.values())[-6:]
            if not viable:
                return self.PlannerDecision(
                    None, summaries, True, "No viable objective with satisfied prerequisites.",
                    [proposal.memory_note], observations=proposal.observations,
                    candidate_frontier=frontier, search_trace=trace)
            chosen = max(viable, key=lambda node: node["score"])
            chosen["status"] = "selected"
            # Keep the selected node even when it was an older retained branch.
            frontier = [node for node in frontier if node["candidate_id"] != chosen["candidate_id"]][-5:] + [chosen]
            candidate = CandidateObjective.model_validate(chosen["candidate"])
            summaries.extend([f"Summary: {candidate.observation_summary}", f"Next step: {candidate.objective}"])
            return self.PlannerDecision(
                ObservationHandoff(candidate.observation_summary, candidate.objective, target, context),
                summaries, task_notes=[proposal.memory_note], observations=proposal.observations,
                observation_summary=candidate.observation_summary, selected_candidate_id=chosen["candidate_id"],
                candidate_frontier=frontier, search_trace=trace)
        except Exception as exc:
            if trace:
                trace[-1]["error"] = str(exc)
            return self.PlannerDecision(None, [f"Head search failed: {exc}"], True,
                                        "Head search unavailable; no action generated.",
                                        candidate_frontier=frontier, search_trace=trace)

    def plan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
             max_tasks: int = 6, working_memory: Dict[str, Any] | None = None) -> PlannerDecision:
        memory = working_memory if working_memory is not None else build_working_memory(
            session_id="", user_goal=user_goal, target=target.model_dump(exclude_none=True),
            operator_context=operator_context)
        return self._search(user_goal=user_goal, target=target, operator_context=operator_context,
                            working_memory=memory, force_branch=True)

    def replan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
               current_task: TaskSpec, current_result: ExecutionResult,
               findings: Sequence[Finding], remaining_tasks: Sequence[TaskSpec],
               prior_notes: Sequence[str] = (), replan_iteration: int = 0,
               max_new_tasks: int = 3, iteration_history: Sequence[Dict[str, Any]] = (),
               execution_history: Sequence[Dict[str, Any]] = (), runtime_notes: Sequence[str] = (),
               candidate_frontier: Sequence[Dict[str, Any]] = (),
               reflections: Sequence[Dict[str, Any]] = (),
               working_memory: Dict[str, Any] | None = None) -> PlannerDecision:
        latest = reflections[-1] if reflections else {}
        # Raw records are kept outside the model payload; the memory builder selects excerpts.
        runs = list(execution_history) or [{"task": current_task.model_dump(),
                                           "result": current_result.model_dump()}]
        memory = working_memory if working_memory is not None else build_working_memory(
            session_id="", user_goal=user_goal, target=target.model_dump(exclude_none=True),
            operator_context=operator_context, runs=runs, reflections=reflections, findings=findings,
            iterations=iteration_history, candidates=candidate_frontier, runtime_notes=runtime_notes,
            iteration=replan_iteration + 1)
        return self._search(user_goal=user_goal, target=target, operator_context=operator_context,
                            candidate_frontier=candidate_frontier, working_memory=memory, raw_history=runs,
                            force_branch=not current_result.success or not latest.get("progress", False))

    def reflect(self, *, user_goal: str, attempt_id: str, attempt: Dict[str, Any],
                findings: Sequence[Finding], prior_reflections: Sequence[Dict[str, Any]],
                compilation_error: str = "", open_questions: Sequence[str] = (),
                trace_sink: List[Dict[str, Any]] | None = None) -> AttemptReflection:
        ref = f"attempt:{attempt_id}"
        forced = "compilation_failure" if compilation_error else None
        if attempt.get("status") == "denied":
            forced = "denied"
        result = attempt.get("result", {})
        error = str(result.get("error") or "").lower()
        if not forced and (result.get("raw_output", {}).get("timed_out") is True or
                           "timed out" in error or "timeoutexpired" in error):
            forced = "timeout"
        if not forced and (result.get("raw_output", {}).get("auth_failed") is True or any(
            signal in error for signal in ("authentication failed", "session expired", "redirect to login")
        )):
            forced = "authentication_failure"
        if not forced and error:
            forced = "execution_failure"

        def validate(record: AttemptReflection) -> None:
            if record.attempt_id != attempt_id or record.source != "model":
                raise ValueError("Preserve the supplied attempt_id and use source=model.")
            if record.outcome == "duplicate_rejected":
                raise ValueError("Duplicate rejection is written only by the runtime, never inferred from tool output.")
            self._check_refs(record.evidence_refs, {ref})
            if forced and record.outcome != forced:
                raise ValueError(f"Recorded execution status requires outcome={forced}.")
            if record.resolved_question and (record.resolved_question not in open_questions or
                                             record.outcome not in {"positive", "negative"}):
                raise ValueError("Only a supplied open question resolved by a conclusive result may be closed.")
            if record.outcome in {"compilation_failure", "denied", "timeout", "execution_failure",
                                  "authentication_failure", "inconclusive", "duplicate_rejected"} and record.progress:
                raise ValueError("An incomplete/failed attempt cannot claim verified progress.")

        try:
            reflection = self._structured("reflect_attempt", {
                "user_goal": user_goal, "attempt_id": attempt_id, "attempt": attempt,
                "findings": [finding.model_dump() for finding in findings],
                "prior_reflections": list(prior_reflections), "compilation_error": compilation_error,
                "open_questions": list(open_questions),
                "available_evidence_refs": [ref], "required_outcome": forced,
            }, AttemptReflection, validate, trace_sink=trace_sink)
        except Exception as exc:
            reflection = AttemptReflection(
                attempt_id=attempt_id, outcome=forced or "inconclusive", evidence_refs=[ref],
                lesson=f"Reflection unavailable; retain the actual attempt without inferring its outcome: {exc}",
                next_adjustment="Review recorded execution evidence before choosing another objective.",
                retry_condition="Retry only with new evidence or corrected prerequisites.",
                progress=False, source="runtime")
        print(f"[Head Reflection] {attempt_id}: {reflection.outcome} — {reflection.lesson}", flush=True)
        return reflection

    def consolidate(self, *, user_goal: str, reflections: Sequence[Dict[str, Any]],
                    stop_reason: str, trace_sink: List[Dict[str, Any]] | None = None) -> SessionReflection:
        # Bind lessons in the runtime; the model may summarize but cannot rewrite outcomes.
        lessons = [AttemptReflection.model_validate(item) for item in reflections]
        try:
            assessment = self._structured("reflect_session", {
                "user_goal": user_goal, "reflections": list(reflections), "stop_reason": stop_reason,
            }, SessionAssessment, trace_sink=trace_sink)
            result = SessionReflection(**assessment.model_dump(), lessons=lessons)
        except Exception:
            result = SessionReflection(
                summary=f"Session ended: {stop_reason or 'No further objectives queued.'}",
                lessons=lessons,
                unresolved_questions=["Review inconclusive or unsuccessful attempts before reuse."], source="runtime")
        print(f"[Head Reflection] Session: {result.summary}", flush=True)
        return result
