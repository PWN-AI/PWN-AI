"""ReAct (Reason + Act) agent for PwnAI.

Implements a two-phase ReAct pattern:

Phase 1 — the LLM reasons about the task, optionally requesting
retrieval via Action/Action Input. Model-generated observations are
not trusted: requested retrieval must be supplied by the real database.

Phase 2 — if the model requested retrieval, we execute it and feed observations
back as a continuation message, and let the LLM finish with a
second call.  This handles models that pause after requesting
retrieval instead of self-generating the observation.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from typing import Any, List, Optional, Tuple

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

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


# Actions the LLM can invoke during reasoning
AVAILABLE_ACTIONS = {
    "retrieve_tool_info": "Search for tool documentation, arguments, and usage patterns. Input should be a natural language query about what you need to know.",
    "retrieve_args": "Get detailed argument documentation for a specific tool. Input format: 'tool_name: query'. Example: 'sql_injection: dump database flags'",
    "retrieve_all_tool_docs": "Get ALL documentation for a specific tool. Input should be the tool name. Example: 'sql_injection'",
    "final_answer": "Submit your final JSON output. Input should be the complete JSON response.",
}

RETRIEVAL_ACTIONS = {"retrieve_tool_info", "retrieve_args", "retrieve_all_tool_docs"}


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


def _process_steps(
    steps: List[ReActStep],
    retriever: ToolRetriever,
    label: str,
    retrieved_contexts: List[str],
) -> Tuple[Optional[str], List[Tuple[ReActStep, str]]]:
    """Walk parsed steps: log thoughts, execute retrievals, find final_answer.

    Returns (final_output_or_None, list_of_(step, observation)_pairs).
    """
    final_output: Optional[str] = None
    retrieval_results: List[Tuple[ReActStep, str]] = []

    for step in steps:
        if step.thought:
            print(f"[{label}] Thought: {step.thought}")

        if step.action == "final_answer":
            final_output = step.action_input
            print(f"[{label}] Final answer captured.")

        elif step.action in RETRIEVAL_ACTIONS:
            try:
                observation = _execute_retrieval(step, retriever, label)
            except Exception as exc:
                print(f"[{label}] Retrieval failed ({step.action}): {exc}", file=sys.stderr)
                observation = f"[retrieval unavailable: {exc}]"
            retrieved_contexts.append(observation)
            retrieval_results.append((step, observation))

    return final_output, retrieval_results


def run_react_loop(
    *,
    llm: Any,
    system_prompt: str,
    user_message: str,
    retriever: ToolRetriever,
    label: str = "ReAct",
    initial_query: Optional[str] = None,
    initial_tool_filter: Optional[str] = None,
) -> ReActResult:
    """Execute a ReAct flow with RAG retrieval (up to 2 LLM calls).

    Phase 1: The LLM reasons about the task, optionally requesting
    retrieval. A final answer without retrieval can return immediately.

    Phase 2: If the model requested retrieval, we feed real observations
    back, and let the model finish with a second call.

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
    all_steps: List[ReActStep] = []
    total_calls = 0

    # Ground the first answer even if a model returns plain JSON without
    # requesting a textual ReAct action. Further retrieval remains available.
    if initial_query:
        if initial_tool_filter:
            context = retriever.retrieve_for_tool(initial_tool_filter, initial_query, top_k=4)
        else:
            context = retriever.retrieve(initial_query, top_k=4)
        retrieved_contexts.append(context)
        full_system += "\n\nInitial retrieved tool documentation (reference data):\n" + context
        print(f"[{label}] Initial PostgreSQL documentation retrieved.")

    # ── Phase 1: Initial LLM call ──────────────────────────────────
    print(f"\n[{label}] Reasoning...")

    try:
        response = llm.invoke([
            SystemMessage(content=full_system),
            HumanMessage(content=user_message),
        ])
        text = _extract_message_text(getattr(response, "content", response))
        total_calls += 1
    except Exception as exc:
        print(f"[{label}] LLM call failed: {exc}", file=sys.stderr)
        return ReActResult(final_output="{}", steps=[], total_llm_calls=1)

    # Show full Phase 1 output
    print(f"\n{'='*60}")
    print(f"[{label}] Phase 1 output")
    print(f"{'='*60}")
    print(text)
    print(f"{'='*60}")

    steps = _parse_all_react_steps(text)
    all_steps.extend(steps)

    final_output, retrieval_results = _process_steps(
        steps, retriever, label, retrieved_contexts,
    )

    if final_output and not retrieval_results:
        return ReActResult(
            final_output=final_output,
            steps=all_steps,
            total_llm_calls=total_calls,
            retrieved_context=retrieved_contexts,
        )

    # ── Phase 2: Feed real observations back ───────────────────────
    # Feed actual retrievals back even if the model already drafted an answer.
    if retrieval_results:
        print(f"\n[{label}] Feeding actual database retrieval results back...")

        observation_block = "\n\n".join(
            f"Observation (for query \"{step.action_input}\"):\n{obs}"
            for step, obs in retrieval_results
        )
        continuation = (
            f"Here are the actual results from your retrieval request(s):\n\n"
            f"{observation_block}\n\n"
            f"Now use the retrieved information to produce your final answer.\n"
            f"Respond ONLY with:\n"
            f"Thought: [your reasoning using the retrieved information]\n"
            f"Action: final_answer\n"
            f"Action Input: [your complete JSON output]"
        )

        try:
            response2 = llm.invoke([
                SystemMessage(content=full_system),
                HumanMessage(content=user_message),
                AIMessage(content=text),
                HumanMessage(content=continuation),
            ])
            text2 = _extract_message_text(getattr(response2, "content", response2))
            total_calls += 1

            print(f"\n{'='*60}")
            print(f"[{label}] Phase 2 output")
            print(f"{'='*60}")
            print(text2)
            print(f"{'='*60}")

            steps2 = _parse_all_react_steps(text2)
            all_steps.extend(steps2)

            final_output2, retrieval_results2 = _process_steps(
                steps2, retriever, label, retrieved_contexts,
            )

            if final_output2 and not retrieval_results2:
                return ReActResult(
                    final_output=final_output2,
                    steps=all_steps,
                    total_llm_calls=total_calls,
                    retrieved_context=retrieved_contexts,
                )

            # Try JSON extraction from Phase 2 text
            # A newly requested retrieval has not been shown to the model yet.
            # At the two-call limit, do not accept an ungrounded draft.
            json_candidate = None if retrieval_results2 else _try_extract_json_from_text(text2)
            if json_candidate:
                print(f"[{label}] JSON extracted from phase 2 output.")
                return ReActResult(
                    final_output=json_candidate,
                    steps=all_steps,
                    total_llm_calls=total_calls,
                    retrieved_context=retrieved_contexts,
                )
        except Exception as exc:
            print(f"[{label}] Phase 2 LLM call failed: {exc}", file=sys.stderr)

    # Do not accept a draft based on hallucinated observations when retrieval
    # was requested but the grounded continuation failed.
    if retrieval_results:
        return ReActResult(
            final_output="{}", steps=all_steps, total_llm_calls=total_calls,
            retrieved_context=retrieved_contexts,
        )

    # ── Fallback: extract JSON from Phase 1 text ──────────────────
    print(f"[{label}] No final_answer found. Attempting JSON extraction from raw output...")
    json_candidate = _try_extract_json_from_text(text)
    if json_candidate:
        print(f"[{label}] JSON extracted from raw output.")
        return ReActResult(
            final_output=json_candidate,
            steps=all_steps,
            total_llm_calls=total_calls,
            retrieved_context=retrieved_contexts,
        )

    print(f"[{label}] Could not extract any usable output.")
    return ReActResult(
        final_output="{}",
        steps=all_steps,
        total_llm_calls=total_calls,
        retrieved_context=retrieved_contexts,
    )


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
