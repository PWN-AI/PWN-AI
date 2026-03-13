"""ReAct (Reason + Act) agent for PwnAI.

Implements a single-call ReAct pattern where the LLM produces its
entire reasoning trace in one generation — interleaving Thought,
Action, and self-generated Observation steps — culminating in a
final_answer action.

The module parses ALL steps from that output, executes any retrieval
actions (for logging/tracking), and returns the final_answer the
model already produced.  This keeps it to one LLM call per invocation.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from ai.rag.retriever import ToolRetriever


@dataclass
class ReActStep:
    """One step in the ReAct loop."""
    thought: str = ""
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


# Available actions the LLM can invoke during reasoning
AVAILABLE_ACTIONS = {
    "retrieve_tool_info": "Search for tool documentation, arguments, and usage patterns. Input should be a natural language query about what you need to know.",
    "retrieve_args": "Get detailed argument documentation for a specific tool. Input format: 'tool_name: query'. Example: 'sql_injection: dump database flags'",
    "retrieve_all_tool_docs": "Get ALL documentation for a specific tool. Input should be the tool name. Example: 'sql_injection'",
    "final_answer": "Submit your final JSON output. Input should be the complete JSON response.",
}


def _build_react_instructions() -> str:
    """Build the ReAct format instructions to prepend to prompts."""
    action_list = "\n".join(
        f"  - {name}: {desc}" for name, desc in AVAILABLE_ACTIONS.items()
    )
    return f"""
You have access to a tool knowledge retrieval system during your reasoning.
Use the following format to think step-by-step and retrieve information when needed:

Thought: [Your reasoning about what to do next]
Action: [One of the available actions below]
Action Input: [Input for the action]

After each Action, you will receive an Observation with the results.
Continue the Thought/Action/Observation cycle until you have enough information.
Then provide your final answer:

Thought: [Final reasoning]
Action: final_answer
Action Input: [Your complete JSON output]

Available Actions:
{action_list}

