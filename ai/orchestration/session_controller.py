from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import Any, Dict

from ai.agents.head_qwen3_8b.planner import HeadPlanner
from ai.agents.implementor_qwen25coder_7b.compiler import ImplementorCompiler
from ai.analysis.result_analyzer import ResultAnalyzer
from ai.config.env_loader import load_project_env
from ai.execution.auth import attempt_pre_auth
from ai.execution.run_manager import RunManager
from ai.llm.ollama_client import build_ollama_clients, resolve_model_connection
from ai.orchestration.langgraph_flow import build_phase1_graph
from ai.orchestration.state import SessionState
from ai.rag.ingest import store_from_config
from ai.rag.retriever import ToolRetriever
from ai.reporting.report_generator import ReportGenerator
from ai.safety.action_filter import ActionFilter


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="PwnAI Phase 1 local single-session orchestrator")
    parser.add_argument("--goal", required=True, help="High-level objective for this session")
    parser.add_argument("--target-url", help="Target URL")
    parser.add_argument("--target-request-file", help="Target request file path")
    parser.add_argument("--username", help="Optional credential context for planning")
    parser.add_argument("--password", help="Optional credential context for planning")
    parser.add_argument("--login-url", help="Optional explicit login URL for pre-auth")
    parser.add_argument("--username-field", default="username", help="Login form username field name")
    parser.add_argument("--password-field", default="password", help="Login form password field name")
    parser.add_argument("--submit-field", help="Optional login form submit field name")
    parser.add_argument("--submit-value", default="Login", help="Value for submit field if provided")
    parser.add_argument("--extra-form-fields", help="Optional JSON object for extra login fields, e.g. '{\"csrf\":\"token\"}'")
    parser.add_argument("--auth-timeout", type=int, default=15, help="Timeout in seconds for pre-auth requests")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable TLS verification for pre-auth requests")
    parser.add_argument("--auth-probe-url", help="URL to probe after login to verify authenticated state (e.g. a dashboard page)")
    parser.add_argument("--auth-success-indicator", help="Text string expected in the body of a successfully authenticated page (e.g. 'Logout', 'Dashboard')")
    parser.add_argument("--auth-failure-indicator", help="Text string indicating auth failure (e.g. 'Invalid credentials', 'Login')")
    parser.add_argument(
        "--config",
        default="ai/config/models.local.yaml",
        help="Path to model/runtime config (supports per-role local/cloud providers)",
    )
    parser.add_argument(
        "--output-dir",
        default="artifacts/phase1",
        help="Output directory for reports and session outputs",
    )
    parser.add_argument("--auto-approve", action="store_true", help="Auto-approve all actions without prompts")
    parser.add_argument("--print-final-state", action="store_true", help="Print final state JSON summary")
    parser.add_argument("--max-tasks", type=int, help="Maximum total task count in this session")
    parser.add_argument("--max-replans", type=int, help="Maximum adaptive re-plan iterations")
    parser.add_argument("--max-new-tasks-per-replan", type=int, help="Maximum new tasks added per re-plan")
    # NEW: Add option for crawl depth
    parser.add_argument("--crawl-depth", type=int, default=2, help="Depth of endpoint crawling (0-3, default 2)")
    return parser


def _build_target(args: argparse.Namespace) -> Dict[str, Any]:
    has_url = bool(args.target_url)
    has_req = bool(args.target_request_file)
    if has_url == has_req:
        raise ValueError("Provide exactly one of --target-url or --target-request-file")
    if has_url:
        return {"url": args.target_url}
    return {"request_file": args.target_request_file}


