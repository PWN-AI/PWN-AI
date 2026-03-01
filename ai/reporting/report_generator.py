from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

from ai.contracts.schemas import Finding, TaskRunRecord


class ReportGenerator:
    def write_markdown_report(
        self,
        *,
        output_dir: Path,
        session_id: str,
        user_goal: str,
        target: Dict[str, Any],
        operator_context: Dict[str, Any],
        notes: List[str],
        runs: List[TaskRunRecord],
        findings: List[Finding],
        planning_trace: List[str],
        stop_reason: str = "",
    ) -> Path:
        output_dir.mkdir(parents=True, exist_ok=True)
        report_path = output_dir / f"{session_id}_report.md"

        lines: List[str] = []
        lines.append(f"# Phase 1 Session Report: {session_id}")
        lines.append("")
        lines.append("## Scope")
        lines.append(f"- Goal: {user_goal}")
        lines.append(f"- Target: `{target}`")
        lines.append("")
        lines.append("## Session Notes")
        if notes:
            for note in notes:
                lines.append(f"- {note}")
        else:
            lines.append("- No session notes.")
        lines.append("")
        lines.append("## Head Planner Trace")
        if planning_trace:
            for idx, trace in enumerate(planning_trace, start=1):
                lines.append(f"- Step {idx}: {trace}")
        else:
            lines.append("- No head planner trace captured.")
        if stop_reason:
            lines.append(f"- Stop reason: `{stop_reason}`")
        lines.append("")
        auth = operator_context.get("auth")
        if isinstance(auth, dict):
            lines.append("## Auth Summary")
            lines.append(f"- Attempted: `{auth.get('attempted')}`")
            lines.append(f"- Success: `{auth.get('success')}`")
            lines.append(f"- Verified: `{auth.get('verified', 'N/A')}`")
            lines.append(f"- Login URL: `{auth.get('login_url')}`")
            lines.append(f"- Final URL: `{auth.get('final_url')}`")
            lines.append(f"- Message: `{auth.get('message')}`")
            discovered = auth.get("discovered_endpoints", [])
            if discovered:
                lines.append(f"- Discovered endpoints ({len(discovered)}):")
                for ep in discovered[:10]:
                    lines.append(f"  - `{ep}`")
            lines.append("")
        lines.append("## Task Execution Log")
        if not runs:
            lines.append("- No tasks were executed.")
        else:
            for idx, run in enumerate(runs, start=1):
                lines.append(f"### Task {idx}: {run.task.title}")
                lines.append(f"- Task ID: `{run.task.id}`")
                lines.append(f"- Tool/Action: `{run.request.tool_name}` / `{run.request.action}`")
                lines.append(f"- Decision: `{run.decision.approved}` ({run.decision.reason})")
                lines.append(f"- Status: `{run.status}`")
                lines.append(f"- Return code: `{run.result.returncode}`")
                if run.result.error:
                    lines.append(f"- Error: `{run.result.error}`")
                if run.result.artifacts:
                    lines.append(f"- Artifacts: `{run.result.artifacts}`")
                if run.result.stdout:
                    stdout_clean = run.result.stdout.strip().replace("```", "`")
                    lines.append("- Stdout (full):")
                    lines.append("```text")
                    lines.append(stdout_clean)
                    lines.append("```")
                if run.result.stderr:
                    stderr_clean = run.result.stderr.strip().replace("```", "`")
                    lines.append("- Stderr (full):")
                    lines.append("```text")
                    lines.append(stderr_clean)
                    lines.append("```")
                lines.append("")

        lines.append("## Findings")
        if not findings:
            lines.append("- No findings generated.")
        else:
            for finding in findings:
                lines.append(f"- [{finding.severity.upper()}] {finding.title}")
                lines.append(f"  - Confidence: {finding.confidence:.2f}")
                lines.append(f"  - Evidence: {finding.evidence[:300]}")
        lines.append("")

        report_path.write_text("\n".join(lines), encoding="utf-8")
        return report_path
