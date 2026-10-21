#!/usr/bin/env python3
"""
nuclei_xss_agent.py

Agent-friendly XSS scan wrapper:
- Uses nuclei with templates root + tags xss (matches your working command)
- Optional dalfox stage (auto-skipped if not installed)
- Allowlist guardrails

Designed for authorized testing only.
"""

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlparse


def eprint(*args, **kwargs):
    print(*args, file=sys.stderr, **kwargs)


def stable_id(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


def read_targets(target: str) -> List[str]:
    """Read targets from a URL, a file path, or stdin ('-')."""
    if target == "-":
        lines = [l.strip() for l in sys.stdin.read().splitlines()]
        return [l for l in lines if l and not l.startswith("#")]

    if target.startswith("http://") or target.startswith("https://"):
        return [target.strip()]

    if os.path.exists(target):
        with open(target, "r", encoding="utf-8", errors="ignore") as f:
            lines = [l.strip() for l in f.read().splitlines()]
            return [l for l in lines if l and not l.startswith("#")]

    raise ValueError(f"Target '{target}' is not a URL, '-' (stdin), or an existing file.")


def load_allowlist(args) -> Tuple[set[str], List[re.Pattern]]:
    exact_hosts: set[str] = set()
    regex_patterns: List[re.Pattern] = []

    def add_host(h: str):
        h = h.strip().lower()
        if h:
            exact_hosts.add(h)

    def add_regex(rx: str):
        rx = rx.strip()
        if rx:
            regex_patterns.append(re.compile(rx, re.IGNORECASE))

    for h in args.allow_host or []:
        add_host(h)

    if args.allowlist_file:
        with open(args.allowlist_file, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("re:"):
                    add_regex(line[3:])
                else:
                    add_host(line)

    return exact_hosts, regex_patterns


def is_allowed(url: str, exact_hosts: set[str], regex_patterns: List[re.Pattern]) -> bool:
    try:
        u = urlparse(url.strip())
        host = (u.hostname or "").lower()
        if not host:
            return False
        if host in exact_hosts:
            return True
        # allow subdomains of allowed domains
        for h in exact_hosts:
            if host.endswith("." + h):
                return True
        for rx in regex_patterns:
            if rx.search(host):
                return True
        return False
    except Exception:
        return False


def which(bin_name: str, guesses: List[str]) -> str:
    p = shutil.which(bin_name)
    if p:
        return p
    for g in guesses:
        g = os.path.expanduser(g)
        if os.path.exists(g) and os.access(g, os.X_OK):
            return g
    return ""


def default_nuclei_guesses() -> List[str]:
    """Return common nuclei binary locations across platforms."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    return [
        # Workspace-local bundled binary (Windows-friendly default in this repo)
        os.path.join(project_root, "tools", "nuclei", "nuclei.exe" if os.name == "nt" else "nuclei"),
        # Common user installs
        "~/go/bin/nuclei",
    ]


def _shorten(s: str, n: int = 260) -> str:
    s = (s or "").strip()
    return s if len(s) <= n else s[: n - 1] + "…"


@dataclass
class RunnerConfig:
    follow_redirects: bool = True
    max_redirects: int = 5
    rate_limit: Optional[int] = None
    retries: Optional[int] = None
    timeout_seconds: int = 300
    silent: bool = True


@dataclass
class Finding:
    tool: str
    name: str
    severity: str
    url: str
    evidence: Optional[str] = None
    raw: Optional[dict] = None
    finding_id: str = ""


def detect_templates_root(user_path: Optional[str]) -> str:
    """
    Prefer user provided path.
    Otherwise, use a populated project-local, tools-local or user directory.
    """
    if user_path:
        return os.path.expanduser(user_path)
    script_dir = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(script_dir)
    candidates = [
        os.path.join(project_root, "nuclei-templates"),
        os.path.join(project_root, "tools", "nuclei-templates"),
        os.path.expanduser("~/nuclei-templates"),
    ]
    for candidate in candidates:
        if os.path.isdir(candidate) and has_templates(candidate):
            return candidate
    return ""


def has_templates(templates_root: str) -> bool:
    for root, _, files in os.walk(templates_root):
        for name in files:
            lower = name.lower()
            if lower.endswith(".yaml") or lower.endswith(".yml"):
                return True
    return False


def run_nuclei(
    nuclei_path: str,
    targets: List[str],
    templates_root: str,
    tags: str,
    headers: Dict[str, str],
    cfg: RunnerConfig,
    workdir: str,
) -> Tuple[str, List[Finding]]:
    out_jsonl = os.path.join(workdir, "nuclei.jsonl")
    if os.path.exists(out_jsonl):
        os.remove(out_jsonl)

    targets_file = os.path.join(workdir, "targets.txt")
    with open(targets_file, "w", encoding="utf-8") as f:
        for t in targets:
            f.write(t + "\n")

    cmd = [
        nuclei_path,
        "-l", targets_file,
        "-t", templates_root,
        "-tags", tags,
        "-jsonl",
        "-o", out_jsonl,
        # Keep scans reproducible and bounded; install/update templates explicitly.
        "-duc",
    ]

    if cfg.silent:
        cmd.append("-silent")

    if cfg.follow_redirects:
        cmd += ["-follow-redirects", "-max-redirects", str(cfg.max_redirects)]

    if cfg.rate_limit is not None:
        cmd += ["-rate-limit", str(cfg.rate_limit)]

    if cfg.retries is not None:
        cmd += ["-retries", str(cfg.retries)]

    # IMPORTANT: nuclei v3.7.0 does NOT support -Hf in your build; use repeatable -H
    for k, v in (headers or {}).items():
        cmd += ["-H", f"{k}: {v}"]

    proc = subprocess.run(
        cmd,
        check=False,
        timeout=cfg.timeout_seconds,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()
        stdout = (proc.stdout or "").strip()
        detail = stderr or stdout or f"nuclei exited with code {proc.returncode}"
        raise RuntimeError(f"Nuclei scan failed: {detail}")

    findings: List[Finding] = []
    if not os.path.exists(out_jsonl):
        return out_jsonl, findings

    with open(out_jsonl, "r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue

            info = d.get("info", {}) or {}
            name = info.get("name") or d.get("template-id") or "Nuclei Finding"
            severity = info.get("severity") or "unknown"
            matched = d.get("matched-at") or d.get("host") or ""

            # Nuclei often provides matcher-name / extracted-results / etc
            evidence = None
            if d.get("matcher-name"):
                evidence = str(d.get("matcher-name"))
            elif d.get("extracted-results"):
                evidence = " ; ".join(map(str, d.get("extracted-results") or []))

            finding = Finding(
                tool="nuclei",
                name=str(name),
                severity=str(severity),
                url=str(matched),
                evidence=_shorten(evidence) if evidence else None,
                raw=d,
            )
            finding.finding_id = stable_id(finding.tool, finding.name, finding.url, finding.evidence or "")
            findings.append(finding)

    return out_jsonl, findings


def main():
    ap = argparse.ArgumentParser(description="AI-agent friendly XSS scan wrapper (nuclei tags=xss).")
    ap.add_argument("--target", required=True, help="URL, file of URLs, or '-' for stdin")

    # Guardrails
    ap.add_argument("--allow-host", action="append", default=[], help="Allowed host/domain (repeatable)")
    ap.add_argument("--allowlist-file", help="Allowlist file; lines can be exact host or 're:<regex>'")

    # Auth/headers
    ap.add_argument("--cookie", help='Cookie string, e.g. "session=abc; csrftoken=xyz"')
    ap.add_argument("--auth-bearer", help="Bearer token for Authorization header")
    ap.add_argument("--header", action="append", default=[], help='Extra header (repeatable) e.g. --header "X: Y"')

    # Nuclei knobs (match what worked)
    ap.add_argument("--nuclei-templates", default=None, help="Templates root path (default: ~/nuclei-templates if present)")
    ap.add_argument("--tags", default="xss", help="Nuclei tags to run (default: xss)")
    ap.add_argument("--disable-nuclei", action="store_true", help="Skip nuclei stage")

    # Runtime
    ap.add_argument("--rate-limit", type=int, default=None)
    ap.add_argument("--retries", type=int, default=None)
    ap.add_argument("--no-follow-redirects", action="store_true")
    ap.add_argument("--max-redirects", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--out", default="xss_findings.json", help="Normalized output JSON file")
    ap.add_argument("--keep-raw", action="store_true")
    ap.add_argument("--workdir", default=None)

    args = ap.parse_args()

    exact_hosts, regex_patterns = load_allowlist(args)
    if not exact_hosts and not regex_patterns:
        eprint("ERROR: Provide allowlist via --allow-host and/or --allowlist-file.")
        sys.exit(2)

    targets = read_targets(args.target)
    filtered = [t for t in targets if is_allowed(t, exact_hosts, regex_patterns)]
    blocked = [t for t in targets if t not in filtered]

    if not filtered:
        eprint("ERROR: No targets remain after allowlist filtering.")
        sys.exit(2)

    if blocked:
        eprint(f"[!] {len(blocked)} targets blocked by allowlist (showing up to 5):")
        for b in blocked[:5]:
            eprint("  -", b)

    headers: Dict[str, str] = {}
    if args.cookie:
        headers["Cookie"] = args.cookie
    if args.auth_bearer:
        headers["Authorization"] = f"Bearer {args.auth_bearer}"

    for h in args.header:
        if ":" not in h:
            eprint(f"[!] Skipping invalid header (missing ':'): {h!r}")
            continue
        k, v = h.split(":", 1)
        headers[k.strip()] = v.strip()

    nuclei_path = which("nuclei", default_nuclei_guesses())
    if not args.disable_nuclei and not nuclei_path:
        eprint("ERROR: nuclei not found in PATH (install nuclei or set PATH).")
        sys.exit(2)

    templates_root = detect_templates_root(args.nuclei_templates)
    if not args.disable_nuclei and not templates_root:
        eprint("ERROR: Could not locate nuclei templates root. Pass --nuclei-templates /path/to/nuclei-templates")
        sys.exit(2)
    if templates_root and not os.path.isdir(templates_root):
        eprint(f"ERROR: templates root not found: {templates_root}")
        sys.exit(2)
    if not args.disable_nuclei and templates_root and not has_templates(templates_root):
        eprint(
            "ERROR: templates root contains no .yaml/.yml templates. "
            "Install nuclei-templates or pass --nuclei-templates to a valid directory."
        )
        sys.exit(2)

    cfg = RunnerConfig(
        follow_redirects=not args.no_follow_redirects,
        max_redirects=args.max_redirects,
        rate_limit=args.rate_limit,
        retries=args.retries,
        timeout_seconds=args.timeout,
        silent=True,
    )

    if args.workdir:
        os.makedirs(args.workdir, exist_ok=True)
        workdir = args.workdir
        temp_workdir = None
    else:
        temp_workdir = tempfile.TemporaryDirectory(prefix="xss_agent_")
        workdir = temp_workdir.name

    started = time.time()
    findings: List[Finding] = []
    raw_paths: Dict[str, str] = {}

    try:
        if not args.disable_nuclei:
            eprint(f"[*] Running nuclei: -t {templates_root} -tags {args.tags}")
            try:
                raw_jsonl, nuc_findings = run_nuclei(
                    nuclei_path=nuclei_path,
                    targets=filtered,
                    templates_root=templates_root,
                    tags=args.tags,
                    headers=headers,
                    cfg=cfg,
                    workdir=workdir,
                )
            except RuntimeError as exc:
                eprint(f"ERROR: {exc}")
                sys.exit(2)
            raw_paths["nuclei_jsonl"] = raw_jsonl
            findings.extend(nuc_findings)

        uniq: Dict[str, Finding] = {f.finding_id: f for f in findings}

        out = {
            "meta": {
                "tool": "nuclei_xss_agent",
                "version": "1.1",
                "started_at_unix": int(started),
                "duration_seconds": round(time.time() - started, 2),
                "targets_total": len(targets),
                "targets_scanned": len(filtered),
                "targets_blocked": len(blocked),
                "nuclei_templates_root": templates_root if not args.disable_nuclei else None,
                "nuclei_tags": args.tags if not args.disable_nuclei else None,
            },
            "raw_outputs": raw_paths if args.keep_raw else {},
            "status": "vulnerable" if uniq else "clean",
            "findings": [dataclasses.asdict(f) for f in uniq.values()],
        }

        with open(args.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2)

        print(json.dumps(out, indent=2))

        if not args.keep_raw:
            for p in raw_paths.values():
                if p and os.path.exists(p):
                    try:
                        os.remove(p)
                    except Exception:
                        pass

    finally:
        if temp_workdir is not None:
            temp_workdir.cleanup()


if __name__ == "__main__":
    main()
