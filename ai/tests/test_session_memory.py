"""Session memory must preserve evidence without replaying the full archive."""
from copy import deepcopy
import json

from ai.agents.head.memory import EXCERPT_CHARS, MAX_OLDER_ATTEMPTS, build_working_memory
from ai.contracts.schemas import ExecutionResult, TargetSpec
from ai.tests.test_head_search import proposal, task
from ai.tests.test_planner_adaptive import LLM, URL, planner, reviewer


def run(index, stdout="raw result", **result_changes):
    return {
        "task": {**task().model_dump(), "id": f"task-{index:02d}", "objective": f"Inspect scope {index}"},
        "request": {"task_id": f"task-{index:02d}", "tool_name": "nuclei_xss", "action": "scan",
                    "target": {"url": URL}, "args": {"parameter": "Exact_Case", "headers": ["X-Test: A%2FB"]}},
        "status": "executed",
        "result": {"stdout": stdout, "stderr": "", "success": True, "returncode": 0, **result_changes},
    }


def feedback(index):
    return {"attempt_id": f"task-{index:02d}", "outcome": "negative",
            "evidence_refs": [f"attempt:task-{index:02d}"], "lesson": "Completed tested scope; other inputs remain untested.",
            "next_adjustment": "Inspect the remaining observed inputs.", "retry_condition": "New evidence required.",
            "progress": True, "source": "model"}


def memory(**kwargs):
    return build_working_memory(session_id="only-this-session", user_goal="Inspect input handling", target={"url": URL},
                                operator_context={}, **kwargs)


def test_all_working_memory_sections_exist_and_new_session_has_no_attempts():
    result = memory()
    assert result["goal_and_constraints"]["goal"] == "Inspect input handling"
    assert result["attempt_ledger"] == result["feedback"] == []
    assert result["open_questions"] == result["relevant_older_evidence"] == []
    assert result["latest_result"] is None
    assert result["verified_facts"]["target"]["value"] == {"url": URL}
    assert result["history_index"]["raw_history_available_in_session"]


def test_exact_urls_parameters_authentication_request_files_and_constraints_are_preserved():
    url = "https://test.invalid/Case/Page?Token=a%2Fb&Empty=&Repeat=1&Repeat=2&Name%20X=%E2%9C%93"
    context = {"auth_verified": False, "auth_cookie": "Session=A%2FB; Case=Exact", "password": "DO_NOT_INCLUDE",
               "discovered_endpoints": [url], "constraints": ["Only the supplied target"],
               "authenticated_page_html": "<form>Exact_Name</form>"}
    result = build_working_memory(session_id="one", user_goal="Validate inputs", target={"url": url},
                                  operator_context=context, constraints={"remaining_replans": 2})
    facts = result["verified_facts"]
    assert facts["observed_urls"][0]["url"] == url
    assert facts["observed_urls"][0]["raw_query"] == url.split("?", 1)[1]
    assert facts["observed_urls"][0]["parameters"] == [
        {"name": "Token", "value": "a/b"}, {"name": "Empty", "value": ""},
        {"name": "Repeat", "value": "1"}, {"name": "Repeat", "value": "2"}, {"name": "Name X", "value": "✓"}]
    assert facts["application_context"]["value"]["auth_verified"] is False
    assert facts["application_context"]["value"]["auth_cookie"] == context["auth_cookie"]
    assert facts["application_context"]["value"]["page_sample"] == context["authenticated_page_html"]
    assert "DO_NOT_INCLUDE" not in json.dumps(result)
    assert result["goal_and_constraints"]["runtime_limits"] == {"remaining_replans": 2}
    assert result["goal_and_constraints"]["operator_constraints"] == context["constraints"]
    captured = build_working_memory(session_id="one", user_goal="Validate request", target={"request_file": "captures/Case Sensitive.req"},
                                    operator_context={})
    assert captured["verified_facts"]["target"]["value"] == {"request_file": "captures/Case Sensitive.req"}
    assert captured["verified_facts"]["observed_urls"] == []


def test_feedback_updates_ledger_outcomes_without_turning_attempt_arguments_into_verified_facts():
    records = [run(1, "No finding in tested scope"), run(2, "", success=False, error="Unsupported option")]
    lessons = [feedback(1), {**feedback(2), "outcome": "execution_failure", "progress": False}]
    result = memory(runs=records, reflections=lessons)
    assert [row["outcome"] for row in result["attempt_ledger"]] == ["negative", "execution_failure"]
    assert [row["progress"] for row in result["attempt_ledger"]] == [True, False]
    assert all(row["resolved_question"] == "" for row in result["attempt_ledger"])
    assert result["attempt_ledger"][0]["request"]["args"] == records[0]["request"]["args"]
    assert "Exact_Case" not in json.dumps(result["verified_facts"])
    assert result["feedback"] == lessons
    assert result["latest_result"]["result"] == records[-1]["result"]


