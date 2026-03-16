from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ai.contracts.schemas import ToolRequest
from ai.integrations.base import ToolAdapter, load_module_from_file, utc_now


# Maps commix CLI flags to Python function keyword arguments.
_FLAG_TO_KWARG = {
    "--cookie": "cookie",
    "--data": "data",
    "-d": "data",
    "-p": "parameter",
    "--technique": "technique",
    "--level": "level",
    "--web-root": "web_root",
    "--tamper": "tamper",
    "--os-cmd": "os_cmd",
    "--url": "url",
    "-u": "url",
    "-r": "request_file",
    "--file-read": "file_path",
    # Enumeration booleans
    "--current-user": "get_current_user",
    "--hostname": "get_hostname",
    "--is-root": "check_root",
    "--is-admin": "check_admin",
    "--sys-info": "get_sys_info",
    "--users": "get_users",
    "--passwords": "get_passwords",
    "--all": "get_all",
}

# Reverse mapping: kwarg name → preferred CLI flag (for extra_options pass-through)
_KWARG_TO_FLAG = {
    "cookie": "--cookie",
    "data": "--data",
    "parameter": "-p",
    "technique": "--technique",
    "level": "--level",
    "web_root": "--web-root",
    "tamper": "--tamper",
    "os_cmd": "--os-cmd",
    "url": "--url",
    "request_file": "-r",
    "file_path": "--file-read",
    "get_current_user": "--current-user",
    "get_hostname": "--hostname",
    "check_root": "--is-root",
    "check_admin": "--is-admin",
    "get_sys_info": "--sys-info",
    "get_users": "--users",
    "get_passwords": "--passwords",
    "get_all": "--all",
    "command": "--os-cmd",
}

# Per-function kwarg aliases: remap kwarg names that differ between functions
_KWARG_ALIASES: Dict[str, Dict[str, str]] = {
    "execute_command": {"os_cmd": "command"},
}

# Flags the adapter silently absorbs (handled automatically by run_commix_tool)
_IGNORED_FLAGS = {"--batch"}

# Valid kwargs accepted by the high-level cmd.py functions
_VALID_KWARGS = {
    "scan_command_injection": {
        "url", "request_file", "data", "cookie", "parameter", "technique",
        "level", "web_root", "tamper", "os_cmd", "extra_options",
        "python_bin", "max_runtime_seconds",
    },
    "enumerate_target": {
        "url", "request_file", "data", "cookie", "parameter", "technique",
        "web_root", "get_current_user", "get_hostname", "check_root",
        "check_admin", "get_sys_info", "get_users", "get_passwords", "get_all",
        "extra_options", "python_bin", "max_runtime_seconds",
    },
    "execute_command": {
        "url", "request_file", "data", "cookie", "parameter", "technique",
        "web_root", "command", "extra_options", "python_bin", "max_runtime_seconds",
    },
    "read_remote_file": {
        "url", "request_file", "data", "cookie", "parameter", "technique",
        "web_root", "file_path", "extra_options", "python_bin", "max_runtime_seconds",
    },
}


