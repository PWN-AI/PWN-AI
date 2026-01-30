import os
import shlex
import subprocess
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Union

SQLMAP_BIN = "/var/data/python/bin/sqlmap"

ALLOWED_FLAGS = {
    # targeting
    "-u": "value",
    "-r": "value",
    "--data": "value",
    "--cookie": "value",
    "--headers": "value",
    "--method": "value",
    "--user-agent": "value",
    "--referer": "value",
    "--host": "value",

    # detection & techniques
    "-p": "value",
    "--level": "value",
    "--risk": "value",
    "--technique": "value",
    "--tamper": "value",
    "--random-agent": "bool",
    "--proxy": "value",
    "--tor": "bool",
    "--check-tor": "bool",

    # enumeration
    "--dbs": "bool",
    "--tables": "bool",
    "--columns": "bool",
    "--schema": "bool",
    "--current-db": "bool",
    "--current-user": "bool",
    "--is-dba": "bool",
    "--banner": "bool",
    "--users": "bool",
    "--passwords": "bool",
    "--privileges": "bool",
    "--roles": "bool",

    # selection filters
    "-D": "value",
    "-T": "value",
    "-C": "value",
    "--exclude-sysdbs": "bool",

    # extraction
    "--dump": "bool",
    "--dump-all": "bool",
    "--where": "value",
    "--start": "value",
    "--stop": "value",
    "--first": "value",
    "--last": "value",
    "--limit": "value",
    "--order-by": "value",

    # misc / safety
    "--batch": "bool",
    "--flush-session": "bool",
    "--fresh-queries": "bool",
    "--threads": "value",
    "--timeout": "value",
    "--retries": "value",
    "--delay": "value",
    "--safe-url": "value",
    "--safe-post": "value",
    "--safe-freq": "value",
    "--safe-time": "value",
    "--output-dir": "value",
    "-v": "value",
}

@dataclass
class SqlmapResult:
    command: List[str]
    returncode: int
    stdout: str
    stderr: str


def _build_args(options: Dict[str, Any]) -> List[str]:
    """
    Convert options dict to a list of args.
    Example:
      {"--dbs": True, "-u": "http://localhost/...", "--batch": True}
    """
    args: List[str] = []

    for k, v in options.items():
        if k not in ALLOWED_FLAGS:
            raise ValueError(f"Flag not allowed: {k}")

        kind = ALLOWED_FLAGS[k]
        if kind == "bool":
            if v:
                args.append(k)
        else:
            if v is None:
                continue
            # Force everything to string and reject newlines to prevent header injection
            sv = str(v)
            if "\n" in sv or "\r" in sv:
                raise ValueError(f"Invalid value for {k}: contains newline characters")
            args.extend([k, sv])

    return args

def run_sqlmap_tool(
    *,
    url: Optional[str] = None,
    request_file: Optional[str] = None,
    options: Optional[Dict[str, Any]] = None,
    cwd: Optional[str] = None,
    max_runtime_seconds: int = 999
) -> SqlmapResult:
    options = options or {}

    # Require one target mode
    if (url is None) == (request_file is None):
        raise ValueError("Provide exactly one of: url or request_file")

    if request_file is not None:
        if not os.path.exists(request_file):
            raise FileNotFoundError(request_file)
        options["-r"] = request_file

    # Enforce batch by default (agent-friendly)
    options.setdefault("--batch", True)

    cmd = [SQLMAP_BIN] + _build_args(options)

    # Run without shell to prevent injection
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        shell=False,
    )

    return SqlmapResult(
        command=cmd,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
    )

# Example usage:
if __name__ == "__main__":
    res = run_sqlmap_tool(
        request_file="req",
        options={
            "--dump": True,
            "--level": 5,
            "--risk": 2,
            "--threads": 4,
            "-v": 1,
        },
    )
    print("CMD:", " ".join(shlex.quote(x) for x in res.command))
    print(res.stdout)
    print(res.stderr)

