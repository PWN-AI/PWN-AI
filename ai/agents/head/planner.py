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

    def __init__(self, llm: Any, prompt_path: Path):
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

    def _structured(self, phase: str, payload: Dict[str, Any], schema: type[R],
                    validate: Callable[[R], None] | None = None) -> R:
        messages = [SystemMessage(content=self.system_prompt + "\nReturn exactly one JSON object "
                                  "matching this phase's schema:\n" + json.dumps(schema.model_json_schema())),
                    HumanMessage(content=json.dumps({"phase": phase, **payload}, ensure_ascii=False))]
        for attempt in range(2):
            response = self.llm.invoke(messages)
            text = _extract_message_text(getattr(response, "content", response)).strip()
            try:
                # Some models wrap valid JSON despite the formatting instruction. Accept only
                # a complete optional fence, never extract arbitrary embedded objects or repair prose.
                fenced = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL | re.IGNORECASE)
                record = schema.model_validate(json.loads(fenced.group(1) if fenced else text))
                if validate:
                    validate(record)
                return record
            except (ValueError, TypeError) as exc:
                if attempt:
                    raise ValueError(f"Invalid {phase} record: {exc}") from exc
                messages.extend([AIMessage(content=text), HumanMessage(content=(
                    f"Correct the record to match the schema and constraints: {exc}. "
                    "Return only JSON. Never invent evidence or executed outcomes."))])
        raise AssertionError("Unreachable")

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
                evidence: Dict[str, Any], candidate_frontier: Sequence[Dict[str, Any]] = (),
                force_branch: bool = False) -> PlannerDecision:
        context = self._context(operator_context)
        frontier = [dict(node) for node in candidate_frontier]
        trace: List[Dict[str, Any]] = []
        available = {"context"}
        available.update(f"attempt:{run['task']['id']}" for run in evidence.get("execution_history", []))
        available.update(f"finding:{item['finding_id']}" for item in evidence.get("findings", []))
        payload = {"user_goal": user_goal, "target": target.model_dump(exclude_none=True),
                   "current_context": context, "head_memory_and_evidence": evidence,
                   "candidate_frontier": frontier, "available_evidence_refs": sorted(available),
                   "force_branch": force_branch, "max_candidates": 3, "lookahead_depth": 2}

        def validate_proposal(proposal: Proposal) -> None:
            if force_branch and proposal.mode == "direct":
                raise ValueError("Initial, failed, or stalled decisions require branch mode or an evidence-based stop.")
            for candidate in proposal.candidates:
                self._check_refs(candidate.evidence_refs, available)

        try:
            proposal = self._structured("propose", payload, Proposal, validate_proposal)
            trace.append({"phase": "propose", **proposal.model_dump()})
            summaries = list(proposal.observations)
            if proposal.mode == "stop":
                return self.PlannerDecision(
                    None, summaries, True, proposal.stop_reason, [proposal.memory_note],
                    observations=proposal.observations, candidate_frontier=frontier, search_trace=trace)

            iteration = evidence.get("iteration", 0)
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

            review = self._structured("evaluate", {**payload, "candidates_to_evaluate": pool},
                                      CandidateReview, validate_review)
            trace.append({"phase": "evaluate", **review.model_dump()})
            reviewed = {item.candidate_id: item for item in review.evaluations}
            viable = []
            for node in pool:
                evaluation = reviewed[node["candidate_id"]]
                node["evaluation"] = evaluation.model_dump()
                node["score"] = evaluation.score
                missing = evaluation.unmet_prerequisites or (
                    node["candidate"]["missing_prerequisites"] if node in new_nodes else [])
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
            return self.PlannerDecision(None, [f"Head search failed: {exc}"], True,
                                        "Head search unavailable; no action generated.",
                                        candidate_frontier=frontier, search_trace=trace)

    def plan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
             max_tasks: int = 6, prior_session_feedback: Sequence[Dict[str, Any]] = ()) -> PlannerDecision:
        return self._search(user_goal=user_goal, target=target, operator_context=operator_context,
                            evidence={"stage": "initial", "memory": [], "iteration": 0,
                                      "prior_session_feedback": list(prior_session_feedback)}, force_branch=True)

    def replan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
               current_task: TaskSpec, current_result: ExecutionResult,
               findings: Sequence[Finding], remaining_tasks: Sequence[TaskSpec],
               prior_notes: Sequence[str] = (), replan_iteration: int = 0,
               max_new_tasks: int = 3, iteration_history: Sequence[Dict[str, Any]] = (),
               execution_history: Sequence[Dict[str, Any]] = (), runtime_notes: Sequence[str] = (),
               candidate_frontier: Sequence[Dict[str, Any]] = (),
               reflections: Sequence[Dict[str, Any]] = (),
               prior_session_feedback: Sequence[Dict[str, Any]] = ()) -> PlannerDecision:
        latest = reflections[-1] if reflections else {}
        return self._search(user_goal=user_goal, target=target, operator_context=operator_context,
                            candidate_frontier=candidate_frontier,
                            force_branch=not current_result.success or not latest.get("progress", False),
                            evidence={"stage": "after_execution", "memory": list(prior_notes),
                                      "iteration": replan_iteration + 1, "iteration_history": list(iteration_history),
                                      "execution_history": list(execution_history), "runtime_notes": list(runtime_notes),
                                      "reflections": list(reflections), "task": current_task.model_dump(),
                                      "prior_session_feedback": list(prior_session_feedback),
                                      "result": current_result.model_dump(),
                                      "findings": [finding.model_dump() for finding in findings],
                                      "remaining_tasks": [task.model_dump() for task in remaining_tasks]})

    def reflect(self, *, user_goal: str, attempt_id: str, attempt: Dict[str, Any],
                findings: Sequence[Finding], prior_reflections: Sequence[Dict[str, Any]],
                compilation_error: str = "") -> AttemptReflection:
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
            self._check_refs(record.evidence_refs, {ref})
            if forced and record.outcome != forced:
                raise ValueError(f"Recorded execution status requires outcome={forced}.")
            if record.outcome in {"compilation_failure", "denied", "timeout", "execution_failure",
                                  "authentication_failure", "inconclusive"} and record.progress:
                raise ValueError("An incomplete/failed attempt cannot claim verified progress.")

        try:
            reflection = self._structured("reflect_attempt", {
                "user_goal": user_goal, "attempt_id": attempt_id, "attempt": attempt,
                "findings": [finding.model_dump() for finding in findings],
                "prior_reflections": list(prior_reflections), "compilation_error": compilation_error,
                "available_evidence_refs": [ref], "required_outcome": forced,
            }, AttemptReflection, validate)
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
                    stop_reason: str) -> SessionReflection:
        # Bind lessons in the runtime; the model may summarize but cannot rewrite outcomes.
        lessons = [AttemptReflection.model_validate(item) for item in reflections]
        try:
            assessment = self._structured("reflect_session", {
                "user_goal": user_goal, "reflections": list(reflections), "stop_reason": stop_reason,
            }, SessionAssessment)
            result = SessionReflection(**assessment.model_dump(), lessons=lessons)
        except Exception:
            result = SessionReflection(
                summary=f"Session ended: {stop_reason or 'No further objectives queued.'}",
                lessons=lessons,
                unresolved_questions=["Review inconclusive or unsuccessful attempts before reuse."], source="runtime")
        print(f"[Head Reflection] Session: {result.summary}", flush=True)
        return result
