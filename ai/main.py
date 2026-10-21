from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict, List

from ai.orchestration.session_controller import run_session


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="PwnAI Phase 1 main runner (single case or multi-case batch)"
    )
    parser.add_argument("--cases-file", help="Path to JSON file containing test cases")
    parser.add_argument("--goal", help="Goal for single-case mode")
    parser.add_argument("--target-url", help="Target URL for single-case mode")
    parser.add_argument("--target-request-file", help="Request file target for single-case mode")
    parser.add_argument("--username", help="Optional username context")
    parser.add_argument("--password", help="Optional password context")
    parser.add_argument("--login-url", help="Optional explicit login URL for pre-auth")
    parser.add_argument("--username-field", default="username", help="Login form username field name")
    parser.add_argument("--password-field", default="password", help="Login form password field name")
    parser.add_argument("--submit-field", help="Optional login form submit field name")
    parser.add_argument("--submit-value", default="Login", help="Login form submit value")
    parser.add_argument("--extra-form-fields", help="Optional JSON object with extra login form fields")
    parser.add_argument("--auth-timeout", type=int, default=15, help="Timeout seconds for pre-auth requests")
    parser.add_argument("--insecure-tls", action="store_true", help="Disable TLS verification during pre-auth")
    parser.add_argument("--crawl-depth", type=int, default=2, help="Depth of endpoint crawling (0-3, default 2)")
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
    parser.add_argument("--print-final-state", action="store_true", help="Print final state for each case")
    parser.add_argument("--continue-on-error", action="store_true", help="Continue batch execution after a case fails")
    parser.add_argument("--max-tasks", type=int, help="Maximum total task count per session")
    parser.add_argument("--max-replans", type=int, help="Maximum adaptive re-plan iterations per session")
    parser.add_argument("--max-actions-per-objective", "--max-new-tasks-per-replan",
                        dest="max_actions_per_objective", type=int,
                        help="Maximum sequential tool actions per delegated objective (old flag is an alias)")
    parser.add_argument(
        "--summary-out",
        default="artifacts/phase1/batch_summary.json",
        help="Path to write run summary JSON",
    )
    return parser


def _validate_case(case: Dict[str, Any], idx: int) -> Dict[str, Any]:
    if not case.get("enabled", True):
        return case

    has_url = bool(case.get("target_url"))
    has_req = bool(case.get("target_request_file"))
    if has_url == has_req:
        raise ValueError(
            f"Case #{idx} invalid: provide exactly one of target_url or target_request_file"
        )
    if not case.get("goal"):
        raise ValueError(f"Case #{idx} invalid: missing goal")
    return case


def _load_cases_from_file(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, list):
        cases = payload
    else:
        cases = payload.get("cases", [])
    if not isinstance(cases, list) or not cases:
        raise ValueError(f"No cases found in {path}")
    validated: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        if not isinstance(case, dict):
            raise ValueError(f"Case #{idx} is not an object")
        validated.append(_validate_case(case, idx))
    return validated


def _single_case_from_args(args: argparse.Namespace) -> List[Dict[str, Any]]:
    case = {
        "name": "single-case",
        "enabled": True,
        "goal": args.goal,
        "target_url": args.target_url,
        "target_request_file": args.target_request_file,
        "username": args.username,
        "password": args.password,
        "login_url": args.login_url,
        "username_field": args.username_field,
        "password_field": args.password_field,
        "submit_field": args.submit_field,
        "submit_value": args.submit_value,
        "extra_form_fields": args.extra_form_fields,
        "auth_timeout": args.auth_timeout,
        "insecure_tls": args.insecure_tls,
        "crawl_depth": args.crawl_depth,
    }
    return [_validate_case(case, 1)]


def _build_run_args(base_args: argparse.Namespace, case: Dict[str, Any]) -> argparse.Namespace:
    return argparse.Namespace(
        goal=case["goal"],
        target_url=case.get("target_url"),
        target_request_file=case.get("target_request_file"),
        username=case.get("username"),
        password=case.get("password"),
        login_url=case.get("login_url", base_args.login_url),
        username_field=case.get("username_field", base_args.username_field),
        password_field=case.get("password_field", base_args.password_field),
        submit_field=case.get("submit_field", base_args.submit_field),
        submit_value=case.get("submit_value", base_args.submit_value),
        extra_form_fields=case.get("extra_form_fields", base_args.extra_form_fields),
        auth_timeout=int(case.get("auth_timeout", base_args.auth_timeout)),
        insecure_tls=bool(case.get("insecure_tls", base_args.insecure_tls)),
        crawl_depth=int(case.get("crawl_depth", base_args.crawl_depth)),
        config=case.get("config", base_args.config),
        output_dir=case.get("output_dir", base_args.output_dir),
        auto_approve=bool(case.get("auto_approve", base_args.auto_approve)),
        print_final_state=bool(case.get("print_final_state", base_args.print_final_state)),
        max_tasks=case.get("max_tasks", base_args.max_tasks),
        max_replans=case.get("max_replans", base_args.max_replans),
        max_actions_per_objective=case.get("max_actions_per_objective", case.get("max_new_tasks_per_replan", base_args.max_actions_per_objective)),
    )


def _serialize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    def encode(value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return encode(value.model_dump())
        if isinstance(value, dict):
            return {key: encode(item) for key, item in value.items()}
        if isinstance(value, list):
            return [encode(item) for item in value]
        return value
    return encode(state)


def main() -> None:
    args = _build_parser().parse_args()
    print(
        "[!] Authorized use only: run this system only on assets with explicit written permission "
        "and in-scope targets."
    )

    if args.cases_file:
        cases = _load_cases_from_file(Path(args.cases_file))
    else:
        cases = _single_case_from_args(args)

    summary: List[Dict[str, Any]] = []
    for idx, case in enumerate(cases, start=1):
        if not case.get("enabled", True):
            summary.append(
                {
                    "index": idx,
                    "name": case.get("name", f"case-{idx:02d}"),
                    "status": "skipped",
                    "reason": "Case disabled",
                }
            )
            continue

        run_args = _build_run_args(args, case)
        name = case.get("name", f"case-{idx:02d}")
        print(f"\n=== Running {name} ({idx}/{len(cases)}) ===")
        try:
            final_state = run_session(run_args)
            record = {
                "index": idx,
                "name": name,
                "status": "ok",
                "session_id": final_state.get("session_id"),
                "tasks_planned": len(final_state.get("tasks", [])),
                "runs_executed": len(final_state.get("runs", [])),
                "findings": len(final_state.get("findings", [])),
                "report_path": final_state.get("report_path"),
            }
            if run_args.print_final_state:
                record["final_state"] = _serialize_state(final_state)
            summary.append(record)
            print(
                f"Completed {name}: tasks={record['tasks_planned']}, runs={record['runs_executed']}, "
                f"findings={record['findings']}"
            )
        except Exception as exc:
            summary.append(
                {
                    "index": idx,
                    "name": name,
                    "status": "failed",
                    "error": str(exc),
                }
            )
            print(f"Failed {name}: {exc}")
            if not args.continue_on_error:
                break

    out_path = Path(args.summary_out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nBatch summary written to: {out_path}")


if __name__ == "__main__":
    main()