class CommandInjectionAdapter(ToolAdapter):
    """Adapter that normalises LLM-produced args into the kwargs expected by
    the cmd.py high-level functions (scan_command_injection, enumerate_target,
    execute_command, read_remote_file).

    Handles three input patterns the LLM might produce:
      1. Flat Python kwargs:  {"cookie": "...", "parameter": "ip", "data": "..."}
      2. options wrapper:     {"options": {"--cookie": "...", "-p": "ip"}}
      3. Mixed CLI flags:     {"--cookie": "...", "-p": "ip", "data": "..."}

    All three are normalised into the correct function kwargs before calling.
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

    @staticmethod
    def _normalise_args(raw_args: Dict[str, Any], function_name: str) -> Dict[str, Any]:
        """Translate whatever the LLM produced into valid function kwargs."""
        kwargs: Dict[str, Any] = {}
        extra: Dict[str, Any] = {}

        valid = _VALID_KWARGS.get(function_name, _VALID_KWARGS["scan_command_injection"])
        aliases = _KWARG_ALIASES.get(function_name, {})

        items_to_process: list[tuple[str, Any]] = []

        # 1. Process flat kwargs first (higher priority), then options dict
        if "options" in raw_args and isinstance(raw_args["options"], dict):
            for k, v in raw_args.items():
                if k != "options":
                    items_to_process.append((k, v))
            items_to_process.extend(raw_args["options"].items())
        else:
            items_to_process.extend(raw_args.items())

        for key, value in items_to_process:
            if key in _IGNORED_FLAGS:
                continue

            # CLI flag → translate to kwarg name
            if key.startswith("-"):
                kwarg_name = _FLAG_TO_KWARG.get(key)
                if kwarg_name:
                    # Apply per-function alias (e.g. os_cmd → command for execute_command)
                    kwarg_name = aliases.get(kwarg_name, kwarg_name)
                    if kwarg_name in valid and kwarg_name not in kwargs:
                        kwargs[kwarg_name] = value
                    elif kwarg_name not in kwargs:
                        extra[key] = value
                else:
                    extra[key] = value
            else:
                # Apply per-function alias
                key = aliases.get(key, key)
                if key in valid:
                    if key not in kwargs:
                        kwargs[key] = value
                elif key == "extra_options" and isinstance(value, dict):
                    extra.update(value)
                elif key in ("max_duration", "quick_test"):
                    continue
                else:
                    # Convert bare kwarg names to CLI flags for extra_options
                    cli_flag = _KWARG_TO_FLAG.get(key)
                    if cli_flag and cli_flag not in extra:
                        extra[cli_flag] = value
                    elif key not in extra:
                        extra[key] = value

        if extra:
            kwargs["extra_options"] = extra

        return kwargs

    # Maps normalised kwarg names to commix CLI flags for direct execution
    _KWARG_TO_OPTION = {
        "cookie": "--cookie",
        "parameter": "-p",
        "technique": "--technique",
        "level": "--level",
        "web_root": "--web-root",
        "tamper": "--tamper",
        "os_cmd": "--os-cmd",
        "command": "--os-cmd",
        "file_path": "--file-read",
        "get_current_user": "--current-user",
        "get_hostname": "--hostname",
        "check_root": "--is-root",
        "check_admin": "--is-admin",
        "get_sys_info": "--sys-info",
        "get_users": "--users",
        "get_passwords": "--passwords",
        "get_all": "--all",
    }

    def execute(self, request: ToolRequest):
        import os
        import subprocess
        import threading

        started = utc_now()
        try:
            cmd_mod = load_module_from_file("pwnai_commix_module", self.module_path)
            function_name = self.ACTION_MAP.get(request.action, "scan_command_injection")
            build_args = getattr(cmd_mod, "_build_args")
            commix_bin = getattr(cmd_mod, "COMMIX_BIN")

            kwargs = self._normalise_args(dict(request.args), function_name)

            # Set target from request
            if request.target_mode == "url":
                kwargs["url"] = request.target.url
            else:
                kwargs["request_file"] = request.target.request_file

            print(f"[Command Injection] Action: {request.action} -> {function_name}")
            print(f"[Command Injection] Target: {request.target.url or request.target.request_file}")
            print(f"[Command Injection] Normalised kwargs: {kwargs}")

            # Build commix CLI options from normalised kwargs
            options: Dict[str, Any] = {}

            # Extract extra_options first (already in CLI flag format)
            extra = kwargs.pop("extra_options", None)
            if extra and isinstance(extra, dict):
                options.update(extra)

            # Convert kwargs to CLI flags
            url = kwargs.pop("url", None)
            request_file = kwargs.pop("request_file", None)
            data = kwargs.pop("data", None)
            python_bin = kwargs.pop("python_bin", "python")
            max_runtime = kwargs.pop("max_runtime_seconds", 600)

            if url:
                options["--url"] = url
            elif request_file:
                options["-r"] = request_file

            if data:
                options["--data"] = data

            for kwarg_name, value in kwargs.items():
                cli_flag = self._KWARG_TO_OPTION.get(kwarg_name)
                if cli_flag and cli_flag not in options:
                    options[cli_flag] = value

            # Agent-friendly defaults
            options.setdefault("--batch", True)
            # Prevent commix from entering interactive os_shell after command execution
            options["--answers"] = "Do you want to spawn=N"

            cmd = [python_bin, commix_bin] + build_args(options)
            cwd = os.path.dirname(commix_bin)

            print(f"[Command Injection] CMD: {' '.join(cmd)}")

            # Stream output in real-time via Popen; stdin=DEVNULL prevents
            # hangs on any unexpected interactive prompt.
            stdout_lines: list[str] = []
            stderr_lines: list[str] = []

            proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                text=True,
                shell=False,
            )

            def _drain_stderr(p: subprocess.Popen, buf: list[str]):
                assert p.stderr is not None
                for line in p.stderr:
                    buf.append(line)
                    print(line, end="", flush=True)

            t = threading.Thread(target=_drain_stderr, args=(proc, stderr_lines), daemon=True)
            t.start()

            assert proc.stdout is not None
            try:
                for line in proc.stdout:
                    stdout_lines.append(line)
                    print(line, end="", flush=True)
            except Exception:
                pass

            try:
                proc.wait(timeout=max_runtime)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()

            t.join(timeout=5)

            stdout_text = "".join(stdout_lines)
            stderr_text = "".join(stderr_lines)
            duration = (utc_now() - started).total_seconds()
            print(f"\n[Command Injection] Completed in {duration:.1f}s | rc={proc.returncode}")

            return self._build_result(
                request=request,
                started_at=started,
                success=proc.returncode == 0,
                returncode=proc.returncode,
                stdout=stdout_text,
                stderr=stderr_text,
                raw_output={"command": cmd, "returncode": proc.returncode},
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

