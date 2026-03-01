from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from langchain_core.messages import HumanMessage, SystemMessage

from ai.contracts.schemas import TaskSpec, ToolRequest
from ai.llm.json_utils import parse_json_object


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


class ImplementorCompiler:
    def __init__(self, llm: Any, prompt_path: Path):
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")

    def _fallback_request(self, task: TaskSpec, operator_context: Dict[str, Any] | None = None) -> ToolRequest:
        """Minimal fallback — pass through task args, only add cookie if available."""
        action = task.action_hint
        args = dict(task.args)
        ctx = operator_context or {}

        if task.tool_hint == "sql_injection":
            opts = dict(args.get("options", {}))
            # Pass through whatever the head planner specified — don't add action-specific flags
            # Let the head planner's args be the source of truth
            cookie = ctx.get("auth_cookie", "")
            if cookie and "--cookie" not in opts:
                opts["--cookie"] = cookie
            args["options"] = opts

        if task.tool_hint == "nuclei_xss" and action != "scan":
            action = "scan"

        if task.tool_hint == "command_injection" and action not in {"scan", "enumerate", "execute_command", "read_remote_file"}:
            action = "scan"

        if task.tool_hint == "brute_force" and action not in {"scan_login", "run_bruteforce"}:
            action = "scan_login"

        return ToolRequest(
            task_id=task.id,
            tool_name=task.tool_hint,
            action=action,
            target_mode=task.target_mode,
            target=task.target,
            args=args,
            reasoning="Fallback: passing through task args as-is.",
        )

    def compile(self, *, task: TaskSpec, user_goal: str, operator_context: Dict[str, Any]) -> ToolRequest:
        # Build context excluding huge blobs
        sanitized_context = {
            k: v for k, v in operator_context.items()
            if k not in ("password", "authenticated_page_html", "navigation_structure")
        }

        prompt_input = {
            "user_goal": user_goal,
            "task": task.model_dump(),
            "operator_context": sanitized_context,
        }
        user_message = (
            "Convert this task into one executable ToolRequest.\n"
            "CRITICAL: The Head LLM's task.args already contains flags it chose. "
            "You MUST carry ALL of them into your ToolRequest args.options. "
            "You may ADD extra flags (e.g. --level, --risk) but NEVER drop the Head LLM's flags.\n"
            "The adapter will NOT add --dbs, --dump, --level, --risk, etc. for you — "
            "if a flag isn't in your output, it won't be used.\n"
            "CRITICAL: For sql_injection, the target URL MUST have a query parameter (e.g., ?id=1). "
            "If it doesn't, add one or use --data for POST injection.\n"
            "Return JSON only.\n"
            f"INPUT:\n{json.dumps(prompt_input, indent=2)}"
        )
        try:
            response = self.llm.invoke(
                [
                    SystemMessage(content=self.system_prompt),
                    HumanMessage(content=user_message),
                ]
            )
            text = _message_text(getattr(response, "content", response))
            parsed = parse_json_object(text)
            request = ToolRequest.model_validate(parsed)

            # Safety net: merge head LLM's flags into the implementor's output.
            # The implementor may have dropped flags the head chose.
            if task.tool_hint == "sql_injection":
                head_opts = (task.args or {}).get("options", {})
                if head_opts:
                    impl_args = dict(request.args)
                    impl_opts = dict(impl_args.get("options", {}))
                    for flag, value in head_opts.items():
                        if flag not in impl_opts and value not in (None, "", False):
                            impl_opts[flag] = value
                    impl_args["options"] = impl_opts
                    request = request.model_copy(update={"args": impl_args})

            return request
        except Exception:
            return self._fallback_request(task, operator_context)
