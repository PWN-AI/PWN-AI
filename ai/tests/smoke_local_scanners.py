"""Opt-in live adapter smoke test against an explicitly started local DVWA.

Run from repo root: python -m ai.tests.smoke_local_scanners
Requires DVWA at 127.0.0.1:8088 and separately installed scanner tools.
"""
from pathlib import Path

from ai.analysis.result_analyzer import ResultAnalyzer
from ai.contracts.schemas import TargetSpec, ToolRequest
from ai.execution.auth import attempt_pre_auth
from ai.execution.run_manager import RunManager


def main():
    root = Path.cwd()
    auth = attempt_pre_auth(
        target_url="http://127.0.0.1:8088/vulnerabilities/sqli/?id=1&Submit=Submit",
        username="admin", password="password", login_url="http://127.0.0.1:8088/login.php",
        crawl_depth=1,
    )
    assert auth.success
    cookie = auth.cookie
    manager = RunManager(root)
    requests = [
        ToolRequest(
            task_id="smoke-sqli", tool_name="sql_injection", action="scan", target_mode="url",
            target=TargetSpec(url="http://127.0.0.1:8088/vulnerabilities/sqli/?id=1&Submit=Submit"),
            args={"options": {
                "--cookie": cookie, "-p": "id", "--technique": "B", "--level": 1,
                "--risk": 1, "--threads": 1, "--timeout": 5, "--retries": 0,
                "--output-dir": "artifacts/smoke/sqlmap",
            }, "max_duration": 90},
        ),
        ToolRequest(
            task_id="smoke-cmdi", tool_name="command_injection", action="scan", target_mode="url",
            target=TargetSpec(url="http://127.0.0.1:8088/vulnerabilities/exec/"),
            args={"data": "ip=127.0.0.1&Submit=Submit", "cookie": cookie, "parameter": "ip",
                  "technique": "c", "max_runtime_seconds": 90,
                  "extra_options": {"--batch": True, "--timeout": 5,
                                    "--ignore-session": True,
                                    "--output-dir": "artifacts/smoke/commix"}},
        ),
        ToolRequest(
            task_id="smoke-brute", tool_name="brute_force", action="run_bruteforce", target_mode="url",
            target=TargetSpec(url="http://127.0.0.1:8088/vulnerabilities/brute/?Login=Login"),
            args={"cookie": cookie, "usernames": ["admin"], "passwords": ["password"],
                  "method": "GET", "success_indicator": "Welcome to the password protected area",
                  "max_runtime_seconds": 30},
        ),
        ToolRequest(
            task_id="smoke-xss", tool_name="nuclei_xss", action="scan", target_mode="url",
            target=TargetSpec(url="http://127.0.0.1:8088/vulnerabilities/xss_r/"),
            args={"cookie": cookie, "tags": "xss", "timeout": 60,
                  "nuclei_templates": str(root / "ai/tests/fixtures"),
                  "out": "artifacts/smoke/nuclei.json"},
        ),
    ]
    output = root / "artifacts/smoke"
    output.mkdir(parents=True, exist_ok=True)
    analyzer = ResultAnalyzer()
    for request in requests:
        result = manager.execute(request)
        (output / f"{request.task_id}.json").write_text(result.model_dump_json(indent=2), encoding="utf-8")
        findings = analyzer.analyze(request, result)
        print(f"{request.tool_name}: success={result.success}; findings={len(findings)}; error={result.error}")
        assert result.success, result.error or result.stderr
        assert findings, result.stdout[-2000:]


if __name__ == "__main__":
    main()
