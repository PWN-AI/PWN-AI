from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Sequence, TypeVar

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import BaseModel

from ai.agents.head.contracts import ObjectivePlan
from ai.agents.head.memory import build_working_memory
from ai.agents.react_agent import _extract_message_text
from ai.contracts.schemas import TargetSpec
from ai.llm.structured import field_contract

R = TypeVar("R", bound=BaseModel)


@dataclass
class ObservationHandoff:
    """A bounded objective and selected evidence, without the head assessment transcript."""
    observation_summary: str
    next_step: str
    target: TargetSpec
    context: Dict[str, Any]
    evidence_refs: List[str] = field(default_factory=list)
    verified_facts: List[str] = field(default_factory=list)
    hypotheses: List[str] = field(default_factory=list)
    success_criteria: List[str] = field(default_factory=list)
    constraints: List[str] = field(default_factory=list)
    return_conditions: List[str] = field(default_factory=list)

    def model_dump(self) -> Dict[str, Any]:
        return {"observation_summary": self.observation_summary, "next_step": self.next_step,
                "target": self.target.model_dump(exclude_none=True), "context": self.context,
                **{name: getattr(self, name) for name in ("evidence_refs", "verified_facts", "hypotheses",
                                                        "success_criteria", "constraints", "return_conditions")}}


class HeadPlanner:
    """Objective planning from session evidence and feedback; no execution or retrieval."""

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
        observation_summary: str = ""
        search_trace: List[Dict[str, Any]] = field(default_factory=list)

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
                    "plus one correction for invalid output. Write assessment prose freely, without prescribed labels. "
                    "Use Status: need_more_thinking to continue without JSON, or Status: ready "
                    "followed by exactly one final JSON object using this phase's field contract:\n" +
                    field_contract(schema) +
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
                    f"Correct the output: {exc}. Use free-form assessment prose as useful. "
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
                "Continue in free-form prose as useful. Emit final JSON only after Status: ready."))])
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

    def _plan_objective(self, *, target: TargetSpec, operator_context: Dict[str, Any],
                        working_memory: Dict[str, Any]) -> PlannerDecision:
        available = {"context", *working_memory["history_index"]["attempt_refs"]}
        available.update(item["evidence_ref"] for item in working_memory["verified_facts"]["findings"])
        trace = [{"phase": "plan_objective", "thinking_steps": []}]

        def validate(plan: ObjectivePlan) -> None:
            if plan.decision == "continue":
                self._check_refs(plan.evidence_refs, available)

        try:
            plan = self._structured("plan_objective", {
                "working_memory": working_memory, "available_evidence_refs": sorted(available),
            }, ObjectivePlan, validate, trace_sink=trace[0]["thinking_steps"])
            trace[0].update(plan.model_dump())
            summaries = [step["assessment"] for step in trace[0]["thinking_steps"]
                         if step.get("accepted") and step.get("assessment")]
            if plan.decision == "stop":
                return self.PlannerDecision(None, summaries, True, plan.stop_reason,
                                            [plan.memory_note], search_trace=trace)
            handoff = ObservationHandoff(
                plan.observation_summary, plan.objective, target, self._context(operator_context),
                **{name: getattr(plan, name) for name in ("evidence_refs", "verified_facts", "hypotheses",
                                                         "success_criteria", "constraints", "return_conditions")})
            handoff.constraints = list(dict.fromkeys([
                *operator_context.get("constraints", []), *handoff.constraints]))
            summaries.extend([plan.observation_summary, f"Objective: {plan.objective}"])
            return self.PlannerDecision(handoff, summaries, task_notes=[plan.memory_note],
                                        observation_summary=plan.observation_summary, search_trace=trace)
        except Exception as exc:
            trace[0]["error"] = str(exc)
            return self.PlannerDecision(None, [f"Head planning failed: {exc}"], True,
                                        "Head planning unavailable; no objective delegated.", search_trace=trace)

    def plan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
             max_tasks: int = 6, working_memory: Dict[str, Any] | None = None) -> PlannerDecision:
        memory = working_memory if working_memory is not None else build_working_memory(
            session_id="", user_goal=user_goal, target=target.model_dump(exclude_none=True),
            operator_context=operator_context)
        return self._plan_objective(target=target, operator_context=operator_context, working_memory=memory)

    def replan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
               working_memory: Dict[str, Any]) -> PlannerDecision:
        return self._plan_objective(target=target, operator_context=operator_context, working_memory=working_memory)
