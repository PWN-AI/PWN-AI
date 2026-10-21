from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from ai.contracts.schemas import ToolRequest
from ai.integrations.base import ToolAdapter, load_module_from_file, utc_now


class BruteForceAdapter(ToolAdapter):
    def __init__(self, project_root: Path):
        super().__init__(project_root)
        self.module_path = self.project_root / "brute_force" / "brute.py"

    def execute(self, request: ToolRequest):
        started = utc_now()
        try:
            brute_mod = load_module_from_file("pwnai_brute_module", self.module_path)
            if request.action == "scan_login":
                runner = brute_mod.scan_login
            else:
                runner = brute_mod.run_bruteforce

            kwargs: Dict[str, Any] = dict(request.args)
            if request.target_mode == "url":
                kwargs["url"] = request.target.url
            else:
                kwargs["request_file"] = request.target.request_file

            result = runner(**kwargs)
            raw = {
                "command": result.command,
                "credentials_found": result.credentials_found,
                "stats": result.stats,
                "logs_count": len(result.logs),
            }
            return self._build_result(
                request=request,
                started_at=started,
                success=bool(result.credentials_found) or result.returncode == 0,
                returncode=result.returncode,
                stdout=result.stdout,
                stderr=result.stderr,
                raw_output=raw,
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

