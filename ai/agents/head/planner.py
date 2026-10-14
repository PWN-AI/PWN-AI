from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from ai.agents.react_agent import _extract_message_text
from ai.contracts.schemas import ExecutionResult, Finding, TargetSpec, TaskSpec


@dataclass
class ObservationHandoff:
    """Current context only; persistent session memory stays with the head."""
    observation_summary: str
    next_step: str
    target: TargetSpec
    context: Dict[str, Any]

    def model_dump(self) -> Dict[str, Any]:
        return {
            "observation_summary": self.observation_summary,
            "next_step": self.next_step,
            "target": self.target.model_dump(exclude_none=True),
            "context": self.context,
        }


class HeadPlanner:
    """Observe and retain session notes; never retrieve or generate requests."""

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

    def _observe(self, *, user_goal: str, target: TargetSpec,
                 operator_context: Dict[str, Any], evidence: Dict[str, Any]) -> PlannerDecision:
        # Explicit allowlist keeps historical notes/results out of the implementor.
        context = {key: operator_context[key] for key in (
            "auth_verified", "auth_cookie", "authenticated_page_url",
            "discovered_endpoints", "navigation_structure", "username",
        ) if key in operator_context}
        html = operator_context.get("authenticated_page_html")
        if html:
            context["page_sample"] = str(html)[:3000]
        messages = [SystemMessage(content=self.system_prompt), HumanMessage(content=json.dumps({
            "user_goal": user_goal, "target": target.model_dump(exclude_none=True),
            "current_context": context, "head_memory_and_evidence": evidence,
        }, ensure_ascii=False))]
        # Enforce the prose contract, with one correction attempt; never parse head JSON.
        for attempt in range(2):
            try:
                response = self.llm.invoke(messages)
                text = _extract_message_text(getattr(response, "content", response)).strip()
                pattern = (r"\A((?:Observation:\s*.+?\n)+)Summary:\s*(.+?)\nNext step:\s*(.+?)\n"
                           r"Memory note:\s*(.+?)\nDecision:\s*(continue|stop)\s*\Z")
                match = re.fullmatch(pattern, text, re.DOTALL | re.IGNORECASE)
                if match and not re.search(r"(?im)^\s*(Action|Action Input|final_answer)\s*:", text):
                    observation_block, summary, next_step, note, decision = match.groups()
                    observations = [part.strip() for part in re.findall(
                        r"(?:\A|\n)Observation:\s*(.*?)(?=\nObservation:|\Z)",
                        observation_block.strip(), re.DOTALL | re.IGNORECASE)]
                    # A JSON payload is not an observation-style handoff.
                    if len(observations) < 2 or any(
                        not part or part.lstrip().startswith(("{", "[", "```"))
                        for part in (*observations, summary, next_step, note)
                    ):
                        match = None
                    else:
                        stop = decision.lower() == "stop"
                        return self.PlannerDecision(
                            handoff=None if stop else ObservationHandoff(
                                summary.strip(), next_step.strip(), target, context),
                            thinking_summary=observations + [f"Summary: {summary.strip()}",
                                                             f"Next step: {next_step.strip()}"],
                            should_stop=stop, stop_reason=next_step.strip() if stop else "",
                            task_notes=[note.strip()],
                            observations=observations,
                            observation_summary=summary.strip(),
                        )
                if attempt == 0:
                    messages.append(HumanMessage(content=(
                        "Your response did not follow the prose contract. Use at least two Observation: "
                        "sections, then Summary:, Next step:, Memory note:, Decision: continue or stop. "
                        "Provide concise evidence and decision summaries. No JSON or actions.")))
            except Exception as exc:
                return self.PlannerDecision(None, [f"Head observation failed: {exc}"], True,
                                            "Head observation unavailable; no action generated.")
        return self.PlannerDecision(None, ["Head returned invalid observation format."], True,
                                    "Head observation invalid; no action generated.")

    def plan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
             max_tasks: int = 6) -> PlannerDecision:
        return self._observe(user_goal=user_goal, target=target, operator_context=operator_context,
                             evidence={"stage": "initial", "memory": []})

    def replan(self, *, user_goal: str, target: TargetSpec, operator_context: Dict[str, Any],
               current_task: TaskSpec, current_result: ExecutionResult,
               findings: Sequence[Finding], remaining_tasks: Sequence[TaskSpec],
               prior_notes: Sequence[str] = (), replan_iteration: int = 0,
               max_new_tasks: int = 3, iteration_history: Sequence[Dict[str, Any]] = (),
               execution_history: Sequence[Dict[str, Any]] = (),
               runtime_notes: Sequence[str] = ()) -> PlannerDecision:
        return self._observe(user_goal=user_goal, target=target, operator_context=operator_context,
                             evidence={
                                 "stage": "after_execution", "memory": list(prior_notes),
                                 "iteration": replan_iteration + 1,
                                 "iteration_history": list(iteration_history),
                                 "execution_history": list(execution_history),
                                 "runtime_notes": list(runtime_notes),
                                 "task": current_task.model_dump(),
                                 "result": current_result.model_dump(),
                                 "findings": [finding.model_dump() for finding in findings],
                                 "remaining_tasks": [task.model_dump() for task in remaining_tasks],
                             })
