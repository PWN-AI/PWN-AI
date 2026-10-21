from ai.analysis.result_analyzer import ResultAnalyzer
from ai.contracts.schemas import ExecutionResult, TargetSpec, ToolRequest


def test_commix_banner_is_not_an_exploit():
    request = ToolRequest(task_id="test", tool_name="command_injection", action="scan",
                          target_mode="url", target=TargetSpec(url="http://127.0.0.1"))
    result = ExecutionResult(tool_name="command_injection", action="scan", success=True,
                             returncode=0, stdout="Automated OS Command Injection Tool\n"
                             "Using stdin for parsing targets list.")
    assert ResultAnalyzer().analyze(request, result) == []


def test_confirmed_command_injection_is_reported():
    request = ToolRequest(task_id="test", tool_name="command_injection", action="scan",
                          target_mode="url", target=TargetSpec(url="http://127.0.0.1"))
    result = ExecutionResult(tool_name="command_injection", action="scan", success=True,
                             returncode=0, stdout="The target is vulnerable")
    assert len(ResultAnalyzer().analyze(request, result)) == 1
