from __future__ import annotations

import multiprocessing
import os
import signal
import sys
from pathlib import Path
from time import monotonic
from typing import Any

from ai.contracts.schemas import ExecutionResult, ToolRequest
from ai.integrations.tool_registry import ToolRegistry


def _execute_adapter(project_root: str, request_data: dict[str, Any], connection: Any) -> None:
    """Isolate an adapter and its subprocesses so the runtime can enforce a deadline."""
    if os.name == "posix":
        os.setsid()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(line_buffering=True)
    try:
        request = ToolRequest.model_validate(request_data)
        result = ToolRegistry(Path(project_root)).get(request.tool_name).execute(request)
        connection.send(result.model_dump())
    except Exception as exc:
        connection.send({"worker_error": str(exc)})
    finally:
        connection.close()


def _stop_worker(process: Any) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            # A very short deadline may expire before the child creates its process group.
            if process.is_alive():
                process.kill()
    elif process.is_alive():
        process.kill()
    process.join(timeout=5)


class RunManager:
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.registry = ToolRegistry(project_root)

    def execute(self, request: ToolRequest, *, timeout_seconds: float | None = None) -> ExecutionResult:
        if timeout_seconds is None:
            return self.registry.get(request.tool_name).execute(request)
        started = monotonic()
        context = multiprocessing.get_context("spawn")
        receiver, sender = context.Pipe(duplex=False)
        process = context.Process(target=_execute_adapter,
                                  args=(str(self.project_root), request.model_dump(), sender))
        try:
            if timeout_seconds <= 0:
                raise TimeoutError("No execution time remains.")
            process.start()
            sender.close()
            remaining = max(0, timeout_seconds - (monotonic() - started))
            if not receiver.poll(remaining):
                raise TimeoutError(f"Tool timed out after {timeout_seconds:.1f}s runtime budget.")
            data = receiver.recv()
            if "worker_error" in data:
                raise RuntimeError(data["worker_error"])
            return ExecutionResult.model_validate(data)
        except Exception as exc:
            return ExecutionResult(tool_name=request.tool_name, action=request.action, success=False,
                                   returncode=1, error=str(exc), stderr=str(exc),
                                   raw_output={"timed_out": isinstance(exc, TimeoutError)},
                                   duration_seconds=monotonic() - started)
        finally:
            if process.pid is not None:
                # Also clean up descendants left behind by an adapter after returning.
                _stop_worker(process)
                process.close()
            receiver.close()
            sender.close()
