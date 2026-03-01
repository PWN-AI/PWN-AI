from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Tuple

import yaml

try:
    from langchain_ollama import ChatOllama  # type: ignore
except Exception:
    # Fallback for environments where langchain_ollama and langchain_core versions mismatch.
    from langchain_community.chat_models import ChatOllama  # type: ignore


def _load_yaml(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_model_config(config_path: Path) -> Dict[str, Any]:
    cfg = _load_yaml(config_path)
    if "models" not in cfg:
        raise ValueError(f"Missing 'models' section in {config_path}")
    return cfg


class StreamingChatOllama:
    """Wrapper around ChatOllama that streams output to terminal in real-time."""

    def __init__(self, chat_ollama: ChatOllama, label: str = "LLM"):
        self._llm = chat_ollama
        self._label = label

    def invoke(self, messages: List[Any], **kwargs: Any) -> Any:
        """Invoke with streaming — prints tokens live then returns full response."""
        print(f"\n{'='*60}")
        print(f"[{self._label}] Thinking...")
        print(f"{'='*60}")

        full_text = ""
        try:
            for chunk in self._llm.stream(messages, **kwargs):
                token = ""
                if hasattr(chunk, "content"):
                    token = chunk.content or ""
                elif isinstance(chunk, str):
                    token = chunk
                if token:
                    full_text += token
                    sys.stdout.write(token)
                    sys.stdout.flush()
        except Exception as e:
            print(f"\n[{self._label}] Stream error: {e}")
            # Fallback to non-streaming invoke
            response = self._llm.invoke(messages, **kwargs)
            full_text = getattr(response, "content", str(response))
            print(full_text)
            print(f"{'='*60}")
            return response

        print(f"\n{'='*60}")

        # Return a response-like object with .content
        class _Response:
            def __init__(self, content: str):
                self.content = content
        return _Response(full_text)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)


def build_ollama_clients(config_path: Path) -> Tuple[ChatOllama, ChatOllama, Dict[str, Any]]:
    cfg = load_model_config(config_path)

    base_url = cfg.get("ollama", {}).get("base_url", "http://localhost:11434")
    head_cfg = cfg["models"]["head"]
    impl_cfg = cfg["models"]["implementor"]

    head_kwargs: Dict[str, Any] = {
        "base_url": base_url,
        "model": head_cfg["model"],
        "temperature": float(head_cfg.get("temperature", 0.2)),
    }
    if "reasoning" in head_cfg:
        head_kwargs["reasoning"] = bool(head_cfg["reasoning"])

    impl_kwargs: Dict[str, Any] = {
        "base_url": base_url,
        "model": impl_cfg["model"],
        "temperature": float(impl_cfg.get("temperature", 0.1)),
    }
    if "reasoning" in impl_cfg:
        impl_kwargs["reasoning"] = bool(impl_cfg["reasoning"])

    head = ChatOllama(**head_kwargs)
    implementor = ChatOllama(**impl_kwargs)

    # Wrap head LLM with streaming for live thinking display
    streaming_head = StreamingChatOllama(head, label="Head LLM")

    return streaming_head, implementor, cfg
