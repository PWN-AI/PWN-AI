from ai.contracts.schemas import TargetSpec, TaskSpec, ToolRequest


def test_task_spec_validation():
    task = TaskSpec.model_validate(
        {
            "id": "task-01",
            "title": "Test task",
            "objective": "Test objective",
            "priority": 1,
            "tool_hint": "sql_injection",
            "action_hint": "scan",
            "target_mode": "url",
            "target": {"url": "http://localhost"},
            "args": {},
        }
    )
    assert task.target.url == "http://localhost"


def test_tool_request_validation():
    req = ToolRequest.model_validate(
        {
            "task_id": "task-01",
            "tool_name": "nuclei_xss",
            "action": "scan",
            "target_mode": "url",
            "target": {"url": "http://localhost"},
            "args": {"tags": "xss"},
            "reasoning": "xss probe",
        }
    )
    assert req.tool_name == "nuclei_xss"


def test_target_spec_requires_one_target():
    target = TargetSpec.model_validate({"request_file": "sql_injection/req"})
    assert target.request_file == "sql_injection/req"

