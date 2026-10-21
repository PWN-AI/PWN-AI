from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Dict, List
from urllib.parse import urlparse

from ai.contracts.schemas import ToolRequest
from ai.integrations.base import ToolAdapter, utc_now


class NucleiXssAdapter(ToolAdapter):
    """Thin passthrough adapter — the LLM controls all nuclei flags.

    The adapter builds the CLI command from request.args.
    LLM decides tags, timeout, headers, and all other options.
    """

    def __init__(self, project_root: Path):
        super().__init__(project_root)
        self.script_path = self.project_root / "nuclei" / "nuclei.py"

    def _derive_allow_hosts(self, request: ToolRequest) -> List[str]:
        allow_hosts: List[str] = []
        args_hosts = request.args.get("allow_host")
        if isinstance(args_hosts, list):
            allow_hosts.extend(str(x) for x in args_hosts if x)
        elif isinstance(args_hosts, str) and args_hosts:
            allow_hosts.append(args_hosts)

        if not allow_hosts and request.target.url:
            host = urlparse(request.target.url).hostname
            if host:
                allow_hosts.append(host)
        return allow_hosts

    def execute(self, request: ToolRequest):
        started = utc_now()
        try:
            out_path = request.args.get("out", "xss_findings.json")
            out_file = Path(out_path)
            if not out_file.is_absolute():
                out_file = self.project_root / out_file

            cmd = [
                sys.executable,
                str(self.script_path),
                "--target",
                request.target.url if request.target_mode == "url" else (request.target.request_file or ""),
                "--out",
                str(out_file),
                "--tags",
                str(request.args.get("tags", "xss")),
                "--timeout",
                str(request.args.get("timeout", 300)),
            ]

            for host in self._derive_allow_hosts(request):
                cmd.extend(["--allow-host", host])

            templates = request.args.get("nuclei_templates")
            if templates:
                cmd.extend(["--nuclei-templates", str(templates)])

            cookie = request.args.get("cookie")
            if cookie:
                cmd.extend(["--cookie", str(cookie)])

            headers = request.args.get("headers", [])
            if isinstance(headers, list):
                for header in headers:
                    cmd.extend(["--header", str(header)])

            if request.args.get("keep_raw"):
                cmd.append("--keep-raw")

            proc = subprocess.run(
                cmd,
                cwd=str(self.project_root),
                capture_output=True,
                text=True,
                shell=False,
                timeout=int(request.args.get("timeout", 300)) + 10,
            )

            raw_payload: Dict[str, object] = {"command": cmd}
            artifacts: Dict[str, str] = {}
            if out_file.exists():
                artifacts["normalized_output"] = str(out_file)
                try:
                    raw_payload["parsed_output"] = json.loads(out_file.read_text(encoding="utf-8"))
                except Exception:
                    pass

            return self._build_result(
                request=request,
                started_at=started,
                success=proc.returncode == 0,
                returncode=proc.returncode,
                stdout=proc.stdout,
                stderr=proc.stderr,
                raw_output=raw_payload,
                artifacts=artifacts,
            )
        except Exception as exc:
            return self._build_result(
                request=request,
                started_at=started,
                success=False,
                returncode=1,
                error=str(exc),
                stderr=str(exc),
            )

