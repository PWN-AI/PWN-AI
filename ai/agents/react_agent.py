"""Shared ReAct parsing and runtime-owned retrieval for the implementor."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, List

from ai.rag.retriever import RetrievedDocumentation, ToolRetriever


@dataclass
class ReActStep:
    """One step in the ReAct loop."""
    assessment: str = ""
    action: str = ""
    action_input: str = ""
    observation: str = ""


RETRIEVAL_ACTIONS = {"retrieve_tool_info", "retrieve_args", "retrieve_all_tool_docs"}


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


def _execute_retrieval(step: ReActStep, retriever: ToolRetriever, label: str) -> RetrievedDocumentation:
    """Execute a retrieval action and return the observation."""
    if step.action == "retrieve_tool_info":
        print(f"[{label}] Retrieving: {step.action_input}...")
        return retriever.retrieve_result(step.action_input, top_k=4)

    elif step.action == "retrieve_args":
        parts = step.action_input.split(":", 1)
        if len(parts) == 2:
            tool_name = parts[0].strip()
            query = parts[1].strip()
            print(f"[{label}] Retrieving args for {tool_name}: {query}...")
            return retriever.retrieve_for_tool_result(tool_name, query, top_k=3)
        return retriever.retrieve_result(step.action_input, top_k=3)

    elif step.action == "retrieve_all_tool_docs":
        tool_name = step.action_input.strip()
        print(f"[{label}] Retrieving all docs for: {tool_name}")
        return retriever.retrieve_all_for_tool_result(tool_name)

    raise ValueError("Unsupported retrieval action.")
