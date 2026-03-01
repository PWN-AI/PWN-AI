from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ai.contracts.schemas import ToolRequest
from ai.integrations.base import ToolAdapter, load_module_from_file, utc_now


class CommandInjectionAdapter(ToolAdapter):
    """Thin passthrough adapter — the LLM controls all commix flags.

    The adapter only maps action to function name and sets the target.
    All other arguments are passed through from request.args.
    """

    ACTION_MAP = {
        "scan": "scan_command_injection",
        "enumerate": "enumerate_target",
        "execute_command": "execute_command",
        "read_remote_file": "read_remote_file",
    }

    def __init__(self, project_root: Path):
        super().__init__(project_root)
        self.module_path = self.project_root / "command_injection" / "cmd.py"

    def execute(self, request: ToolRequest):
        started = utc_now()
        try:
            cmd_mod = load_module_from_file("pwnai_commix_module", self.module_path)
            function_name = self.ACTION_MAP.get(request.action, "scan_command_injection")
            runner = getattr(cmd_mod, function_name)

            kwargs: Dict[str, Any] = dict(request.args)
            if request.target_mode == "url":
                kwargs["url"] = request.target.url
            else:
                kwargs["request_file"] = request.target.request_file

            print(f"[Command Injection] Action: {request.action} -> {function_name}")
            print(f"[Command Injection] Target: {request.target.url or request.target.request_file}")
            print(f"[Command Injection] Args (LLM-controlled): {kwargs}")

            result = runner(**kwargs)
            raw = {
                "command": result.command,
                "returncode": result.returncode,
            }

            duration = (utc_now() - started).total_seconds()
            print(f"[Command Injection] Completed in {duration:.1f}s | rc={result.returncode}")

            return self._build_result(
                request=request,
                started_at=started,
                success=result.returncode == 0,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_output=raw,
            )
        except Exception as exc:
            print(f"[Command Injection] ERROR: {exc}")
            return self._build_result(
                request=request,
                started_at=started,
                success=False,
                returncode=1,
                error=str(exc),
                stderr=str(exc),
            )

