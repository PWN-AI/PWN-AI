"""The CLI/API-compatible flags reach the new runtime budgets."""
from pathlib import Path


from ai.main import _build_parser, _build_run_args
from ai.orchestration.session_controller import _build_parser as session_parser


def test_new_flag_and_legacy_alias_map_to_the_objective_action_budget():
    for parser in (_build_parser(), session_parser()):
        for flag in ("--max-actions-per-objective", "--max-new-tasks-per-replan"):
            args = parser.parse_args(["--goal", "Check", "--target-url", "http://example.invalid", flag, "4"])
            assert args.max_actions_per_objective == 4
    base = _build_parser().parse_args([])
    assert _build_run_args(base, {"goal": "Check", "max_actions_per_objective": 2}).max_actions_per_objective == 2
    assert _build_run_args(base, {"goal": "Check", "max_new_tasks_per_replan": 5}).max_actions_per_objective == 5


def test_full_controller_executes_two_observed_actions_and_writes_the_report(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import ai.orchestration.session_controller as controller
    from ai.tests.test_planner_adaptive import (
        Approver, HeadLLM, HeadProtocolFixture, FeedbackLLM, LLM, RETRIEVE, Retriever, Runner, final, observation, returned,
    )
    head = HeadLLM(observation())
    impl = LLM(RETRIEVE, final(), final("task-02", "other"), returned(ids=("task-01", "task-02")))
    cfg = {"models": {role: {"model": "fixture", "provider": "local"} for role in ("head", "implementor", "embedding")},
           "runtime": {"max_tasks": 4, "max_replans": 0, "objective_timeout_seconds": 30},
           "rag": {"enabled": True}}
    runner = Runner()
    monkeypatch.setattr(controller, "build_feedback_client", lambda cfg: FeedbackLLM())
    monkeypatch.setattr(controller, "build_ollama_clients", lambda path: (HeadProtocolFixture(head), impl, cfg))
    monkeypatch.setattr(controller, "ensure_index_ready", lambda **kwargs: SimpleNamespace(_index=SimpleNamespace(ntotal=1)))
    monkeypatch.setattr(controller, "ToolRetriever", lambda store: Retriever())
    monkeypatch.setattr(controller, "RunManager", lambda **kwargs: runner)
    monkeypatch.setattr(controller, "ActionFilter", lambda **kwargs: Approver())
    args = _build_run_args(_build_parser().parse_args([]), {
        "goal": "Validate observed input", "target_url": "http://example.com/page?id=1",
        "max_actions_per_objective": 2, "output_dir": str(tmp_path)})
    result = controller.run_session(args)
    assert len(runner.requests) == 2
    assert result["max_actions_per_objective"] == 2
    report = Path(result["report_path"]).read_text()
    assert "task-01: negative" in report and "task-02: negative" in report
    assert "execution evidence" in report and "Reached max re-plan limit" in report
    assert "## Objective Feedback" in report and "Implementor assessment: completed" in report


def test_final_state_serialization_preserves_nested_history_as_json_records():
    import json
    from ai.main import _serialize_state
    from ai.tests.test_planner_adaptive import Runner, final
    state = {"head_iterations": [{"implementor_steps": [{"action": "execute_tool", "call": 1}]}],
             "objective_feedback": [{"lessons": [{"progress": False}]}]}
    encoded = json.loads(json.dumps(_serialize_state(state)))
    assert encoded == state
    assert isinstance(encoded["head_iterations"][0], dict)