IMPORTANT RULES:
1. You SHOULD retrieve tool documentation when you need to know exact argument names, types, or valid values.
2. You can make multiple retrieval calls if needed — each builds on previous observations.
3. After retrieving, use the information to construct precise, complete arguments.
4. Your final_answer Action Input MUST be valid JSON.
5. Do NOT hallucinate argument names — if unsure, retrieve first.
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
    """Parse LLM output for the FIRST ReAct Thought/Action/Action Input.

    Only used as a fallback — prefer _parse_all_react_steps() for full
    output parsing.
    """
    step = ReActStep()

    thought_match = re.search(
        r"Thought\s*:\s*(.*?)(?=\nAction\s*:|$)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if thought_match:
        step.thought = thought_match.group(1).strip()

    action_match = re.search(
        r"Action\s*:\s*([\w_]+)",
        text, re.IGNORECASE,
    )
    if action_match:
        step.action = action_match.group(1).strip()

    input_match = re.search(
        r"Action\s+Input\s*:\s*(.*?)(?=\nThought\s*:|\nObservation\s*:|$)",
        text, re.DOTALL | re.IGNORECASE,
    )
    if input_match:
        step.action_input = input_match.group(1).strip()

    return step


def _parse_all_react_steps(text: str) -> List[ReActStep]:
    """Parse ALL ReAct steps from a single LLM generation.

    Models often produce the entire Thought/Action/Observation/...
    /final_answer trace in one shot.  This function extracts every
    step so we can execute retrieval actions AND capture the
    final_answer from the same output.

    Strategy: find every ``Action:`` occurrence and work backwards to
    grab the preceding ``Thought:`` (if any) and forwards to grab the
    ``Action Input:`` and ``Observation:``.  This avoids losing actions
    that appear without a ``Thought:`` prefix.
    """
    steps: List[ReActStep] = []

    # Find all Action positions
    action_iter = list(re.finditer(
        r"Action\s*:\s*([\w_]+)",
        text, re.IGNORECASE,
    ))

    if not action_iter:
        # No actions at all — try to build a single step from any Thought
        thought_match = re.search(
            r"Thought\s*:\s*(.*)",
            text, re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            steps.append(ReActStep(thought=thought_match.group(1).strip()))
        return steps

    for idx, action_match in enumerate(action_iter):
        step = ReActStep()
        step.action = action_match.group(1).strip()

        # Determine the text region for this action:
        # from the previous action's end (or start of text) to the next action's start (or end of text)
        region_start = action_iter[idx - 1].end() if idx > 0 else 0
        region_end = action_iter[idx + 1].start() if idx + 1 < len(action_iter) else len(text)
        region = text[region_start:region_end]

        # Look for Thought: before this Action within the region
        thought_match = re.search(
            r"Thought\s*:\s*(.*?)(?=\nAction\s*:|$)",
            region, re.DOTALL | re.IGNORECASE,
        )
        if thought_match:
            step.thought = thought_match.group(1).strip()

        # Look for Action Input: after this Action within the region
        # Use text from the Action match position onwards
        after_action = text[action_match.end():region_end]
        input_match = re.search(
            r"Input\s*:\s*(.*?)(?=\nThought\s*:|\nObservation\s*:|\nAction\s*:|$)",
            after_action, re.DOTALL | re.IGNORECASE,
        )
        if input_match:
            step.action_input = input_match.group(1).strip()

        # Look for Observation: in the region after action input
        obs_match = re.search(
            r"Observation\s*:\s*(.*?)(?=\nThought\s*:|\nAction\s*:|$)",
            after_action, re.DOTALL | re.IGNORECASE,
        )
        if obs_match:
            step.observation = obs_match.group(1).strip()

        steps.append(step)

    return steps


def _try_extract_json_from_text(text: str) -> Optional[str]:
    """Attempt to extract JSON from raw text that doesn't follow ReAct format.

    Fallback for models that skip the ReAct format and just output JSON.
    """
    # Try to find a JSON object in the text
    brace_depth = 0
    start = -1
    for i, ch in enumerate(text):
        if ch == '{':
            if brace_depth == 0:
                start = i
            brace_depth += 1
        elif ch == '}':
            brace_depth -= 1
            if brace_depth == 0 and start >= 0:
                candidate = text[start:i + 1]
                try:
                    json.loads(candidate)
                    return candidate
                except json.JSONDecodeError:
                    start = -1
    return None


def run_react_loop(
    *,
    llm: Any,
    system_prompt: str,
    user_message: str,
    retriever: ToolRetriever,
    label: str = "ReAct",
) -> ReActResult:
    """Execute a single-call ReAct flow with RAG retrieval.

    The LLM produces the entire reasoning trace in one generation —
    Thought → Action → (self-generated Observation) → … → final_answer.
    This function parses ALL steps from that output, executes any
    retrieval actions (for logging/tracking), and returns the
    final_answer the model already produced.

    Args:
        llm: LangChain-compatible LLM (with .invoke() method)
        system_prompt: Base system prompt for the agent
        user_message: The user/task message to reason about
        retriever: ToolRetriever for RAG queries
        label: Label for logging

    Returns:
        ReActResult with the final output and reasoning trace
    """
    react_instructions = _build_react_instructions()
    full_system = system_prompt + "\n\n" + react_instructions
    retrieved_contexts: List[str] = []

    print(f"\n[{label}] Reasoning...")

    try:
        response = llm.invoke([
            SystemMessage(content=full_system),
            HumanMessage(content=user_message),
        ])
        text = _extract_message_text(getattr(response, "content", response))
    except Exception as exc:
        print(f"[{label}] LLM call failed: {exc}", file=sys.stderr)
        return ReActResult(
            final_output="{}",
            steps=[],
            total_llm_calls=1,
            retrieved_context=[],
        )

    # Parse all steps from the full output
    steps = _parse_all_react_steps(text)

    # Walk through steps: execute retrieval actions, find final_answer
    final_output = None
    for step in steps:
        if step.thought:
            print(f"[{label}] Thought: {step.thought[:200]}...")

        if step.action == "final_answer":
            final_output = step.action_input
            print(f"[{label}] Final answer captured.")

        elif step.action in ("retrieve_tool_info", "retrieve_args", "retrieve_all_tool_docs"):
            # Execute retrieval for logging/tracking
            observation = _execute_retrieval(step, retriever, label)
            retrieved_contexts.append(observation)

    if final_output:
        return ReActResult(
            final_output=final_output,
            steps=steps,
            total_llm_calls=1,
            retrieved_context=retrieved_contexts,
        )

    # No final_answer step found — try extracting JSON from raw text
    print(f"[{label}] No final_answer action found. Extracting JSON from output...")
    json_candidate = _try_extract_json_from_text(text)
    if json_candidate:
        print(f"[{label}] JSON extracted from raw output.")
        return ReActResult(
            final_output=json_candidate,
            steps=steps,
            total_llm_calls=1,
            retrieved_context=retrieved_contexts,
        )

    # Ultimate fallback
    print(f"[{label}] Could not extract any usable output.")
    return ReActResult(
        final_output="{}",
        steps=steps,
        total_llm_calls=1,
        retrieved_context=retrieved_contexts,
    )


def _execute_retrieval(step: ReActStep, retriever: ToolRetriever, label: str) -> str:
    """Execute a retrieval action and return the observation."""
    if step.action == "retrieve_tool_info":
        print(f"[{label}] Retrieving: {step.action_input[:100]}...")
        return retriever.retrieve(step.action_input, top_k=4)

    elif step.action == "retrieve_args":
        parts = step.action_input.split(":", 1)
        if len(parts) == 2:
            tool_name = parts[0].strip()
            query = parts[1].strip()
            print(f"[{label}] Retrieving args for {tool_name}: {query[:80]}...")
            return retriever.retrieve_for_tool(tool_name, query, top_k=3)
        return retriever.retrieve(step.action_input, top_k=3)

    elif step.action == "retrieve_all_tool_docs":
        tool_name = step.action_input.strip()
        print(f"[{label}] Retrieving all docs for: {tool_name}")
        return retriever.retrieve_all_for_tool(tool_name)

    return ""
