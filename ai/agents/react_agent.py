"""Stateless observation/action loop with runtime-owned RAG observations."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from ai.rag.retriever import ToolRetriever


@dataclass
class ReActStep:
    """One step in the ReAct loop."""
    assessment: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""


@dataclass
class ReActResult:
    """Final result from a ReAct loop run."""
    final_output: str
    steps: List[ReActStep] = field(default_factory=list)
    total_llm_calls: int = 0
    retrieved_context: List[str] = field(default_factory=list)


# Actions the LLM can invoke during reasoning
AVAILABLE_ACTIONS = {
    "retrieve_tool_info": "Search for tool documentation, arguments, and usage patterns. Input should be a natural language query about what you need to know.",
    "retrieve_args": "Get detailed argument documentation for a specific tool. Input format: 'tool_name: query'. Example: 'sql_injection: dump database flags'",
    "retrieve_all_tool_docs": "Get ALL documentation for a specific tool. Input should be the tool name. Example: 'sql_injection'",
    "final_answer": "Submit your final JSON output. Input should be the complete JSON response.",
}

RETRIEVAL_ACTIONS = {"retrieve_tool_info", "retrieve_args", "retrieve_all_tool_docs"}


def _build_react_instructions() -> str:
    actions = "\n".join(f"- {name}: {description}" for name, description in AVAILABLE_ACTIONS.items())
    return f"""
Use this protocol:
Observation: concise assessment of supplied facts and documentation needed
Action: an available action
Action Input: the action input

Available actions:
{actions}

Request documentation before constructing a request. Stop after ONE action.
The runtime supplies actual retrieval observations in a later message.
Never invent retrieval observations. After receiving actual documentation,
use Action: final_answer with the complete JSON request in Action Input.
"""


def _extract_message_text(content: Any) -> str:
    """Extract text from various LLM response formats."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                chunks.append(str(item["text"]))
            else:
                chunks.append(str(item))
        return "\n".join(chunks)
    return str(content)


def _parse_react_output(text: str) -> ReActStep:
    """Read the first action only, discarding model-invented continuations."""
    action = re.search(r"(?im)^Action:\s*([\w_]+)\s*$", text)
    if not action:
        return ReActStep()
    assessment = re.search(r"(?is)^Observation:\s*(.+)", text[:action.start()].strip())
    after_action = text[action.end():]
    action_input = re.search(
        r"(?ims)^Action Input:\s*(.*?)(?=^Observation:|^Thought:|^Action:|\Z)", after_action)
    return ReActStep(
        assessment=assessment.group(1).strip() if assessment else "",
        action=action.group(1).lower(),
        action_input=action_input.group(1).strip() if action_input else "",
    )


def run_react_loop(
    *, llm: Any, system_prompt: str, user_message: str,
    retriever: ToolRetriever, label: str = "ReAct", max_iterations: int = 3,
    trace_sink: Optional[List[Dict[str, Any]]] = None,
) -> ReActResult:
    """Execute actual retrieval before accepting a final answer.

    Only the first action in a generation executes. Model-generated observations
    or answers following retrieval are discarded. Messages persist only here.
    """
    if max_iterations < 2:
        raise ValueError("max_iterations must allow retrieval and a final response (>=2).")
    messages = [SystemMessage(content=system_prompt + "\n" + _build_react_instructions() +
                              f"\nYou have at most {max_iterations} model calls, including the final request."),
                HumanMessage(content=user_message)]
    contexts: List[str] = []
    steps: List[ReActStep] = []
    for call in range(1, max_iterations + 1):
        response = llm.invoke(messages)
        text = _extract_message_text(getattr(response, "content", response))
        step = _parse_react_output(text)
        if not step.assessment or not step.action_input:
            if trace_sink is not None:
                trace_sink.append({"call": call, "assessment": step.assessment,
                                   "action": step.action, "action_input": step.action_input,
                                   "error": "Missing observation or action input."})
            messages.append(HumanMessage(content="Use Observation:, Action:, and Action Input: with nonempty values."))
            continue
        if step.action in RETRIEVAL_ACTIONS:
            event = {"call": call, "assessment": step.assessment, "action": step.action,
                     "action_input": step.action_input}
            if trace_sink is not None:
                trace_sink.append(event)
            print(f"[{label}] Action: {step.action}", flush=True)
            try:
                observation = _execute_retrieval(step, retriever, label)
                if not observation or not observation.strip():
                    raise RuntimeError("RAG returned no documentation.")
            except Exception as exc:
                event["error"] = str(exc)
                raise
            step.observation = observation
            event["retrieval_observation"] = observation
            print(f"[{label}] Retrieval done.", flush=True)
            contexts.append(observation)
            steps.append(step)
            messages.extend([
                AIMessage(content=f"Observation: {step.assessment}\nAction: {step.action}\nAction Input: {step.action_input}"),
                HumanMessage(content=f"Observation: {observation}\nUse this actual documentation to decide the next action."),
            ])
        elif step.action == "final_answer" and contexts:
            if trace_sink is not None:
                trace_sink.append({"call": call, "assessment": step.assessment,
                                   "action": step.action, "action_input": step.action_input})
            print(f"[{label}] Observation: {step.assessment}", flush=True)
            steps.append(step)
            return ReActResult(step.action_input, steps, call, contexts)
        else:
            if trace_sink is not None:
                trace_sink.append({"call": call, "assessment": step.assessment,
                                   "action": step.action, "action_input": step.action_input,
                                   "error": "Unsupported action or final request before real retrieval."})
            messages.append(HumanMessage(content=(
                "Observe the handoff, then request RAG using Action and Action Input. "
                "You must receive real documentation before final_answer. "
                "Only one action per response; never generate retrieval observations.")))
    raise RuntimeError(f"{label} did not produce a grounded request within {max_iterations} calls.")


def _execute_retrieval(step: ReActStep, retriever: ToolRetriever, label: str) -> str:
    """Execute a retrieval action and return the observation."""
    if step.action == "retrieve_tool_info":
        print(f"[{label}] Retrieving: {step.action_input}...")
        return retriever.retrieve(step.action_input, top_k=4)

    elif step.action == "retrieve_args":
        parts = step.action_input.split(":", 1)
        if len(parts) == 2:
            tool_name = parts[0].strip()
            query = parts[1].strip()
            print(f"[{label}] Retrieving args for {tool_name}: {query}...")
            return retriever.retrieve_for_tool(tool_name, query, top_k=3)
        return retriever.retrieve(step.action_input, top_k=3)

    elif step.action == "retrieve_all_tool_docs":
        tool_name = step.action_input.strip()
        print(f"[{label}] Retrieving all docs for: {tool_name}")
        return retriever.retrieve_all_for_tool(tool_name)

    return ""
