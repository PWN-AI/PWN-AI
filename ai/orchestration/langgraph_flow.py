from __future__ import annotations

import json
import time
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List

from langgraph.graph import END, START, StateGraph

from ai.agents.head.planner import HeadPlanner, ObservationHandoff
from ai.agents.feedback.agent import FeedbackAgent
from ai.agents.feedback.validation import required_outcome
from ai.agents.head.memory import build_working_memory
from ai.agents.implementor.compiler import ImplementorCompiler
from ai.agents.implementor.contracts import ObjectiveReport
from ai.analysis.result_analyzer import ResultAnalyzer
from ai.contracts.schemas import ActionDecision, ExecutionResult, Finding, TargetSpec, TaskRunRecord, TaskSpec, ToolRequest
from ai.execution.auth import refresh_session_cookie
from ai.execution.run_manager import RunManager
from ai.orchestration.state import SessionState
from ai.reporting.report_generator import ReportGenerator
from ai.safety.action_filter import ActionFilter


def build_phase1_graph(*, planner: HeadPlanner, feedback: FeedbackAgent, implementor: ImplementorCompiler,
                       action_filter: ActionFilter, run_manager: RunManager,
                       analyzer: ResultAnalyzer, reporter: ReportGenerator) -> StateGraph:
    graph = StateGraph(SessionState)

    def _working_memory(state: SessionState) -> Dict[str, Any]:
        memory = build_working_memory(
            session_id=state["session_id"], user_goal=state["user_goal"], target=state["target"],
            operator_context=state.get("operator_context", {}), runs=state.get("runs", []),
            reflections=state.get("attempt_reflections", []), findings=state.get("findings", []),
            iterations=state.get("head_iterations", []) + state.get("attempt_records", []),
            runtime_notes=state.get("notes", []), iteration=int(state.get("replan_count", 0)),
            focus=[state.get("active_handoff", {})],
            constraints={key: state.get(key, default) for key, default in (
                ("max_tasks", 12), ("max_replans", 8), ("max_actions_per_objective", 3),
                ("objective_timeout_seconds", 600), ("max_consecutive_failures", 2))})
        memory["goal_and_constraints"]["runtime_limits"].update(
            remaining_replans=max(0, state.get("max_replans", 8) - state.get("replan_count", 0)),
            remaining_actions=max(0, state.get("max_tasks", 12) - len(state.get("runs", []))))
        memory["objective_feedback"] = deepcopy(state.get("objective_feedback", []))
        return memory

    def _signature(request: ToolRequest) -> str:
        return json.dumps({key: request.model_dump()[key] for key in (
            "tool_name", "action", "target_mode", "target", "args")}, sort_keys=True, ensure_ascii=False)

    def _handoff(state: SessionState) -> ObservationHandoff:
        data = dict(state["active_handoff"])
        data["target"] = TargetSpec.model_validate(data["target"])
        return ObservationHandoff(**data)

    def _runtime_report(reason: str) -> Dict[str, Any]:
        return ObjectiveReport(status="inconclusive", summary=reason,
                               remaining_uncertainty=["The delegated objective may remain unresolved."]).model_dump()

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

    def _initialize_node(state: SessionState) -> Dict[str, Any]:
        # Every invocation is a new session, even when supplied a prior final state.
        fresh = {**state, "runs": [], "findings": [], "head_iterations": [], "attempt_records": [],
                 "attempt_reflections": [], "objective_feedback": [], "tasks": [], "planning_trace": [],
                 "replan_count": 0, "next_task_number": 1, "prepared_requests": {},
                 "notes": list(state.get("notes", [])), "session_feedback_steps": [], "session_reflection": {},
                 "active_handoff": {}, "implementor_state": {}, "objective_report": {}, "stop_reason": ""}
        for key, default in (("max_tasks", 12), ("max_replans", 8), ("max_actions_per_objective", 3),
                             ("objective_timeout_seconds", 600), ("max_consecutive_failures", 2)):
            value = state.get(key, default)
            if value < (0 if key == "max_replans" else 1):
                raise ValueError(f"Invalid runtime limit: {key}")
            fresh[key] = value
        return fresh

    def _plan_node(state: SessionState) -> Dict[str, Any]:
        memory = _working_memory(state)
        kwargs = dict(user_goal=state["user_goal"], target=TargetSpec.model_validate(state["target"]),
                      operator_context=state.get("operator_context", {}), working_memory=memory)
        decision = planner.replan(**kwargs) if state["replan_count"] else planner.plan(**kwargs)
        objective_id = f"objective-{state['replan_count'] + 1:02d}"
        iteration = {"objective_id": objective_id, "task_id": objective_id,
                     "next_step": decision.handoff.next_step if decision.handoff else "",
                     "memory_notes": list(decision.task_notes), "search_trace": decision.search_trace,
                     "decision": "stop" if decision.should_stop else "continue", "implementor_steps": []}
        print(f"[Head Planner] {objective_id}: " + (
            decision.stop_reason if decision.should_stop else iteration["next_step"]), flush=True)
        updates = {**state, "head_iterations": state["head_iterations"] + [iteration],
                   "planning_trace": state["planning_trace"] + [" | ".join(decision.thinking_summary)],
                   "active_handoff": decision.handoff.model_dump() if decision.handoff else {},
                   "objective_id": objective_id, "objective_start_run": len(state["runs"]),
                   "objective_start_attempt": len(state["attempt_records"]),
                   "objective_started_at": time.monotonic(), "implementor_state": {},
                   "objective_report": {}, "return_reason": "", "consecutive_failures": 0,
                   "result_fingerprints": [], "consecutive_unchanged_results": 0,
                   "current_request": None, "current_task": None, "current_result": None,
                   "finished": decision.should_stop or decision.handoff is None,
                   "stop_reason": decision.stop_reason if decision.should_stop else ""}
        updates["working_memory"] = _working_memory(updates)
        return updates

    def _plan_route(state: SessionState) -> str:
        return "generate_report" if state.get("finished") else "implementor_step"

    def _implementor_node(state: SessionState) -> Dict[str, Any]:
        local = deepcopy(state.get("implementor_state", {}))
        iterations = deepcopy(state["head_iterations"])
        attempts = list(state["attempt_records"])
        number = state["next_task_number"]
        task_id = f"task-{number:02d}"
        action_count = len(attempts) - state["objective_start_attempt"]
        seconds_left = max(0, state["objective_timeout_seconds"] - (time.monotonic() - state["objective_started_at"]))
        reason = state.get("return_reason", "")
        if len(state["runs"]) >= state["max_tasks"]:
            reason = "Reached total action limit."
        elif seconds_left <= 0:
            reason = "Objective time budget exhausted."
        elif action_count >= state["max_actions_per_objective"]:
            reason = "Objective action budget exhausted."
        elif state.get("consecutive_failures", 0) >= state["max_consecutive_failures"]:
            reason = "Repeated execution failures; return for strategic review."
        elif state.get("consecutive_unchanged_results", 0) >= 2:
            reason = "Repeated unchanged execution results; return for strategic review."
        limits = {"can_execute": not bool(reason), "return_reason": reason,
                  "deadline": state["objective_started_at"] + state["objective_timeout_seconds"],
                  "remaining_actions": max(0, state["max_actions_per_objective"] - action_count),
                  "remaining_seconds": seconds_left}
        updates: Dict[str, Any] = {"implementor_state": local, "head_iterations": iterations,
                                   "current_request": None, "return_reason": reason}
        if seconds_left <= 0:
            updates["objective_report"] = _runtime_report(reason)
            return updates
        try:
            result = implementor.advance(handoff=_handoff(state), user_goal=state["user_goal"],
                                         task_id=task_id, local_state=local, limits=limits,
                                         trace_sink=iterations[-1]["implementor_steps"])
        except Exception as exc:
            message = f"Implementor could not continue: {exc}"
            attempts.append({"task_id": task_id, "next_step": state["active_handoff"]["next_step"],
                             "compilation_error": message, "objective_id": state["objective_id"]})
            updates.update(attempt_records=attempts, next_task_number=number + 1,
                           objective_report=_runtime_report(message))
            updates["working_memory"] = _working_memory({**state, **updates})
            return updates
        if isinstance(result, ObjectiveReport):
            updates["objective_report"] = result.model_dump()
            # Runtime limits override a model's optimistic completion assessment.
            if reason:
                updates["objective_report"]["runtime_return_reason"] = reason
            return updates
        # Check the deadline again after model/retrieval work and before any approval/execution.
        if time.monotonic() - state["objective_started_at"] >= state["objective_timeout_seconds"]:
            updates["objective_report"] = _runtime_report("Objective time budget exhausted before execution.")
            return updates
        result = _inject_auth_cookie(result, state)
        duplicate = next((run for run in state["runs"] if _signature(run.request) == _signature(result)), None)
        entry = {"task_id": task_id, "next_step": state["active_handoff"]["next_step"],
                 "tool_request": result.model_dump(), "objective_id": state["objective_id"]}
        if duplicate:
            message = f"Request matches {duplicate.task.id}; no new execution occurred."
            entry["rejection"] = {"duplicate_of": duplicate.task.id, "message": message}
            attempts.append(entry)
            local["messages"].append({"role": "user", "content": message})
            updates.update(attempt_records=attempts, next_task_number=number + 1,
                           objective_report=_runtime_report(message))
            updates["working_memory"] = _working_memory({**state, **updates})
            return updates
        task = TaskSpec(id=task_id, title=state["active_handoff"]["next_step"],
                        objective=state["active_handoff"]["next_step"], rationale=result.reasoning,
                        tool_hint=result.tool_name, action_hint=result.action,
                        target_mode=result.target_mode, target=result.target, args=result.args)
        updates.update(current_request=result, current_task=task, next_task_number=number + 1,
                       tasks=state["tasks"] + [task], attempt_records=attempts + [entry],
                       prepared_requests={**state["prepared_requests"], task_id: result.model_dump()})
        return updates

    def _implementor_route(state: SessionState) -> str:
        return "review_action" if state.get("current_request") else "reflect_objective"

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
            remaining = state["objective_timeout_seconds"] - (time.monotonic() - state["objective_started_at"])
            result = run_manager.execute(current_request, timeout_seconds=max(0, remaining))
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
        return_dict: Dict[str, object] = {"current_result": result, "current_request": current_request, "runs": runs, "notes": notes}
        # Propagate refreshed cookie into state so subsequent tasks use it
        if operator_context.get("auth_cookie") != cookie:
            return_dict["operator_context"] = operator_context
        return return_dict

    def _analyze_node(state: SessionState) -> Dict[str, Any]:
        run = state["runs"][-1]
        notes = list(state.get("notes", []))
        try:
            new_findings = analyzer.analyze(run.request, run.result)
        except Exception as exc:
            new_findings = []
            notes.append(f"Result analysis unavailable for {run.task.id}: {exc}")
        local = deepcopy(state["implementor_state"])
        implementor.record_result(local, run.model_dump(), [item.model_dump() for item in new_findings])
        failures = state.get("consecutive_failures", 0) + 1 if not run.result.success else 0
        reason = ""
        if run.status == "denied":
            reason = "Action denied; return to the head."
        elif _detect_login_redirect(run.result) or required_outcome(run.model_dump()) == "authentication_failure":
            reason = "Authentication state requires head review."
        fingerprints = list(state.get("result_fingerprints", []))
        fingerprint = json.dumps({"tool": run.request.tool_name, "stdout": run.result.stdout,
                                  "stderr": run.result.stderr, "raw_output": run.result.raw_output,
                                  "error": run.result.error}, sort_keys=True)
        unchanged = state.get("consecutive_unchanged_results", 0) + 1 if fingerprints and fingerprints[-1] == fingerprint else 0
        updates = {"result_fingerprints": fingerprints + [fingerprint], "consecutive_unchanged_results": unchanged,
                   "findings": state["findings"] + new_findings, "implementor_state": local,
                   "consecutive_failures": failures, "return_reason": reason, "notes": notes}
        # The factual ledger is refreshed after EVERY actual action, before any further decision.
        updates["working_memory"] = _working_memory({**state, **updates})
        return updates

    def _reflect_node(state: SessionState) -> Dict[str, Any]:
        actual = {run.task.id: run.model_dump() for run in state["runs"][state["objective_start_run"]:]}
        attempts = []
        for entry in state["attempt_records"][state["objective_start_attempt"]:]:
            item = {**entry, **actual.get(entry["task_id"], {}), "attempt_id": entry["task_id"]}
            item["required_outcome"] = required_outcome(item)
            attempts.append(item)
        iterations = deepcopy(state["head_iterations"])
        steps: List[Dict[str, Any]] = []
        reflection = feedback.reflect_objective(
            user_goal=state["user_goal"], attempts=attempts,
            objective={"handoff": state["active_handoff"], "implementor_report": state["objective_report"],
                       "action_trace": [{key: value for key, value in step.items() if key != "retrieval_observation"}
                                        for step in iterations[-1]["implementor_steps"]]},
            findings=state["findings"], prior_reflections=state["attempt_reflections"],
            open_questions=state["working_memory"]["open_questions"], trace_sink=steps)
        iterations[-1].update(objective_report=state["objective_report"], feedback_steps=steps,
                              feedback=reflection.model_dump())
        updates = {"head_iterations": iterations,
                   "attempt_reflections": state["attempt_reflections"] + [item.model_dump() for item in reflection.lessons],
                   "objective_feedback": state["objective_feedback"] + [{
                       "objective_id": state["objective_id"], **reflection.model_dump(),
                       "implementor_report": state["objective_report"]}]}
        print(f"[Feedback] {state['objective_id']}: {reflection.summary}", flush=True)
        updates["working_memory"] = _working_memory({**state, **updates})
        return updates

    def _next_node(state: SessionState) -> Dict[str, Any]:
        reason = ""
        if len(state["runs"]) >= state["max_tasks"]:
            reason = "Reached total action limit."
        elif state["replan_count"] >= state["max_replans"]:
            reason = "Reached max re-plan limit."
        if reason:
            return {"finished": True, "stop_reason": reason}
        return {"replan_count": state["replan_count"] + 1, "finished": False}

    def _report_node(state: SessionState) -> Dict[str, Any]:
        steps: List[Dict[str, Any]] = []
        reflection = feedback.consolidate(user_goal=state["user_goal"], reflections=state["attempt_reflections"],
                                         stop_reason=state.get("stop_reason", ""), objective_feedback=state["objective_feedback"], trace_sink=steps)
        path = reporter.write_markdown_report(
            output_dir=Path(state["output_dir"]), session_id=state["session_id"], user_goal=state["user_goal"],
            target=state["target"], operator_context=state.get("operator_context", {}), notes=state.get("notes", []),
            runs=state["runs"], findings=state["findings"], planning_trace=state["planning_trace"],
            stop_reason=state.get("stop_reason", ""), session_reflection=reflection.model_dump(), objective_feedback=state["objective_feedback"])
        return {"report_path": str(path), "session_reflection": reflection.model_dump(),
                "session_feedback_steps": steps, "finished": True, "working_memory": _working_memory(state)}

    for name, node in (("initialize_session", _initialize_node), ("plan_objective", _plan_node), ("implementor_step", _implementor_node),
                       ("review_action", _review_node), ("execute_request", _execute_node),
                       ("analyze_result", _analyze_node), ("reflect_objective", _reflect_node),
                       ("next_objective", _next_node), ("generate_report", _report_node)):
        graph.add_node(name, node)
    graph.add_edge(START, "initialize_session")
    graph.add_edge("initialize_session", "plan_objective")
    routes = {name: name for name in ("generate_report", "implementor_step")}
    graph.add_conditional_edges("plan_objective", _plan_route, routes)
    graph.add_conditional_edges("implementor_step", _implementor_route,
                                {name: name for name in ("review_action", "reflect_objective")})
    graph.add_edge("review_action", "execute_request")
    graph.add_edge("execute_request", "analyze_result")
    graph.add_edge("analyze_result", "implementor_step")
    graph.add_edge("reflect_objective", "next_objective")
    graph.add_conditional_edges("next_objective", lambda state: "generate_report" if state.get("finished") else "plan_objective",
                                {name: name for name in ("generate_report", "plan_objective")})
    graph.add_edge("generate_report", END)
    return graph
