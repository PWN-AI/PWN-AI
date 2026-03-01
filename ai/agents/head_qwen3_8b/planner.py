from __future__ import annotations

import sys
from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any, Dict, List, Sequence

from langchain_core.messages import HumanMessage, SystemMessage

from ai.contracts.schemas import ExecutionResult, Finding, TargetSpec, TaskSpec
from ai.llm.json_utils import parse_json_object


def _is_login_like_url(url: str) -> bool:
    """Check if a URL looks like a login page or the bare base URL."""
    url_l = (url or "").lower().rstrip("/")
    login_indicators = ["login", "signin", "sign-in", "log-in", "auth"]
    if any(ind in url_l for ind in login_indicators):
        return True
    from urllib.parse import urlparse
    parsed = urlparse(url_l)
    if not parsed.path or parsed.path == "/":
        return True
    return False


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks: List[str] = []
        for item in content:
            if isinstance(item, dict) and "text" in item:
                chunks.append(str(item["text"]))
            else:
                chunks.append(str(item))
        return "\n".join(chunks)
    return str(content)


class HeadPlanner:
    """Head planner with FULL LLM AUTONOMY.

    The LLM controls the entire plan — number of tasks, tools, targets,
    flags, and escalation chains. No hardcoded expansion, no hardcoded
    fallback chains. The only safety net is retargeting login-like URLs
    to discovered authenticated endpoints.
    """

    def __init__(self, llm: Any, prompt_path: Path):
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    @dataclass
    class PlannerDecision:
        tasks: List[TaskSpec]
        thinking_summary: List[str]
        should_stop: bool = False
        stop_reason: str = ""
        # Notes the LLM wrote about this execution — carried into the next replan context.
        task_notes: List[str] = field(default_factory=list)

    # ── Utilities ──────────────────────────────────────────────────

    def _task_signature(self, task: TaskSpec) -> str:
        args_blob = json.dumps(task.args, sort_keys=True, ensure_ascii=True)
        target_blob = json.dumps(task.target.model_dump(), sort_keys=True, ensure_ascii=True)
        return f"{task.tool_hint}|{task.action_hint}|{task.target_mode}|{target_blob}|{args_blob}"

    def _dedupe_tasks(self, tasks: Sequence[TaskSpec]) -> List[TaskSpec]:
        deduped: List[TaskSpec] = []
        seen = set()
        for task in tasks:
            sig = self._task_signature(task)
            if sig in seen:
                continue
            seen.add(sig)
            deduped.append(task)
        return deduped

    def _normalize_task_ids(self, tasks: Sequence[TaskSpec], *, prefix: str = "task") -> List[TaskSpec]:
        normalized: List[TaskSpec] = []
        for idx, task in enumerate(tasks, start=1):
            normalized.append(task.model_copy(update={"id": f"{prefix}-{idx:02d}"}))
        return normalized

    def _safe_summary(self, raw: Any, *, default: str) -> List[str]:
        if isinstance(raw, list):
            summary = [str(item).strip() for item in raw if str(item).strip()]
            if summary:
                return summary[:6]
        if isinstance(raw, str) and raw.strip():
            return [raw.strip()]
        return [default]

    def _sanitize_raw_task(self, raw: Dict[str, Any]) -> Dict[str, Any]:
        """Best-effort cleanup of a raw task dict before Pydantic validation.

        Fixes common LLM mistakes:
        - Strips None/null values from target
        - Normalizes action_hint to a plain action name
        - Moves misplaced sqlmap flags from args root into args.options
        - Ensures required fields have safe defaults
        """
        # 1. Clean target — remove null values so TargetSpec validator passes
        target = raw.get("target")
        if isinstance(target, dict):
            raw["target"] = {k: v for k, v in target.items() if v is not None}

        # 2. Normalize action_hint — strip flag-like values to plain names
        action = str(raw.get("action_hint", "scan")).strip()
        # If it looks like flags (starts with -- or contains spaces with --), extract the action name
        if action.startswith("--") or " --" in action:
            # Try to extract a known action keyword
            known_actions = {
                "dump", "dump_all", "scan", "enumerate", "enumerate_dbs",
                "enumerate_tables", "enumerate_columns", "schema",
                "scan_login", "run_bruteforce", "execute_command",
                "read_remote_file",
            }
            for kw in known_actions:
                if kw in action.lower().replace("-", "_"):
                    action = kw
                    break
            else:
                action = "scan"
        raw["action_hint"] = action

        # 3. Move misplaced sqlmap CLI flags from args root to args.options
        args = raw.get("args", {})
        if isinstance(args, dict) and not isinstance(args.get("options"), dict):
            promoted: Dict[str, Any] = {}
            leftover: Dict[str, Any] = {}
            for k, v in args.items():
                if str(k).startswith("-"):
                    # Drop only null or empty string — keep booleans and real values
                    if v is None or v == "":
                        continue
                    promoted[k] = v
                else:
                    leftover[k] = v
            if promoted:
                leftover["options"] = promoted
            raw["args"] = leftover

        # 4. Ensure required string fields aren't empty
        raw.setdefault("objective", raw.get("title", "Execute task"))
        raw.setdefault("target_mode", "url" if (raw.get("target") or {}).get("url") else "request_file")

        return raw

    def _extract_tasks(self, parsed: Dict[str, Any], *, max_tasks: int) -> List[TaskSpec]:
        raw_tasks = parsed.get("tasks", [])
        tasks: List[TaskSpec] = []
        for idx, raw in enumerate(raw_tasks, start=1):
            if not isinstance(raw, dict):
                continue
            raw.setdefault("id", f"task-{idx:02d}")
            raw.setdefault("priority", idx)
            raw = self._sanitize_raw_task(raw)
            try:
                tasks.append(TaskSpec.model_validate(raw))
            except Exception as exc:
                print(f"[Head Planner] WARNING: Skipping invalid task #{idx}: {exc}")
                continue
        deduped = self._dedupe_tasks(tasks)
        deduped = sorted(deduped, key=lambda t: (t.priority, t.id))
        return deduped[:max_tasks]

    # ── Context builders ───────────────────────────────────────────

    def _build_endpoint_context(self, operator_context: Dict[str, Any]) -> str:
        """Build a rich context string from discovered endpoints and navigation."""
        parts: List[str] = []
        discovered_endpoints = operator_context.get("discovered_endpoints", [])
        navigation_structure = operator_context.get("navigation_structure", {})

        if discovered_endpoints:
            parameterized = [ep for ep in discovered_endpoints if "?" in ep or "=" in ep]
            other = [ep for ep in discovered_endpoints if ep not in parameterized]

            parts.append(f"\nDISCOVERED ENDPOINTS ({len(discovered_endpoints)} total):")
            if parameterized:
                parts.append("  HIGH PRIORITY — Endpoints with query parameters (injectable targets):")
                for ep in parameterized[:25]:
                    parts.append(f"    - {ep}")
            if other:
                parts.append("  Other authenticated pages (may accept POST data / have forms):")
                for ep in other[:20]:
                    parts.append(f"    - {ep}")

        if navigation_structure:
            parts.append("\nNAVIGATION STRUCTURE (application menu):")
            for section, items in navigation_structure.items():
                parts.append(f"  {section}:")
                for item in items[:10]:
                    parts.append(f"    - {item}")

        # Include truncated HTML sample for the LLM to analyze page structure
        html = operator_context.get("authenticated_page_html", "")
        if html:
            sample = html[:3000]
            parts.append(f"\nAUTHENTICATED PAGE HTML SAMPLE ({len(html)} chars total, first 3000 shown):")
            parts.append(sample)

        return "\n".join(parts)

    # ── Safety-net retargeting ─────────────────────────────────────

    def _retarget_to_authenticated_endpoints(
        self,
        tasks: List[TaskSpec],
        discovered_endpoints: List[str],
        original_target: TargetSpec,
    ) -> List[TaskSpec]:
        """Replace login-like target URLs with discovered authenticated endpoints.

        This is a SAFETY NET only — the LLM should already pick correct targets.
        Only activates when a task points at a login page or bare base URL.
        """
        if not discovered_endpoints:
            return tasks

        parameterized = [ep for ep in discovered_endpoints if "?" in ep or "=" in ep]
        best = parameterized[0] if parameterized else discovered_endpoints[0]

        retargeted: List[TaskSpec] = []
        for task in tasks:
            target_url = task.target.url or ""
            if target_url and _is_login_like_url(target_url):
                print(f"  [Retarget] {task.id}: login-like URL '{target_url}' -> '{best}'")
                new_target = TargetSpec(url=best)
                retargeted.append(task.model_copy(update={
                    "target": new_target,
                    "target_mode": "url",
                }))
            else:
                retargeted.append(task)
        return retargeted

    # ── Plan (initial) ─────────────────────────────────────────────

    def plan(
        self,
        *,
        user_goal: str,
        target: TargetSpec,
        operator_context: Dict[str, Any],
        max_tasks: int = 6,
    ) -> "HeadPlanner.PlannerDecision":
        auth_info = operator_context.get("auth", {})
        auth_verified = operator_context.get("auth_verified", False)
        has_creds = bool(operator_context.get("username") and operator_context.get("password"))
        discovered_endpoints = operator_context.get("discovered_endpoints", [])

        # --- Build rich auth + endpoint context for the LLM ---
        auth_context_block = ""
        if has_creds and not auth_verified and auth_info.get("attempted"):
            auth_context_block = (
                "\n\nAUTH STATUS: Authentication was attempted but NOT strictly verified. "
                "The session cookie may be invalid. Prefer authenticated endpoints over login pages. "
                "If no authenticated endpoints were discovered, note this in thinking_summary."
            )
        elif has_creds and auth_verified:
            cookie = operator_context.get("auth_cookie", "")
            auth_context_block = (
                "\n\nAUTH STATUS: Authentication VERIFIED. Session cookie is valid. "
                f"Credentials: username={operator_context.get('username')}."
            )
            if cookie:
                auth_context_block += (
                    f"\nAuth cookie: {cookie}"
                    "\nYou MUST include this cookie in every task's args.options under '--cookie'."
                )

        endpoint_context = self._build_endpoint_context(operator_context)
        if endpoint_context:
            auth_context_block += endpoint_context

        # Sanitized context (exclude huge HTML blob and password)
        sanitized_context = {
            k: v for k, v in operator_context.items()
            if k not in ("password", "authenticated_page_html", "navigation_structure", "discovered_endpoints")
        }

        prompt_input = {
            "user_goal": user_goal,
            "target": target.model_dump(),
            "operator_context": sanitized_context,
            "constraints": {
                "max_tasks": max_tasks,
            },
        }
        user_message = (
            "Create a task plan to achieve the goal.\n"
            "You have FULL AUTONOMY — decide the number of tasks, tools, targets, "
            "escalation chains, and ALL flags/options. Your plan is final.\n"
            "CRITICAL: For injection tools (sql_injection, command_injection), the target URL "
            "MUST contain an injectable parameter (e.g., ?id=1). Analyze the discovered endpoints "
            "and pick the best one. If no parameterized URL exists, use --data for POST injection.\n"
            "Return JSON only.\n"
            "Required JSON shape:\n"
            "{\n"
            '  "thinking_summary": ["short planning bullets"],\n'
            '  "tasks": [TaskSpec, ...]\n'
            "}\n"
            f"{auth_context_block}\n"
            f"INPUT:\n{json.dumps(prompt_input, indent=2)}"
        )
        try:
            print("\n[Head Planner] Invoking LLM for initial plan...")
            response = self.llm.invoke(
                [
                    SystemMessage(content=self.system_prompt),
                    HumanMessage(content=user_message),
                ]
            )
            text = _message_text(getattr(response, "content", response))
            print(f"\n[Head Planner] Raw response length: {len(text)} chars")
            parsed = parse_json_object(text)
            tasks = self._extract_tasks(parsed, max_tasks=max_tasks)

            # Safety net: retarget login-like URLs to discovered endpoints
            if auth_verified and discovered_endpoints:
                tasks = self._retarget_to_authenticated_endpoints(tasks, discovered_endpoints, target)

            if tasks:
                return self.PlannerDecision(
                    tasks=tasks,
                    thinking_summary=self._safe_summary(
                        parsed.get("thinking_summary"),
                        default="Head planner generated task sequence.",
                    ),
                    should_stop=False,
                )
        except Exception as exc:
            print(f"\n[Head Planner] LLM plan() failed: {exc}", file=sys.stderr)

        # ── Minimal fallback (LLM failed to produce valid output) ──
        print("[Head Planner] Using minimal fallback plan (LLM output unparseable).")
        target_mode = "url" if target.url else "request_file"
        target_payload: Dict[str, str] = (
            {"url": target.url} if target.url else {"request_file": target.request_file or ""}
        )
        fallback_url = target.url or ""

        if discovered_endpoints:
            parameterized = [ep for ep in discovered_endpoints if "?" in ep]
            fallback_url = parameterized[0] if parameterized else discovered_endpoints[0]
            target_payload = {"url": fallback_url}
            target_mode = "url"

        fallback_opts: Dict[str, Any] = {"--level": 2, "--risk": 1}
        cookie = operator_context.get("auth_cookie", "")
        if cookie:
            fallback_opts["--cookie"] = cookie

        fallback_task = TaskSpec.model_validate({
            "id": "task-01",
            "title": "Baseline vulnerability scan",
            "objective": "Scan the target for vulnerabilities.",
            "rationale": "Fallback: LLM output could not be parsed.",
            "priority": 1,
            "tool_hint": "sql_injection",
            "action_hint": "scan",
            "target_mode": target_mode,
            "target": target_payload,
            "args": {"options": fallback_opts},
        })

        return self.PlannerDecision(
            tasks=[fallback_task],
            thinking_summary=["Fallback: LLM output unparseable. Running baseline scan."],
            should_stop=False,
        )

    # ── Replan (adaptive, after each task execution) ───────────────

    def replan(
        self,
        *,
        user_goal: str,
        target: TargetSpec,
        operator_context: Dict[str, Any],
        current_task: TaskSpec,
        current_result: ExecutionResult,
        findings: Sequence[Finding],
        remaining_tasks: Sequence[TaskSpec],
        prior_notes: Sequence[str] = (),
        replan_iteration: int = 0,
        max_new_tasks: int = 3,
    ) -> "HeadPlanner.PlannerDecision":
        """Re-evaluate after one task and decide the next step.

        Context windowing:
        - First re-plan (replan_iteration=0): the LLM sees the full stdout/stderr
          of the task it just ran, and is asked to write task_notes that it will
          carry forward as its only memory.
        - Subsequent re-plans: the LLM sees its accumulated prior_notes from all
          previous tasks plus a compact snippet of the latest output only.
        """
        # Sanitized operator context (no passwords / large HTML blobs)
        sanitized_context = {
            k: v for k, v in operator_context.items()
            if k not in ("password", "authenticated_page_html", "navigation_structure")
        }

        # ── Context windowing ──────────────────────────────────────────────
        if replan_iteration == 0:
            # First replan: give the LLM the richest possible view of the output.
            result_context: Dict[str, Any] = {
                "tool_name": current_result.tool_name,
                "action": current_result.action,
                "success": current_result.success,
                "returncode": current_result.returncode,
                "stdout": (current_result.stdout or ""),
                "stderr": (current_result.stderr or ""),
                "raw_output": current_result.raw_output,
            }
            iteration_note = (
                "FIRST RE-PLAN: You see the full output of the task you just ran. "
                "Summarise what you found in task_notes — concise, factual bullets. "
                "These notes will be your ONLY memory in future re-plans "
                "(you will NOT see this full output again)."
            )
            prior_notes_block = ""
        else:
            # Subsequent replans: compact snapshot of latest output + accumulated notes.
            result_context = {
                "tool_name": current_result.tool_name,
                "action": current_result.action,
                "success": current_result.success,
                "returncode": current_result.returncode,
                "stdout": (current_result.stdout or ""),
                "stderr": (current_result.stderr or ""),
            }
            iteration_note = (
                f"RE-PLAN ITERATION {replan_iteration + 1}: You see your accumulated notes "
                "from all previous tasks and only the latest task output. "
                "Use your notes to maintain continuity and avoid repeating work."
            )
            if prior_notes:
                notes_text = "\n".join(f"  - {n}" for n in prior_notes)
                prior_notes_block = f"\n\nYOUR NOTES FROM PREVIOUS TASKS:\n{notes_text}"
            else:
                prior_notes_block = ""

        prompt_input = {
            "user_goal": user_goal,
            "target": target.model_dump(),
            "operator_context": sanitized_context,
            "current_task": current_task.model_dump(),
            "current_result": result_context,
            "findings": [finding.model_dump() for finding in findings],
            "remaining_tasks": [task.model_dump() for task in remaining_tasks],
            "constraints": {"max_new_tasks": max_new_tasks},
        }

        user_message = (
            "Re-plan after latest execution result.\n"
            "You have FULL AUTONOMY. Analyse the tool output and decide next steps.\n"
            "If the tool found a vulnerability, escalate "
            "(e.g., scan confirmed SQLi → enumerate_dbs → dump).\n"
            "If the tool failed, pivot to a different approach or stop.\n"
            "Return JSON only with this schema:\n"
            "{\n"
            '  "thinking_summary": ["short bullets"],\n'
            '  "task_notes": ["concise facts to remember about this result"],\n'
            '  "should_stop": false,\n'
            '  "stop_reason": "",\n'
            '  "tasks": [TaskSpec, ...]\n'
            "}\n"
            f"\n{iteration_note}"
            f"{prior_notes_block}\n"
            f"INPUT:\n{json.dumps(prompt_input, indent=2)}"
        )

        try:
            print(f"\n[Head Planner] Invoking LLM for re-plan after {current_task.id} "
                  f"(iteration {replan_iteration})...")
            response = self.llm.invoke(
                [
                    SystemMessage(content=self.system_prompt),
                    HumanMessage(content=user_message),
                ]
            )
            text = _message_text(getattr(response, "content", response))
            print(f"\n[Head Planner] Re-plan raw response length: {len(text)} chars")
            parsed = parse_json_object(text)
            tasks = self._extract_tasks(parsed, max_tasks=max_new_tasks)
            raw_notes = parsed.get("task_notes", [])
            task_notes = [str(n).strip() for n in raw_notes if str(n).strip()][:10]
            return self.PlannerDecision(
                tasks=tasks,
                thinking_summary=self._safe_summary(
                    parsed.get("thinking_summary"),
                    default="Head planner evaluated latest result.",
                ),
                should_stop=bool(parsed.get("should_stop", False)),
                stop_reason=str(parsed.get("stop_reason", "")).strip(),
                task_notes=task_notes,
            )
        except Exception as exc:
            print(f"\n[Head Planner] LLM replan() failed: {exc}", file=sys.stderr)
            return self.PlannerDecision(
                tasks=[],
                thinking_summary=[f"Replan LLM failed: {exc}. Continuing with remaining queue."],
                should_stop=False,
            )
