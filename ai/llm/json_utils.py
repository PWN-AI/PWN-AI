from __future__ import annotations

import json
import re
from typing import Any, Dict


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks that qwen3 models emit before JSON."""
    return re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE).strip()


def _extract_first_json_candidate(text: str) -> str:
    # Strip think tags first (qwen3 often wraps reasoning in <think> blocks)
    text = _strip_think_tags(text)

    codeblock_match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.DOTALL | re.IGNORECASE)
    if codeblock_match:
        return codeblock_match.group(1)

    # Use brace-depth matching to find the first complete JSON object.
    # This correctly handles extra trailing braces (e.g. model outputting }}).
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
                return text[start:i + 1]

    raise ValueError("No JSON object detected in model response")


def parse_json_object(text: str) -> Dict[str, Any]:
    candidate = _extract_first_json_candidate(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try fixing common LLM JSON issues: trailing commas
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)  # remove trailing commas
        try:
            return json.loads(fixed)
        except json.JSONDecodeError:
            pass
        # Last resort: replace single quotes used as JSON delimiters
        # (only those adjacent to colons, commas, brackets — not mid-word apostrophes)
        fixed2 = re.sub(r"(?<=[\[,{:\s])'|'(?=[\],}:\s])", '"', fixed)
        return json.loads(fixed2)

