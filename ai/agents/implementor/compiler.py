from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from ai.agents.head.planner import ObservationHandoff
from ai.agents.react_agent import run_react_loop
from ai.contracts.schemas import ToolRequest
from ai.llm.json_utils import parse_json_object
from ai.rag.retriever import ToolRetriever


class ImplementorCompiler:
    """Stateless observation/action compiler; each call has a fresh retrieval loop."""

    def __init__(self, llm: Any, prompt_path: Path, retriever: Optional[ToolRetriever] = None,
                 max_iterations: int = 3):
        self.llm = llm
        self.system_prompt = prompt_path.read_text(encoding="utf-8")
        self.retriever = retriever
        self.max_iterations = max_iterations

    def compile(self, *, handoff: ObservationHandoff, user_goal: str, task_id: str,
                trace_sink: Optional[List[Dict[str, Any]]] = None) -> ToolRequest:
        if self.retriever is None:
            raise RuntimeError("Implementor requires RAG retrieval; no executable fallback is allowed.")
        result = run_react_loop(
            llm=self.llm, system_prompt=self.system_prompt,
            user_message=json.dumps({"user_goal": user_goal, "task_id": task_id,
                                     "head_handoff": handoff.model_dump()}, ensure_ascii=False),
            retriever=self.retriever, label="Implementor", max_iterations=self.max_iterations,
            trace_sink=trace_sink,
        )
        parsed = parse_json_object(result.final_output)
        # Repair a scalar target's shape using the explicit mode, preserving its value.
        target_mode = parsed.get("target_mode")
        if isinstance(parsed.get("target"), str) and target_mode in ("url", "request_file"):
            parsed["target"] = {target_mode: parsed["target"]}
        request = ToolRequest.model_validate(parsed)
        if request.task_id != task_id:
            raise ValueError("Implementor returned a different task_id.")
        print(f"[Implementor] Tool request: {request.model_dump_json()}", flush=True)
        return request
