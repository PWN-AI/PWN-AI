from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict

from ai.contracts.schemas import ToolRequest
from ai.integrations.base import ToolAdapter, load_module_from_file, utc_now


class SqlInjectionAdapter(ToolAdapter):
    """Thin passthrough adapter — the LLM controls ALL sqlmap flags.

    The adapter's only responsibilities:
    1. Set target (url / request_file) from the request.
    2. Force --batch to prevent interactive hangs.
    3. Pass every other option through untouched from request.args["options"].
    4. Pass max_duration to the wrapper so it can enforce timeout.
    """

    DEFAULT_TIMEOUT = 300  # seconds

    def __init__(self, project_root: Path):
        super().__init__(project_root)
        self.module_path = self.project_root / "sql_injection" / "sql.py"

    def execute(self, request: ToolRequest):
        started = utc_now()
        max_duration = int(request.args.get("max_duration", self.DEFAULT_TIMEOUT))

        try:
            sql_mod = load_module_from_file("pwnai_sqlmap_module", self.module_path)
            runner = sql_mod.run_sqlmap_tool

            kwargs: Dict[str, Any] = dict(request.args)
            options = dict(kwargs.get("options", {}))

            # ONLY hard rule: --batch must be True to prevent interactive prompts
            options["--batch"] = True

            kwargs["options"] = options

            # Set target from request
            if request.target_mode == "url":
                kwargs["url"] = request.target.url
            else:
                kwargs["request_file"] = request.target.request_file
            # Keep tool execution anchored to repo root (stable relative paths).
            kwargs["cwd"] = str(self.project_root)

            # Remove meta-keys that are not sqlmap args
            kwargs.pop("max_duration", None)
            kwargs.pop("quick_test", None)

            # Pass timeout to the wrapper so it enforces it via Popen
            kwargs["max_runtime_seconds"] = max_duration

            print(f"[SQL Injection] Action: {request.action}")
            print(f"[SQL Injection] Target: {request.target.url or request.target.request_file}")
            print(f"[SQL Injection] Options (LLM-controlled): {options}")
            print(f"[SQL Injection] Timeout cap: {max_duration}s")

            result = runner(**kwargs)

            raw = {
                "command": result.command,
                "returncode": result.returncode,
            }
            success = result.returncode == 0
            timed_out = result.returncode == -1  # our sentinel for timeout

            duration = (utc_now() - started).total_seconds()
            print(f"[SQL Injection] Completed in {duration:.1f}s | rc={result.returncode} | success={success}")

            if timed_out:
                print(f"[SQL Injection] TIMEOUT after {max_duration}s")

            return self._build_result(
                request=request,
                started_at=started,
                success=success,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_output=raw,
                error=f"Scan timed out after {max_duration}s." if timed_out else None,
            )

        except Exception as exc:
            print(f"[SQL Injection] ERROR: {exc}")
            return self._build_result(
                request=request,
                started_at=started,
                success=False,
                returncode=1,
                error=str(exc),
                stderr=str(exc),
            ) 

if __name__ == "__main__":
    # Ensure project root is on sys.path when running this file directly
    _project_root = Path(__file__).resolve().parents[2]
    if str(_project_root) not in sys.path:
        sys.path.insert(0, str(_project_root))

    from ai.contracts.schemas import TargetSpec  # noqa: E402 (needed after path fix)

    project_root = _project_root
    adapter = SqlInjectionAdapter(project_root=project_root)

    test_request = ToolRequest(
        task_id="test-01",
        tool_name="sql_injection",
        action="dump",
        target_mode="url",
        target=TargetSpec(url="http://localhost/vulnerabilities/sqli/?id=1&Submit=Submit"),
        args={
            "options": {
                "--dump-all": True,
                "--exclude-sysdbs": True,
                "--cookie": "PHPSESSID=j9iu08d3skqfdjr7ltrn05tct3; security=low",
                "--level": 2,
                "--risk": 1,
            },
            "max_duration": 120,
        },
    )

    print("[Test] Running SQL adapter...")
    result = adapter.execute(test_request)
    print(f"[Test] Success: {result.success}")
    print(f"[Test] Return code: {result.returncode}")
    print(f"[Test] Error: {result.error}")
    print(f"[Test] STDOUT:\n{result.stdout if result.stdout else 'None'}")
    print(f"[Test] STDERR:\n{result.stderr if result.stderr else 'None'}")
