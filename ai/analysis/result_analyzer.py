from __future__ import annotations

import hashlib
from typing import List

from ai.contracts.schemas import ExecutionResult, Finding, ToolRequest


def _stable_id(*parts: str) -> str:
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", errors="ignore"))
        h.update(b"\x00")
    return h.hexdigest()[:16]


class ResultAnalyzer:
    def analyze(self, request: ToolRequest, result: ExecutionResult) -> List[Finding]:
        findings: List[Finding] = []
        if not result.success:
            return findings

        if request.tool_name == "brute_force":
            creds = result.raw_output.get("credentials_found") or []
            for cred in creds:
                user = str(cred.get("username", ""))
                pwd = str(cred.get("password", ""))
                fid = _stable_id(request.tool_name, request.action, user, pwd)
                findings.append(
                    Finding(
                        finding_id=fid,
                        tool_name=request.tool_name,
                        title="Valid credentials discovered",
                        severity="high",
                        confidence=0.95,
                        evidence=f"username={user}, password={pwd}",
                    )
                )
            return findings

        if request.tool_name == "nuclei_xss":
            parsed = result.raw_output.get("parsed_output", {})
            raw_findings = parsed.get("findings", []) if isinstance(parsed, dict) else []
            for item in raw_findings:
                title = str(item.get("name", "Nuclei finding"))
                severity = str(item.get("severity", "medium")).lower()
                severity = severity if severity in {"info", "low", "medium", "high", "critical"} else "medium"
                evidence = str(item.get("evidence") or item.get("url") or "")
                fid = str(item.get("finding_id") or _stable_id(request.tool_name, title, evidence))
                findings.append(
                    Finding(
                        finding_id=fid,
                        tool_name=request.tool_name,
                        title=title,
                        severity=severity,  # type: ignore[arg-type]
                        confidence=0.8,
                        evidence=evidence,
                    )
                )
            return findings

        # Generic parser for command and SQL outputs — use STRONG evidence only.
        text = (result.stdout or "") + "\n" + (result.stderr or "")
        lowered = text.lower()

        # === SQL injection: require strong exploit evidence, not just keyword match ===
        if request.tool_name == "sql_injection":
            strong_sqli_signals = [
                "sqlmap identified the following injection point",
                "parameter" and "injectable",
                "available databases",
                "back-end dbms",
                "fetched data logged",
                "table(s) found",
                "dumped to csv",
                "database:",
                "entries found",
                "type: boolean-based",
                "type: time-based",
                "type: union query",
                "type: error-based",
                "type: stacked queries",
            ]
            has_strong_signal = any(s in lowered for s in strong_sqli_signals)

            # False positive exclusions: generic sqlmap output without real injection
            false_positive_indicators = [
                "all tested parameters do not appear to be injectable",
                "connection timed out",
                "target url content is not stable",
                "it is not recommended to continue",
            ]
            is_false_positive = any(fp in lowered for fp in false_positive_indicators)

            if has_strong_signal and not is_false_positive:
                # Determine severity based on what was confirmed
                severity = "medium"
                if any(s in lowered for s in ["available databases", "database:", "back-end dbms"]):
                    severity = "high"
                if any(s in lowered for s in ["dumped to csv", "entries found", "fetched data"]):
                    severity = "critical"

                fid = _stable_id(request.tool_name, request.action, text[:200])
                findings.append(
                    Finding(
                        finding_id=fid,
                        tool_name=request.tool_name,
                        title=f"SQL injection confirmed ({request.action})",
                        severity=severity,  # type: ignore[arg-type]
                        confidence=0.85 if severity in ("high", "critical") else 0.7,
                        evidence=text[:500],
                    )
                )
            return findings

        # === Command injection: require confirmed exploitation ===
        if request.tool_name == "command_injection":
            strong_cmdi_signals = [
                "identified the following injection point",
                "the target is vulnerable",
                "remote command execution",
                "os command injection",
            ]
            has_strong_signal = any(s in lowered for s in strong_cmdi_signals)
            if has_strong_signal:
                fid = _stable_id(request.tool_name, request.action, text[:200])
                findings.append(
                    Finding(
                        finding_id=fid,
                        tool_name=request.tool_name,
                        title="Command injection confirmed",
                        severity="high",
                        confidence=0.8,
                        evidence=text[:500],
                    )
                )
            return findings

        # === Generic fallback for other tools — still require "vulnerable" keyword ===
        if "vulnerable" in lowered:
            fid = _stable_id(request.tool_name, request.action, text[:200])
            findings.append(
                Finding(
                    finding_id=fid,
                    tool_name=request.tool_name,
                    title=f"{request.tool_name} reported exploitable signal",
                    severity="medium",
                    confidence=0.6,
                    evidence=text[:500],
                )
            )
        return findings