def run_session(args: argparse.Namespace) -> SessionState:
    project_root = Path(__file__).resolve().parents[2]
    load_project_env(project_root)
    config_path = (project_root / args.config).resolve()

    head_llm, impl_llm, cfg = build_ollama_clients(config_path)
    head_conn = resolve_model_connection(cfg, "head")
    impl_conn = resolve_model_connection(cfg, "implementor")
    print(
        f"[LLM] head provider: {head_conn['provider']} | "
        f"base_url: {head_conn['base_url']} | model: {cfg['models']['head']['model']}"
    )
    print(
        f"[LLM] implementor provider: {impl_conn['provider']} | "
        f"base_url: {impl_conn['base_url']} | model: {cfg['models']['implementor']['model']}"
    )

    # ── Initialize RAG retriever ──
    rag_cfg = cfg.get("rag", {})
    rag_enabled = bool(rag_cfg.get("enabled", True))
    retriever = None

    if rag_enabled:
        try:
            store = store_from_config(cfg, project_root)
            retriever = ToolRetriever(store)
            print(f"[RAG] Tool knowledge base initialized ({store.document_count} vectors).")
        except Exception as exc:
            print(f"[RAG] WARNING: Failed to initialize RAG: {exc}")
            print("[RAG] Continuing without RAG retrieval (LLMs will use pretrained knowledge only).")
            retriever = None

    planner = HeadPlanner(
        llm=head_llm,
        prompt_path=project_root / "ai" / "agents" / "head_qwen3_8b" / "system_prompt.md",
        retriever=retriever,
    )
    compiler = ImplementorCompiler(
        llm=impl_llm,
        prompt_path=project_root / "ai" / "agents" / "implementor_qwen25coder_7b" / "system_prompt.md",
        retriever=retriever,
    )
    action_filter = ActionFilter(
        ask_permission=bool(cfg.get("runtime", {}).get("ask_permission_per_action", True)),
        auto_approve=bool(args.auto_approve or cfg.get("runtime", {}).get("auto_approve", False)),
    )
    run_manager = RunManager(project_root=project_root)
    analyzer = ResultAnalyzer()
    reporter = ReportGenerator()

    app = build_phase1_graph(
        planner=planner,
        implementor=compiler,
        action_filter=action_filter,
        run_manager=run_manager,
        analyzer=analyzer,
        reporter=reporter,
    ).compile()

    target_payload = _build_target(args)
    runtime_cfg = cfg.get("runtime", {})
    max_tasks = int(args.max_tasks if args.max_tasks is not None else runtime_cfg.get("max_tasks", 6))
    max_replans = int(args.max_replans if args.max_replans is not None else runtime_cfg.get("max_replans", 8))
    max_new_tasks_per_replan = int(
        args.max_new_tasks_per_replan
        if args.max_new_tasks_per_replan is not None
        else runtime_cfg.get("max_new_tasks_per_replan", 3)
    )

    operator_context: Dict[str, Any] = {
        "username": args.username,
        "password": args.password,
        "provided_credentials": {
            "username": args.username,
            "password": args.password,
        }
        if args.username and args.password
        else {},
        "phase": "phase1_react_rag" if retriever else "phase1_no_rag",
    }
    notes = []

    if target_payload.get("url") and args.username and args.password:
        extra_form_fields = {}
        if args.extra_form_fields:
            try:
                parsed_fields = json.loads(args.extra_form_fields)
                if isinstance(parsed_fields, dict):
                    extra_form_fields = {str(k): str(v) for k, v in parsed_fields.items()}
            except json.JSONDecodeError:
                notes.append("Invalid --extra-form-fields JSON; ignored.")

        auth_result = attempt_pre_auth(
            target_url=target_payload["url"],
            username=args.username,
            password=args.password,
            login_url=args.login_url,
            username_field=args.username_field,
            password_field=args.password_field,
            submit_field=args.submit_field,
            submit_value=args.submit_value,
            extra_form_fields=extra_form_fields,
            timeout_seconds=args.auth_timeout,
            verify_tls=not args.insecure_tls,
            auth_probe_url=getattr(args, "auth_probe_url", None),
            auth_success_indicator=getattr(args, "auth_success_indicator", None),
            auth_failure_indicator=getattr(args, "auth_failure_indicator", None),
            crawl_depth=int(args.crawl_depth),
        )
        operator_context["auth"] = auth_result.to_dict()
        if auth_result.success and auth_result.cookie:
            operator_context["auth_cookie"] = auth_result.cookie
            operator_context["auth_verified"] = auth_result.verified

            # NEW: Store full HTML context for LLM analysis
            if auth_result.authenticated_page_html:
                operator_context["authenticated_page_html"] = auth_result.authenticated_page_html
                operator_context["authenticated_page_url"] = auth_result.authenticated_page_url
                notes.append(f"Captured {len(auth_result.authenticated_page_html)} chars of authenticated page HTML for LLM analysis.")

            # NEW: Store navigation structure
            if auth_result.navigation_structure:
                operator_context["navigation_structure"] = auth_result.navigation_structure
                total_nav_items = sum(len(v) for v in auth_result.navigation_structure.values())
                notes.append(f"Extracted navigation structure with {total_nav_items} menu items.")

            if auth_result.discovered_endpoints:
                operator_context["discovered_endpoints"] = auth_result.discovered_endpoints
                notes.append(f"Discovered {len(auth_result.discovered_endpoints)} authenticated endpoint(s) for targeting.")
            if auth_result.verified:
                notes.append("Pre-auth succeeded with strict verification; session cookie injected into tool requests.")
            else:
                notes.append("Pre-auth succeeded (heuristic); cookie injected but auth not strictly verified.")
        else:
            operator_context["auth_verified"] = False
            notes.append(f"Pre-auth did not establish session: {auth_result.message}")

    session_id = f"session-{uuid.uuid4().hex[:8]}"
    initial_state: SessionState = {
        "session_id": session_id,
        "user_goal": args.goal,
        "target": target_payload,
        "operator_context": operator_context,
        "output_dir": str((project_root / args.output_dir).resolve()),
        "max_tasks": max_tasks,
        "max_replans": max_replans,
        "max_new_tasks_per_replan": max_new_tasks_per_replan,
        "notes": notes,
    }
    final_state = app.invoke(initial_state)
    return final_state


def main() -> None:
    parser = _build_parser()
    args = parser.parse_args()

    final_state = run_session(args)
    print("\n=== Phase 1 Session Complete ===")
    print(f"Session ID: {final_state.get('session_id')}")
    print(f"Tasks planned: {len(final_state.get('tasks', []))}")
    print(f"Runs executed: {len(final_state.get('runs', []))}")
    print(f"Findings generated: {len(final_state.get('findings', []))}")
    print(f"Report path: {final_state.get('report_path')}")

    if args.print_final_state:
        serializable = {}
        for key, value in final_state.items():
            if isinstance(value, list):
                serializable[key] = [
                    item.model_dump() if hasattr(item, "model_dump") else str(item)
                    for item in value
                ]
            elif hasattr(value, "model_dump"):
                serializable[key] = value.model_dump()
            else:
                serializable[key] = value
        print(json.dumps(serializable, indent=2))


if __name__ == "__main__":
    main()
