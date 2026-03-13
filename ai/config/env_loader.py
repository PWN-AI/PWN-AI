from __future__ import annotations

import os
from pathlib import Path
from typing import Tuple


def _parse_env_line(line: str) -> Tuple[str, str] | None:
    text = line.strip()
    if not text or text.startswith("#"):
        return None

    if text.startswith("export "):
        text = text[len("export "):].strip()

    if "=" not in text:
        return None

    key, value = text.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key:
        return None

    # Drop matching surrounding quotes.
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        value = value[1:-1]

    return key, value


def load_project_env(project_root: Path, *, filename: str = ".env", override: bool = False) -> int:
    """Load simple KEY=VALUE pairs from a project-level .env file.

    Returns the number of environment variables set from file.
    """
    env_path = project_root / filename
    if not env_path.exists():
        return 0

    loaded = 0
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        parsed = _parse_env_line(raw_line)
        if parsed is None:
            continue
        key, value = parsed
        if key in os.environ and not override:
            continue
        os.environ[key] = value
        loaded += 1
    return loaded
