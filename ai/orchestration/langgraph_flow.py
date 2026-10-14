from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from langgraph.graph import END, START, StateGraph

from ai.agents.head.planner import HeadPlanner
from ai.agents.head.memory import load_feedback
from ai.agents.implementor.compiler import ImplementorCompiler
from ai.analysis.result_analyzer import ResultAnalyzer
from ai.contracts.schemas import ActionDecision, ExecutionResult, Finding, TargetSpec, TaskRunRecord, TaskSpec, ToolRequest
from ai.execution.auth import refresh_session_cookie
from ai.execution.run_manager import RunManager
from ai.orchestration.state import SessionState
from ai.reporting.report_generator import ReportGenerator
from ai.safety.action_filter import ActionFilter


def build_phase1_graph(
    *,
    planner: HeadPlanner,
    implementor: ImplementorCompiler,
    action_filter: ActionFilter,
    run_manager: RunManager,
    analyzer: ResultAnalyzer,
    reporter: ReportGenerator,
) -> StateGraph:
    graph = StateGraph(SessionState)

    def _compile_handoff(decision: HeadPlanner.PlannerDecision, state: SessionState,
                         task_id: str) -> tuple[List[TaskSpec], Dict[str, Dict[str, Any]], Dict[str, Any]]:
        iteration = {
            "observations": list(decision.observations),
            "observation_summary": decision.observation_summary,
            "next_step": decision.handoff.next_step if decision.handoff else decision.stop_reason,
            "memory_notes": list(decision.task_notes),
            "decision": "stop" if decision.should_stop else "continue",
            "task_id": task_id,
            "implementor_steps": [],
            "selected_candidate_id": decision.selected_candidate_id,
            "search_trace": decision.search_trace,
        }
        if decision.should_stop or decision.handoff is None:
            return [], {}, iteration
        try:
            request = implementor.compile(handoff=decision.handoff, user_goal=state["user_goal"],
                                          task_id=task_id, trace_sink=iteration["implementor_steps"])
            iteration["tool_request"] = request.model_dump()
            task = TaskSpec(
                id=task_id, title=decision.handoff.next_step, objective=decision.handoff.next_step,
                rationale=decision.handoff.observation_summary, tool_hint=request.tool_name,
                action_hint=request.action, target_mode=request.target_mode,
                target=request.target, args=request.args,
            )
        except Exception as exc:
            decision.should_stop = True
            decision.stop_reason = f"Implementor could not produce a grounded request: {exc}"
            decision.thinking_summary.append(decision.stop_reason)
            iteration["compilation_error"] = decision.stop_reason
            return [], {}, iteration
        return [task], {task_id: request.model_dump()}, iteration

    def _task_signature(task: TaskSpec) -> str:
        args_blob = json.dumps(task.args, sort_keys=True, ensure_ascii=True)
        target_blob = json.dumps(task.target.model_dump(), sort_keys=True, ensure_ascii=True)
        return f"{task.tool_hint}|{task.action_hint}|{task.target_mode}|{target_blob}|{args_blob}"

    def _print_head_trace(title: str, summary: List[str], tasks: List[TaskSpec]) -> None:
        print(f"\n{'='*60}")
        print(f"[Head Planner] {title}")
        print(f"{'='*60}")
        if summary:
            print("  Observations:")
            for line in summary:
                print(f"    > {line}")
        if tasks:
            print(f"  Tasks ({len(tasks)}):")
            for task in tasks:
                target_str = task.target.url or task.target.request_file or "?"
                print(f"    [{task.id}] {task.tool_hint}/{task.action_hint} -> {target_str}")
                print(f"           {task.title} (P{task.priority})")
        print(f"{'='*60}")

    def _inject_auth_cookie(request: ToolRequest, state: SessionState) -> ToolRequest:
        """Inject the current auth cookie into the request.

        ALWAYS overwrites any existing cookie value so the request uses
        the most recent session cookie from operator_context, not a
        stale one the LLM may have baked in from an earlier planning step.
        """
        operator_context = state.get("operator_context", {})
        cookie = str(operator_context.get("auth_cookie") or "").strip()
        if not cookie:
            return request

        args = dict(request.args)
        if request.tool_name == "sql_injection":
            options = dict(args.get("options", {}))
            options["--cookie"] = cookie          # overwrite, not setdefault
            args["options"] = options
        else:
            args["cookie"] = cookie                # overwrite, not setdefault
        return request.model_copy(update={"args": args})

    def _looks_login_like_url(url: str) -> bool:
        url_l = (url or "").lower()
        login_indicators = ["login", "signin", "sign-in", "log-in", "auth"]
        return any(ind in url_l for ind in login_indicators)

    def _select_keepalive_url(operator_context: Dict[str, object], target_url: str) -> str:
        """Choose a stable authenticated URL for keepalive to avoid login redirects."""
        candidates: List[str] = []

        auth_url = str(operator_context.get("authenticated_page_url") or "").strip()
        if auth_url:
            candidates.append(auth_url)

        discovered = operator_context.get("discovered_endpoints", [])
        if isinstance(discovered, list):
            for ep in discovered:
                if isinstance(ep, str) and ep.strip():
                    candidates.append(ep.strip())
                    break

        if target_url:
            candidates.append(str(target_url).strip())

        for cand in candidates:
            if cand and not _looks_login_like_url(cand):
                return cand

        return candidates[0] if candidates else ""

    def _detect_login_redirect(result: ExecutionResult) -> bool:
        """Check if execution output indicates the server REJECTED the session.

        Only triggers on strong evidence of actual HTTP redirect to a login
        page — NOT on tool output that merely mentions login URLs (e.g.
        sqlmap logging that it visited login.php during crawling).
        """
        combined = ((result.stdout or "") + (result.stderr or "")).lower()
        # Require explicit redirect language — these mean the server itself
        # sent us back to a login page, not just that the tool saw one.
        strong_indicators = [
            "redirect to login",
            "got a 302 redirect",
            "session expired",
            "session has expired",
            "location: /login",
            "location: /signin",
            "location: /sign-in",
            "303 see other",
        ]
        return any(ind in combined for ind in strong_indicators)

    def _plan_node(state: SessionState) -> Dict[str, object]:
        target = TargetSpec.model_validate(state["target"])
        max_tasks = int(state.get("max_tasks", 6))
        operator_context = state.get("operator_context", {})

        # Display auth status at session start
        print(f"\n{'='*60}")
        print("[Session] Starting Phase 1 Planning")
        print(f"  Goal: {state['user_goal']}")
        print(f"  Target: {state['target']}")
        auth_info = operator_context.get("auth", {})
        if auth_info:
            print(f"  Auth attempted: {auth_info.get('attempted')}")
            print(f"  Auth success: {auth_info.get('success')}")
            print(f"  Auth verified: {auth_info.get('verified', 'N/A')}")
            print(f"  Auth cookie: {'present' if operator_context.get('auth_cookie') else 'missing'}")
            discovered = operator_context.get("discovered_endpoints", [])
            if discovered:
                print(f"  Discovered endpoints: {len(discovered)}")
                for ep in discovered[:5]:
                    print(f"    - {ep}")
        elif operator_context.get("username"):
            print("  Credentials provided but auth not attempted (no URL target?)")
        else:
            print("  No credentials provided")
        print(f"{'='*60}")

        # Plan ONE task at a time — the planner picks the single most
        # important next step, executes it, observes the output, then
        # the re-planner decides what to do next.
        prior_session_feedback = load_feedback(Path(state["output_dir"]), state["target"], state["user_goal"])
        decision = planner.plan(
            user_goal=state["user_goal"],
            target=target,
            operator_context=operator_context,
            max_tasks=1,
            prior_session_feedback=prior_session_feedback,
        )
        _print_head_trace("Initial Plan", decision.thinking_summary, [])
        tasks, prepared_requests, iteration = _compile_handoff(decision, state, "task-01")
        existing_notes = list(state.get("notes", []))
        planning_trace = list(state.get("planning_trace", []))
        planning_trace.append("Initial plan: " + " | ".join(decision.thinking_summary))
        if decision.stop_reason:
            existing_notes.append(f"Head planner stop reason: {decision.stop_reason}")
        return {
            "tasks": tasks,
            "task_index": 0,
            "runs": [],
            "findings": [],
            "notes": existing_notes,
            "llm_notes": list(decision.task_notes),
            "prepared_requests": prepared_requests,
            "planning_trace": planning_trace,
            "head_iterations": [iteration],
            "candidate_frontier": decision.candidate_frontier,
            "task_candidates": {task.id: decision.selected_candidate_id for task in tasks},
            "attempt_reflections": [],
            "prior_session_feedback": prior_session_feedback,
            "replan_count": 0,
            "finished": len(tasks) == 0,
            "stop_reason": decision.stop_reason if decision.should_stop else "",
        }

    def _select_node(state: SessionState) -> Dict[str, object]:
        idx = state.get("task_index", 0)
        tasks = state.get("tasks", [])
        if idx >= len(tasks):
            return {"finished": True, "current_task": None}
        return {"finished": False, "current_task": tasks[idx]}

    def _select_route(state: SessionState) -> str:
        return "generate_report" if state.get("finished") else "compile_request"

    def _compile_node(state: SessionState) -> Dict[str, object]:
        current_task = state.get("current_task")
        if current_task is None:
            return {"current_request": None}
        request = ToolRequest.model_validate(state["prepared_requests"][current_task.id])
        request = _inject_auth_cookie(request, state)
        return {"current_request": request}

    def _review_node(state: SessionState) -> Dict[str, object]:
        current_request = state.get("current_request")
        if current_request is None:
            return {"current_decision": ActionDecision(approved=False, reason="No request to review.")}
        decision = action_filter.review(current_request)
        return {"current_decision": decision}

    def _execute_node(state: SessionState) -> Dict[str, object]:
        current_task: TaskSpec = state["current_task"]  # type: ignore[assignment]
        current_request: ToolRequest = state["current_request"]  # type: ignore[assignment]
        decision: ActionDecision = state["current_decision"]  # type: ignore[assignment]

        if not decision.approved:
            denied_result = ExecutionResult(
                tool_name=current_request.tool_name,
                action=current_request.action,
                success=False,
                returncode=1,
                stderr="Execution skipped due to operator denial.",
                error="Execution skipped by action filter.",
            )
            record = TaskRunRecord(
                task=current_task,
                request=current_request,
                decision=decision,
                result=denied_result,
                status="denied",
            )
            runs = list(state.get("runs", []))
            runs.append(record)
            return {"current_result": denied_result, "runs": runs}

        # -- Session keepalive: refresh cookie before tool execution --
        # A lightweight GET to keep the PHP session alive. We do NOT
        # re-login mid-session here; we only refresh if the current
        # cookie still works.
        operator_context = dict(state.get("operator_context", {}))
        cookie = str(operator_context.get("auth_cookie") or "").strip()
        target_info = state.get("target", {})
        target_url = target_info.get("url", "")
        keepalive_url = _select_keepalive_url(operator_context, target_url)
        if cookie and keepalive_url:
            refreshed = refresh_session_cookie(
                target_url=keepalive_url,
                current_cookie=cookie,
                username=operator_context.get("username", ""),
                password=operator_context.get("password", ""),
                login_url=(operator_context.get("auth", {}) or {}).get("login_url"),
                allow_relogin=False,
            )
            if refreshed != cookie:
                print(f"[Execute] Session cookie refreshed before {current_task.id}.")
                operator_context["auth_cookie"] = refreshed
                # Re-inject the fresh cookie into the request
                updated_state = dict(state)
                updated_state["operator_context"] = operator_context
                current_request = _inject_auth_cookie(current_request, updated_state)  # type: ignore[arg-type]

        print(f"[Implementor] Execute tool: {current_request.tool_name}/{current_request.action} "
              f"| task={current_request.task_id}", flush=True)
        try:
            result = run_manager.execute(current_request)
        except Exception as exc:
            result = ExecutionResult(
                tool_name=current_request.tool_name, action=current_request.action,
                success=False, returncode=1, error=str(exc), stderr=str(exc))

        # --- SESSION-EXPIRY DETECTION (informational only) ---
        # The system authenticates once at session start and reuses that
        # cookie for every task.  If a task's output shows a genuine
        # redirect-to-login, we flag it in notes so the re-planner can
        # decide what to do (e.g. stop, or the operator can restart with
        # fresh creds).  We do NOT re-auth mid-session because:
        #   1. Tools like sqlmap mention "login.php" in normal output
        #      (visited URLs, crawl logs) which triggers false positives.
        #   2. Re-authing produces a new cookie but the request already
        #      had the old one baked in, so retries were using stale creds.
        #   3. Silently retrying masks real failures from the re-planner.
        notes = list(state.get("notes", []))
        if _detect_login_redirect(result):
            msg = (f"Possible session expiry detected in {current_task.id} output. "
                   "The re-planner will evaluate whether to continue or stop.")
            print(f"\n[Execute] {msg}")
            notes.append(msg)

        # ── Print task output so the operator sees results before replan ──
        duration = result.duration_seconds
        print(f"\n{'='*60}")
        print(f"[Task Output] {current_task.id} | {current_request.tool_name}/{current_request.action} | "
              f"rc={result.returncode} | {duration:.1f}s")
        print(f"{'='*60}")
        if result.stdout:
            print(f"[STDOUT]\n{result.stdout}")
        if result.stderr:
            print(f"[STDERR]\n{result.stderr}")
        if result.error:
            print(f"[ERROR] {result.error}")
        if not result.stdout and not result.stderr and not result.error:
            print("(no output)")
        print(f"{'='*60}")

        status = "executed" if result.success else "failed"
        record = TaskRunRecord(
            task=current_task,
            request=current_request,
            decision=decision,
            result=result,
            status=status,  # type: ignore[arg-type]
        )
        runs = list(state.get("runs", []))
        runs.append(record)
        return_dict: Dict[str, object] = {"current_result": result, "runs": runs, "notes": notes}
        # Propagate refreshed cookie into state so subsequent tasks use it
        if operator_context.get("auth_cookie") != cookie:
            return_dict["operator_context"] = operator_context
        return return_dict

    def _analyze_node(state: SessionState) -> Dict[str, object]:
        current_request = state.get("current_request")
        current_result = state.get("current_result")
        if current_request is None or current_result is None:
            return {}
        new_findings = analyzer.analyze(current_request, current_result)
        findings: List[Finding] = list(state.get("findings", []))
        findings.extend(new_findings)
        return {"findings": findings}

    def _replan_node(state: SessionState) -> Dict[str, object]:
        next_index = state.get("task_index", 0) + 1
        tasks = state.get("tasks", [])
        notes = list(state.get("notes", []))
        llm_notes = list(state.get("llm_notes", []))
        planning_trace = list(state.get("planning_trace", []))

        current_task = state.get("current_task")
        current_result = state.get("current_result")
        if current_task is None or current_result is None:
            return {
                "task_index": next_index,
                "finished": next_index >= len(tasks),
                "notes": notes,
                "llm_notes": llm_notes,
                "planning_trace": planning_trace,
            }

        max_replans = int(state.get("max_replans", 8))
        replan_count = int(state.get("replan_count", 0))
        # One task at a time — the re-planner generates exactly the next step.
        max_new_tasks = 1
        # Total session cap: initial task(s) + one per replan cycle.
        # Do NOT use state["max_tasks"] here — that controls per-call task
        # generation, not the session-wide ceiling.
        max_total_tasks = 1 + max_replans

        if replan_count >= max_replans:
            notes.append("Reached max re-plan limit; continuing with existing queue only.")
            return {
                "task_index": next_index,
                "finished": next_index >= len(tasks),
                "notes": notes,
                "llm_notes": llm_notes,
                "planning_trace": planning_trace,
                "replan_count": replan_count,
                "stop_reason": "Reached max re-plan limit.",
            }

        decision = planner.replan(
            user_goal=state["user_goal"],
            target=TargetSpec.model_validate(state["target"]),
            operator_context=state.get("operator_context", {}),
            current_task=current_task,
            current_result=current_result,
            findings=state.get("findings", []),
            remaining_tasks=tasks[next_index:],
            prior_notes=llm_notes,
            replan_iteration=replan_count,
            max_new_tasks=max_new_tasks,
            iteration_history=state.get("head_iterations", []),
            execution_history=[run.model_dump() for run in state.get("runs", [])],
            runtime_notes=notes,
            candidate_frontier=state.get("candidate_frontier", []),
            reflections=state.get("attempt_reflections", []),
            prior_session_feedback=state.get("prior_session_feedback", []),
        )
        _print_head_trace(
            f"Adaptive Re-plan #{replan_count + 1} (after {current_task.id})",
            decision.thinking_summary,
            [],
        )
        produced_tasks, new_requests, iteration = _compile_handoff(
            decision, state, f"task-{len(tasks) + 1:02d}")
        head_iterations = list(state.get("head_iterations", [])) + [iteration]
        prepared_requests = dict(state.get("prepared_requests", {}))
        prepared_requests.update(new_requests)
        task_candidates = dict(state.get("task_candidates", {}))
        planning_trace.append(f"Re-plan #{replan_count + 1}: " + " | ".join(decision.thinking_summary))
        # Accumulate the LLM's own notes about this task's result.
        if decision.task_notes:
            llm_notes = llm_notes + decision.task_notes
            print(f"[Replan] LLM wrote {len(decision.task_notes)} note(s) for future context.")

        existing_signatures = {_task_signature(task) for task in tasks}
        appended_tasks: List[TaskSpec] = []
        for task in produced_tasks:
            sig = _task_signature(task)
            if sig in existing_signatures:
                notes.append(f"Skipped repeated executable request for {task.id}; no new action executed.")
                continue
            if len(tasks) + len(appended_tasks) >= max_total_tasks:
                notes.append("Adaptive tasks skipped because max task cap was reached.")
                break
            new_idx = len(tasks) + len(appended_tasks) + 1
            appended_tasks.append(
                task.model_copy(update={"id": f"task-{new_idx:02d}", "priority": min(new_idx, 10)})
            )
            task_candidates[appended_tasks[-1].id] = decision.selected_candidate_id
            existing_signatures.add(sig)

        if appended_tasks:
            tasks = list(tasks) + appended_tasks
            notes.append(f"Adaptive re-planning appended {len(appended_tasks)} task(s).")

        queue_empty = next_index >= len(tasks)
        if decision.should_stop and queue_empty:
            stop_reason = decision.stop_reason or "Head planner requested stop after re-plan."
            notes.append(f"Session finished by head planner: {stop_reason}")
            return {
                "task_index": next_index,
                "tasks": tasks,
                "finished": True,
                "stop_reason": stop_reason,
                "notes": notes,
                "llm_notes": llm_notes,
                "planning_trace": planning_trace,
                "replan_count": replan_count + 1,
                "prepared_requests": prepared_requests,
                "head_iterations": head_iterations,
                "candidate_frontier": decision.candidate_frontier,
                "task_candidates": task_candidates,
            }

        return {
            "task_index": next_index,
            "tasks": tasks,
            "finished": queue_empty,
            "notes": notes,
            "llm_notes": llm_notes,
            "planning_trace": planning_trace,
            "replan_count": replan_count + 1,
            "prepared_requests": prepared_requests,
            "head_iterations": head_iterations,
            "candidate_frontier": decision.candidate_frontier,
            "task_candidates": task_candidates,
            "stop_reason": ("No new executable objective remained after duplicate/cap checks."
                            if queue_empty else state.get("stop_reason", "")),
        }

    def _replan_route(state: SessionState) -> str:
        return "generate_report" if state.get("finished") else "select_task"

    def _reflect_node(state: SessionState) -> Dict[str, object]:
        runs = state.get("runs", [])
        if not runs:
            return {}
        run = runs[-1]
        reflections = list(state.get("attempt_reflections", []))
        if any(item["attempt_id"] == run.task.id for item in reflections):
            return {}
        reflection = planner.reflect(
            user_goal=state["user_goal"], attempt_id=run.task.id, attempt=run.model_dump(),
            findings=state.get("findings", []), prior_reflections=reflections)
        reflections.append(reflection.model_dump())
        frontier = [dict(node) for node in state.get("candidate_frontier", [])]
        candidate_id = state.get("task_candidates", {}).get(run.task.id)
        for node in frontier:
            if node["candidate_id"] == candidate_id:
                node.update(status="attempted", attempt_id=run.task.id, outcome=reflection.outcome)
        return {"attempt_reflections": reflections, "candidate_frontier": frontier}

    def _report_node(state: SessionState) -> Dict[str, object]:
        reflections = list(state.get("attempt_reflections", []))
        frontier = [dict(node) for node in state.get("candidate_frontier", [])]
        # Compilation failures do not visit the execution/reflection nodes. Retain them too.
        for iteration in state.get("head_iterations", []):
            if iteration.get("compilation_error") and not any(
                item["attempt_id"] == iteration["task_id"] for item in reflections
            ):
                reflection = planner.reflect(
                    user_goal=state["user_goal"], attempt_id=iteration["task_id"], attempt=iteration,
                    compilation_error=iteration["compilation_error"],
                    findings=state.get("findings", []), prior_reflections=reflections)
                reflections.append(reflection.model_dump())
                for node in frontier:
                    if node["candidate_id"] == iteration.get("selected_candidate_id"):
                        node.update(status="attempted", attempt_id=iteration["task_id"],
                                    outcome="compilation_failure")
        session_reflection = planner.consolidate(
            user_goal=state["user_goal"], reflections=reflections,
            stop_reason=state.get("stop_reason", "") or "No further objectives queued.")
        output_dir = Path(state["output_dir"])
        output_dir.mkdir(parents=True, exist_ok=True)
        feedback_path = output_dir / f"{state['session_id']}_feedback.json"
        feedback_path.write_text(json.dumps({
            "session_id": state["session_id"], "target": state["target"],
            "user_goal": state["user_goal"], **session_reflection.model_dump(),
            "search_history": [{
                "task_id": iteration["task_id"],
                "selected_candidate_id": iteration.get("selected_candidate_id", ""),
                "search_trace": iteration.get("search_trace", []),
                "compilation_error": iteration.get("compilation_error", ""),
            } for iteration in state.get("head_iterations", [])],
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        report_path = reporter.write_markdown_report(
            output_dir=Path(state["output_dir"]),
            session_id=state["session_id"],
            user_goal=state["user_goal"],
            target=state["target"],
            operator_context=state.get("operator_context", {}),
            notes=state.get("notes", []),
            runs=state.get("runs", []),
            findings=state.get("findings", []),
            planning_trace=state.get("planning_trace", []),
            stop_reason=str(state.get("stop_reason", "")),
            session_reflection=session_reflection.model_dump(),
        )
        return {"report_path": str(report_path), "attempt_reflections": reflections,
                "session_reflection": session_reflection.model_dump(), "feedback_path": str(feedback_path),
                "candidate_frontier": frontier}

    graph.add_node("plan_tasks", _plan_node)
    graph.add_node("select_task", _select_node)
    graph.add_node("compile_request", _compile_node)
    graph.add_node("review_action", _review_node)
    graph.add_node("execute_request", _execute_node)
    graph.add_node("analyze_result", _analyze_node)
    graph.add_node("reflect_attempt", _reflect_node)
    graph.add_node("replan_task", _replan_node)
    graph.add_node("generate_report", _report_node)

    graph.add_edge(START, "plan_tasks")
    graph.add_edge("plan_tasks", "select_task")
    graph.add_conditional_edges("select_task", _select_route, {"compile_request": "compile_request", "generate_report": "generate_report"})
    graph.add_edge("compile_request", "review_action")
    graph.add_edge("review_action", "execute_request")
    graph.add_edge("execute_request", "analyze_result")
    graph.add_edge("analyze_result", "reflect_attempt")
    graph.add_edge("reflect_attempt", "replan_task")
    graph.add_conditional_edges("replan_task", _replan_route, {"select_task": "select_task", "generate_report": "generate_report"})
    graph.add_edge("generate_report", END)

    return graph
