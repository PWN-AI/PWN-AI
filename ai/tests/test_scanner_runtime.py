import io
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from ai.contracts.schemas import TargetSpec, ToolRequest
from ai.integrations.command_injection_adapter import CommandInjectionAdapter
from ai.integrations.nuclei_adapter import NucleiXssAdapter
from nuclei.nuclei import default_nuclei_guesses
from nuclei import nuclei as nuclei_module


def test_commix_uses_current_python_ignores_stdin_and_enforces_timeout():
    root = Path(__file__).resolve().parents[2]
    adapter = CommandInjectionAdapter(root)
    request = ToolRequest(
        task_id="timeout", tool_name="command_injection", action="scan", target_mode="url",
        target=TargetSpec(url="http://127.0.0.1"),
        args={"max_runtime_seconds": 1},
    )

    class Process:
        stdout = io.StringIO("Scanner started\n")
        stderr = io.StringIO("")
        returncode = None
        killed = False

        def wait(self, timeout=None):
            if timeout is not None:
                assert timeout == 1
                raise subprocess.TimeoutExpired("commix", timeout)
            self.returncode = -9
            return self.returncode

        def kill(self):
            self.killed = True

    proc = Process()
    with patch.object(subprocess, "Popen", return_value=proc) as call:
        result = adapter.execute(request)
    command = call.call_args.args[0]
    assert command[0] == sys.executable
    assert "--ignore-stdin" in command
    assert proc.killed
    assert not result.success
    assert result.error == "Scan timed out after 1s."
    assert result.stdout == "Scanner started\n"


def test_nuclei_guesses_match_the_operating_system():
    binary = Path(default_nuclei_guesses()[0]).name
    assert binary == ("nuclei.exe" if os.name == "nt" else "nuclei")


def test_nuclei_adapter_passes_explicit_templates(tmp_path):
    root = Path(__file__).resolve().parents[2]
    request = ToolRequest(
        task_id="xss", tool_name="nuclei_xss", action="scan", target_mode="url",
        target=TargetSpec(url="http://127.0.0.1:8088"),
        args={"nuclei_templates": str(tmp_path), "out": str(tmp_path / "result.json")},
    )
    with patch.object(subprocess, "run", return_value=SimpleNamespace(
        returncode=0, stdout="", stderr="")) as call:
        result = NucleiXssAdapter(root).execute(request)
    assert result.success
    command = call.call_args.args[0]
    assert command[0] == sys.executable
    assert command[command.index("--nuclei-templates") + 1] == str(tmp_path)
    assert command[command.index("--allow-host") + 1] == "127.0.0.1"


def test_empty_project_templates_do_not_hide_installed_tools_library(tmp_path):
    (tmp_path / "nuclei-templates").mkdir()
    populated = tmp_path / "tools/nuclei-templates"
    populated.mkdir(parents=True)
    (populated / "test.yaml").write_text("id: test", encoding="utf-8")
    with patch.object(nuclei_module, "__file__", str(tmp_path / "nuclei/nuclei.py")):
        assert nuclei_module.detect_templates_root(None) == str(populated)
