"""Bounded recall of validated feedback scoped to the same target and goal."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai.agents.head.contracts import AttemptReflection


def load_feedback(output_dir: Path, target: dict[str, Any], user_goal: str,
                  *, limit: int = 20) -> list[dict[str, Any]]:
    if limit <= 0:
        return []
    try:
        files = sorted(output_dir.glob("*_feedback.json"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return []
    feedback = []
    for path in files[-10:]:
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(record, dict) or record.get("target") != target or record.get("user_goal") != user_goal:
                continue
            lessons = [AttemptReflection.model_validate(lesson) for lesson in record.get("lessons", [])]
            for reflection in lessons:
                feedback.append({"session_id": str(record.get("session_id", path.stem)),
                                 "reflection": reflection.model_dump()})
        except (OSError, ValueError, TypeError):
            # Invalid or interrupted artifacts are not trusted memory.
            continue
    return feedback[-limit:]
