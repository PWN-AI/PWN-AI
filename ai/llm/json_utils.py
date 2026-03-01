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

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    raise ValueError("No JSON object detected in model response")


def parse_json_object(text: str) -> Dict[str, Any]:
    candidate = _extract_first_json_candidate(text)
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        # Try fixing common LLM JSON issues: trailing commas, single quotes
        fixed = re.sub(r",\s*([}\]])", r"\1", candidate)  # remove trailing commas
        fixed = fixed.replace("'", '"')  # single to double quotes
        return json.loads(fixed)

