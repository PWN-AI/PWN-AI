"""Exercise the real process boundary without invoking a security tool or target."""
import multiprocessing

from ai.contracts.schemas import ToolRequest
from ai.execution.run_manager import RunManager


def request():
    return ToolRequest(task_id="timeout-check", tool_name="sql_injection", action="scan",
                       target_mode="url", target={"url": "http://example.invalid"})


def test_expired_budget_does_not_start_worker(tmp_path):
    before = {process.pid for process in multiprocessing.active_children()}
    result = RunManager(tmp_path).execute(request(), timeout_seconds=0)
    assert not result.success and result.raw_output["timed_out"]
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_worker_timeout_is_bounded_and_reaped(tmp_path):
    before = {process.pid for process in multiprocessing.active_children()}
    result = RunManager(tmp_path).execute(request(), timeout_seconds=0.001)
    assert not result.success and result.raw_output["timed_out"]
    assert {process.pid for process in multiprocessing.active_children()} == before


def test_worker_returns_actual_adapter_error_without_running_a_tool(tmp_path):
    # No sql_injection/sql.py exists in this temporary project, so loading fails before any execution.
    result = RunManager(tmp_path).execute(request(), timeout_seconds=10)
    assert not result.success and "sql.py" in result.error
    assert result.tool_name == "sql_injection" and result.action == "scan"
    assert not result.raw_output.get("timed_out")


def _blocking_adapter(project_root, request_data, connection):
    import os
    from pathlib import Path
    import subprocess
    import sys
    import time
    os.setsid()
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    Path(project_root, "child.pid").write_text(str(child.pid))
    time.sleep(60)


def test_deadline_kills_a_running_worker_and_its_descendant(tmp_path, monkeypatch):
    import os
    import pytest
    import ai.execution.run_manager as module
    if os.name != "posix":
        pytest.skip("Process group assertion requires POSIX")
    monkeypatch.setattr(module, "_execute_adapter", _blocking_adapter)
    result = module.RunManager(tmp_path).execute(request(), timeout_seconds=3)
    assert result.raw_output["timed_out"] and result.duration_seconds < 5
    pid = int((tmp_path / "child.pid").read_text())
    status = tmp_path.__class__(f"/proc/{pid}/stat")
    # A killed orphan may briefly remain as a zombie until the host's init reaps it.
    assert not status.exists() or status.read_text().split()[2] == "Z"