def test_large_archive_is_retained_but_only_latest_output_and_bounded_older_windows_are_sent():
    records = [run(index, f"Archive marker {index}\n" + "Z" * 50000) for index in range(1, 13)]
    untouched = deepcopy(records)
    candidate = {"candidate_id": "branch-12-01", "status": "viable", "candidate": {
        **proposal(), "evidence_refs": ["attempt:task-01", "attempt:task-03"]}}
    iterations = [{"task_id": "task-01", "memory_notes": ["Brief current-goal note"],
                   "implementor_steps": [{"retrieval_observation": "RAW_RAG_ARCHIVE_MUST_STAY_OUT"}],
                   "observations": ["OLD_FULL_OBSERVATION_MUST_STAY_OUT"], "search_trace": []}]
    result = memory(runs=records, reflections=[feedback(index) for index in range(1, 13)],
                    focus=[candidate["candidate"]], iterations=iterations)
    assert len(result["attempt_ledger"]) == 12
    assert len(result["relevant_older_evidence"]) <= MAX_OLDER_ATTEMPTS
    assert result["latest_result"]["result"]["stdout"] == records[-1]["result"]["stdout"]
    assert result["relevant_older_evidence"][0]["evidence_ref"] in {"attempt:task-01", "attempt:task-03"}
    assert len(json.dumps(result)) < len(json.dumps(records)) / 4
    assert "RAW_RAG_ARCHIVE_MUST_STAY_OUT" not in json.dumps(result)
    assert "OLD_FULL_OBSERVATION_MUST_STAY_OUT" not in json.dumps(result)
    for item in result["relevant_older_evidence"]:
        index = int(item["evidence_ref"].split("-")[-1]) - 1
        for excerpt in item["excerpts"]:
            assert len(excerpt["text"]) <= EXCERPT_CHARS
            assert excerpt["text"] == records[index]["result"][excerpt["field"]][excerpt["start"]:excerpt["end"]]
    assert records == untouched
    result["latest_result"]["result"]["stdout"] = "Model-facing object changed"
    assert records == untouched


def test_scoped_evidence_selection_can_retrieve_an_exact_older_value_by_reference():
    from ai.agents.head.memory import select_older_evidence
    anchor = "Unique_Observed_Parameter_Identifier"
    records = [run(1, "x" * 60000 + "\n" + anchor + "=Case%2FValue\n" + "z" * 60000)]
    records.extend(run(index, "unrelated execution text") for index in range(2, 9))
    windows = select_older_evidence(records, goal="Inspect inputs", feedback=[],
                                    focus=[{"objective": f"Validate {anchor}", "evidence_refs": ["attempt:task-01"]}])
    selected = next(item for item in windows if item["evidence_ref"] == "attempt:task-01")
    assert anchor + "=Case%2FValue" in selected["excerpts"][0]["text"]
    assert selected["excerpts"][0]["start"] > 0 and selected["excerpts"][0]["truncated"]


def test_open_questions_remaining_objectives_and_compilation_failure_are_current_session_records():
    planned = {"phase": "plan_objective", **proposal(open_questions=["Is the observed input authenticated?"],
                                             remaining_objectives=["Resolve input handling"])}
    iteration = {"task_id": "task-01", "next_step": "Validate observed input", "compilation_error": "RAG unavailable",
                 "memory_notes": ["Input remains untested"], "search_trace": [planned]}
    result = memory(iterations=[iteration], reflections=[{**feedback(1), "outcome": "compilation_failure", "progress": False}])
    assert result["goal_and_constraints"]["remaining_objectives"] == ["Resolve input handling"]
    assert result["open_questions"] == ["Is the observed input authenticated?"]
    assert result["attempt_ledger"][0]["execution_status"] == "not_executed"
    assert not result["attempt_ledger"][0]["progress"]
    assert result["feedback"][0]["outcome"] == "compilation_failure"


def test_only_explicitly_resolved_questions_leave_working_memory():
    question = "Does the observed endpoint require authentication?"
    planned = {"phase": "plan_objective", **proposal(open_questions=[question, "What inputs remain untested?"])}
    record = {**feedback(1), "resolved_question": question}
    llm = LLM(json.dumps(record))
    validated = reviewer(llm).reflect(user_goal="Inspect input handling", attempt_id="task-01", attempt=run(1),
                                     findings=[], prior_reflections=[], open_questions=[question])
    result = memory(runs=[run(1)], reflections=[validated], iterations=[{"search_trace": [planned]}])
    assert result["open_questions"] == ["What inputs remain untested?"]
    assert result["attempt_ledger"][0]["resolved_question"] == question
    invalid = {**record, "resolved_question": "Invented question"}
    rejected = reviewer(LLM(json.dumps(invalid), json.dumps(invalid))).reflect(
        user_goal="Inspect input handling", attempt_id="task-01", attempt=run(1),
        findings=[], prior_reflections=[], open_questions=[question])
    assert rejected.source == "runtime" and rejected.resolved_question == ""


def test_omitted_memory_assessments_are_retained_until_explicitly_updated():
    initial = {"search_trace": [{"phase": "plan_objective", **proposal(open_questions=["Unresolved input question"],
                                                               remaining_objectives=["Verify the observed parameter"])}]}
    omitted = {"search_trace": [{"phase": "plan_objective", **proposal(open_questions=None, remaining_objectives=None)}]}
    result = memory(iterations=[initial, omitted])
    assert result["open_questions"] == ["Unresolved input question"]
    assert result["goal_and_constraints"]["remaining_objectives"] == ["Verify the observed parameter"]
    cleared = {"search_trace": [{"phase": "plan_objective", **proposal(open_questions=[], remaining_objectives=[])}]}
    result = memory(iterations=[initial, omitted, cleared])
    assert result["open_questions"] == result["goal_and_constraints"]["remaining_objectives"] == []


def test_head_memory_notes_survive_multiple_actions_in_the_same_objective():
    iterations = [{"task_id": "objective-01", "memory_notes": ["Keep this exact strategic constraint"]}]
    iterations += [{"task_id": f"task-{index:02d}", "tool_request": {}} for index in range(1, 5)]
    result = memory(iterations=iterations)
    assert result["planning_notes"] == [{"task_id": "objective-01", "notes": ["Keep this exact strategic constraint"]}]
