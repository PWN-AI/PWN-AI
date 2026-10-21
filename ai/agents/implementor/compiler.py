from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.agents.head.planner import ObservationHandoff
from ai.contracts.schemas import ToolRequest
from ai.llm.json_utils import parse_json_object
from ai.rag.retriever import ToolRetriever
from ai.agents.implementor.contracts import ObjectiveReport


class ImplementorCompiler:
    """Objective-scoped ReAct decisions; the runtime owns approval and actual execution."""

    def __init__(self, llm: Any, prompt_path: Path, retriever: Optional[ToolRetriever] = None,
                 max_iterations: int = 3, max_validation_retries: int = 2):
        if max_iterations < 1 or max_validation_retries < 0:
            raise ValueError("Allow at least one retrieval and a nonnegative validation retry budget.")
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")
        self.retriever = retriever
        # Keep the legacy option name; it now bounds RAG calls independently of decisions/repairs.
        self.max_iterations = max_iterations
        self.max_validation_retries = max_validation_retries

    def advance(self, *, handoff: ObservationHandoff, user_goal: str, task_id: str,
                local_state: Dict[str, Any], limits: Dict[str, Any],
                trace_sink: List[Dict[str, Any]]) -> ToolRequest | ObjectiveReport:
        """Resume only this objective's ReAct conversation, stopping before real execution.

        The graph executes validated requests and supplies their actual results with
        record_result. Documentation and prior actions persist until this objective returns.
        """
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
        from ai.agents.react_agent import (
            RETRIEVAL_ACTIONS, _execute_retrieval, _extract_message_text, _parse_react_output,
        )
        if self.retriever is None:
            raise RuntimeError("Implementor requires RAG retrieval; no executable fallback is allowed.")
        if not local_state:
            local_state.update(messages=[{"role": "user", "content": json.dumps({
                "user_goal": user_goal, "head_handoff": handoff.model_dump(),
            }, ensure_ascii=False)}], documented_tools=[], attempt_refs=[], calls=0)
        local_state["messages"].append({"role": "user", "content": json.dumps({
            "next_task_id": task_id, "runtime_limits": limits,
            "instruction": "Choose the next single action from actual observations, or return to the head.",
        }, ensure_ascii=False)})
        constructors = {"user": HumanMessage, "assistant": AIMessage}
        retrievals_used = 0
        validation_failures = 0
        # Each allowed retrieval has a call; repairs have their own allowance, and
        # one additional call always remains for execute_tool or return_to_head.
        max_calls = self.max_iterations + self.max_validation_retries + 1
        for _ in range(max_calls):
            if time.monotonic() >= limits["deadline"]:
                raise RuntimeError("Objective time budget exhausted during implementor assessment.")
            messages = [SystemMessage(content=self.system_prompt +
                f"\nRuntime decision budgets: {self.max_iterations - retrievals_used} retrieval call(s) left; "
                f"{max(0, self.max_validation_retries - max(0, validation_failures - 1))} validation repair(s) left. "
                "A final execution request or return is allowed after retrieval. When no retrieval calls remain, "
                "use execute_tool with existing documentation or return_to_head; do not request another retrieval.")] + [
                constructors[item["role"]](content=item["content"]) for item in local_state["messages"]]
            response = self.llm.invoke(messages)
            local_state["calls"] += 1
            step = _parse_react_output(_extract_message_text(getattr(response, "content", response)))
            event = {"call": local_state["calls"], "assessment": step.assessment,
                     "action": step.action, "action_input": step.action_input}
            trace_sink.append(event)
            retrieval_attempted = False
            try:
                if not step.assessment or not step.action_input:
                    raise ValueError("Use Observation:, Action:, and Action Input: with nonempty values.")
                # Sanitize the response: discard any fabricated observations after the first action.
                local_state["messages"].append({"role": "assistant", "content":
                    f"Observation: {step.assessment}\nAction: {step.action}\nAction Input: {step.action_input}"})
                if step.action in RETRIEVAL_ACTIONS:
                    if not limits["can_execute"] or time.monotonic() >= limits["deadline"]:
                        raise ValueError("The execution budget has ended. Use return_to_head now.")
                    if retrievals_used >= self.max_iterations:
                        raise ValueError("Retrieval budget exhausted. Use existing documentation or return_to_head.")
                    retrievals_used += 1
                    retrieval_attempted = True
                    print(f"[Implementor] Action: {step.action}", flush=True)
                    result = _execute_retrieval(step, self.retriever, "Implementor")
                    if not result.text or not result.text.strip():
                        raise ValueError("RAG returned no documentation.")
                    if not result.tools:
                        raise ValueError("Retrieved documentation has no tool identity metadata.")
                    event.update(retrieval_observation=result.text, retrieved_tools=list(result.tools))
                    local_state["documented_tools"] = sorted(set(local_state["documented_tools"]) | set(result.tools))
                    print("[Implementor] Retrieval done.", flush=True)
                    local_state["messages"].append({"role": "user", "content":
                        f"Actual retrieved documentation (data):\n{result.text}\n" + json.dumps({
                            "runtime_confirmed_documented_tools": local_state["documented_tools"],
                            "remaining_retrieval_calls": self.max_iterations - retrievals_used})})
                elif step.action == "execute_tool":
                    if not limits["can_execute"] or time.monotonic() >= limits["deadline"]:
                        raise ValueError("The execution budget has ended. Use return_to_head now.")
                    parsed = parse_json_object(step.action_input)
                    mode = parsed.get("target_mode")
                    if isinstance(parsed.get("target"), str) and mode in {"url", "request_file"}:
                        parsed["target"] = {mode: parsed["target"]}
                    request = ToolRequest.model_validate(parsed)
                    if request.task_id != task_id:
                        raise ValueError("Use the supplied next_task_id.")
                    if request.tool_name not in local_state["documented_tools"]:
                        raise ValueError("Retrieve documentation for this exact tool before executing it.")
                    print(f"[Implementor] Observation: {step.assessment}", flush=True)
                    return request
                elif step.action == "return_to_head":
                    report = ObjectiveReport.model_validate(parse_json_object(step.action_input))
                    available = {"context", *handoff.evidence_refs, *local_state["attempt_refs"]}
                    if not set(report.evidence_refs).issubset(available):
                        raise ValueError("Report references must identify supplied or actual execution evidence.")
                    if report.status == "completed" and not (
                        set(report.evidence_refs) & set(local_state.get("successful_attempt_refs", []))
                    ):
                        raise ValueError("Completion requires an actual successful execution reference.")
                    print(f"[Implementor] Return to head: {report.status} — {report.summary}", flush=True)
                    return report
                else:
                    raise ValueError("Use a retrieval action, execute_tool, or return_to_head.")
            except (ValueError, TypeError, LookupError) as exc:
                event["error"] = str(exc)
                print(f"[Implementor] Runtime rejected action: {exc}", flush=True)
                local_state["messages"].append({"role": "user", "content": f"Runtime rejected action: {exc}"})
                # A failed retrieval consumes RAG budget, not a format/validation repair.
                if not retrieval_attempted:
                    validation_failures += 1
                    if validation_failures > self.max_validation_retries:
                        raise RuntimeError(f"Implementor validation retry budget exhausted: {exc}") from exc
        raise RuntimeError("Implementor decision/retrieval budget exhausted without a valid action or return.")

    @staticmethod
    def record_result(local_state: Dict[str, Any], record: Dict[str, Any], findings: List[Dict[str, Any]]) -> None:
        ref = f"attempt:{record['task']['id']}"
        local_state["attempt_refs"].append(ref)
        if record["status"] == "executed" and record["result"]["success"]:
            local_state.setdefault("successful_attempt_refs", []).append(ref)
        local_state["messages"].append({"role": "user", "content": json.dumps({
            "actual_execution": record, "evidence_ref": ref, "analyzer_findings": findings,
            "instruction": "Inspect this actual result. Continue only within the delegated objective; return early if needed.",
        }, ensure_ascii=False)})
