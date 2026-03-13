from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

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


def _normalize_headers(raw_headers: Any) -> Dict[str, str]:
    if not isinstance(raw_headers, dict):
        return {}
    headers: Dict[str, str] = {}
    for k, v in raw_headers.items():
        key = str(k).strip()
        value = str(v).strip()
        if key and value:
            headers[key] = value
    return headers


def _get_provider_section(ollama_cfg: Dict[str, Any], provider: str) -> Dict[str, Any]:
    """Return config section for a provider, supporting old and new schemas."""
    providers = ollama_cfg.get("providers", {})
    if isinstance(providers, dict) and isinstance(providers.get(provider), dict):
        return providers.get(provider, {}) or {}
    section = ollama_cfg.get(provider, {})
    if isinstance(section, dict):
        return section
    return {}


def resolve_ollama_connection(cfg: Dict[str, Any], provider: Optional[str] = None) -> Dict[str, Any]:
    """Resolve Ollama connection config for local/cloud with auth headers.

    Supported schemas:
    - Legacy:
        ollama:
          base_url: "http://localhost:11434"
    - New:
        ollama:
          mode: local|cloud    # optional default provider
          providers:
            local:
              base_url: ...
              headers: {...}         # optional
            cloud:
              base_url: ...
              api_key_env: OLLAMA_API_KEY
              api_key: ...           # optional, discouraged
              auth_header: Authorization
              auth_scheme: Bearer
              headers: {...}         # optional extra headers

    Also supports top-level ollama.local / ollama.cloud for backward compatibility.
    """
    ollama_cfg = cfg.get("ollama", {}) or {}
    legacy_base_url = str(ollama_cfg.get("base_url", "")).strip()
    providers_cfg = ollama_cfg.get("providers", {})
    has_profiles = (
        isinstance(ollama_cfg.get("local"), dict)
        or isinstance(ollama_cfg.get("cloud"), dict)
        or isinstance(providers_cfg, dict)
    )

    if provider is None:
        provider = str(ollama_cfg.get("mode", "local")).strip().lower()
        if not provider:
            provider = "local"
    else:
        provider = str(provider).strip().lower()

    # Backward compatibility with existing config files that only defined base_url.
    if legacy_base_url and not has_profiles and provider == "local":
        return {
            "provider": "local",
            "base_url": legacy_base_url.rstrip("/"),
            "headers": _normalize_headers(ollama_cfg.get("headers", {})),
        }

    if provider == "local":
        local_cfg = _get_provider_section(ollama_cfg, "local")
        base_url = str(local_cfg.get("base_url", legacy_base_url or "http://localhost:11434")).strip()
        if not base_url:
            base_url = "http://localhost:11434"
        headers = _normalize_headers(local_cfg.get("headers", {}))
        return {
            "provider": "local",
            "base_url": base_url.rstrip("/"),
            "headers": headers,
        }

    if provider == "cloud":
        cloud_cfg = _get_provider_section(ollama_cfg, "cloud")
        base_url = str(cloud_cfg.get("base_url", "https://ollama.com")).strip().rstrip("/")
        api_key_env = str(cloud_cfg.get("api_key_env", "OLLAMA_API_KEY")).strip() or "OLLAMA_API_KEY"
        api_key = str(cloud_cfg.get("api_key", "")).strip()
        if not api_key:
            api_key = os.getenv(api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                "Ollama cloud provider selected but API key is missing. "
                f"Set env var '{api_key_env}' or configure ollama.providers.cloud.api_key."
            )

        auth_header = str(cloud_cfg.get("auth_header", "Authorization")).strip() or "Authorization"
        auth_scheme = str(cloud_cfg.get("auth_scheme", "Bearer")).strip()
        token_value = f"{auth_scheme} {api_key}".strip() if auth_scheme else api_key

        headers = _normalize_headers(cloud_cfg.get("headers", {}))
        headers[auth_header] = token_value

        return {
            "provider": "cloud",
            "base_url": base_url,
            "headers": headers,
            "api_key_env": api_key_env,
        }

    raise ValueError("Invalid Ollama provider. Expected 'local' or 'cloud'.")


def resolve_model_connection(cfg: Dict[str, Any], role: str) -> Dict[str, Any]:
    """Resolve provider connection for a model role (head/implementor/embedding)."""
    models_cfg = cfg.get("models", {}) or {}
    role_cfg = models_cfg.get(role, {}) or {}
    if not isinstance(role_cfg, dict):
        role_cfg = {}

    provider = str(role_cfg.get("provider", "")).strip().lower()
    conn = resolve_ollama_connection(cfg, provider=provider or None)
    conn["role"] = role
    return conn


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

    head_cfg = cfg["models"]["head"]
    impl_cfg = cfg["models"]["implementor"]
    head_conn = resolve_model_connection(cfg, "head")
    impl_conn = resolve_model_connection(cfg, "implementor")

    head_kwargs: Dict[str, Any] = {
        "base_url": head_conn["base_url"],
        "model": head_cfg["model"],
        "temperature": float(head_cfg.get("temperature", 0.2)),
        "num_ctx": int(head_cfg.get("num_ctx", 32768)),
    }
    if "reasoning" in head_cfg:
        head_kwargs["reasoning"] = bool(head_cfg["reasoning"])
    if head_conn.get("headers"):
        head_kwargs["client_kwargs"] = {"headers": dict(head_conn["headers"])}

    impl_kwargs: Dict[str, Any] = {
        "base_url": impl_conn["base_url"],
        "model": impl_cfg["model"],
        "temperature": float(impl_cfg.get("temperature", 0.1)),
        "num_ctx": int(impl_cfg.get("num_ctx", 32768)),
    }
    if "reasoning" in impl_cfg:
        impl_kwargs["reasoning"] = bool(impl_cfg["reasoning"])
    if impl_conn.get("headers"):
        impl_kwargs["client_kwargs"] = {"headers": dict(impl_conn["headers"])}

    head = ChatOllama(**head_kwargs)
    implementor = ChatOllama(**impl_kwargs)

    # Wrap head LLM with streaming for live thinking display
    streaming_head = StreamingChatOllama(head, label="Head LLM")

    return streaming_head, implementor, cfg
