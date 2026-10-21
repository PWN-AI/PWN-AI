from __future__ import annotations

from types import SimpleNamespace

import pytest
import yaml

from ai.llm.ollama_client import resolve_model_connection, resolve_ollama_connection
from ai.llm import ollama_client


@pytest.mark.parametrize("reasoning", [False, True, None, "low", "medium", "high"])
def test_reasoning_values_are_passed_without_boolean_coercion(tmp_path, monkeypatch, reasoning):
    cfg = {
        "ollama": {"base_url": "http://localhost:11434"},
        "models": {
            "head": {"model": "test", "reasoning": reasoning},
            "implementor": {"model": "test", "reasoning": reasoning},
        },
    }
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    monkeypatch.setattr(ollama_client, "ChatOllama", lambda **kwargs: SimpleNamespace(**kwargs))
    head, impl, _ = ollama_client.build_ollama_clients(path)
    assert head.reasoning == reasoning
    assert impl.reasoning == reasoning


def test_resolve_legacy_ollama_base_url() -> None:
    cfg = {
        "ollama": {
            "base_url": "http://localhost:11434",
        }
    }
    conn = resolve_ollama_connection(cfg)
    assert conn["provider"] == "local"
    assert conn["base_url"] == "http://localhost:11434"
    assert conn["headers"] == {}


def test_resolve_cloud_from_env_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    cfg = {
        "ollama": {
            "mode": "cloud",
            "providers": {
                "cloud": {
                    "base_url": "https://ollama.com",
                    "api_key_env": "OLLAMA_API_KEY",
                },
            },
        }
    }
    conn = resolve_ollama_connection(cfg)
    assert conn["provider"] == "cloud"
    assert conn["base_url"] == "https://ollama.com"
    assert conn["headers"]["Authorization"] == "Bearer test-key"


def test_resolve_cloud_missing_api_key_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OLLAMA_API_KEY", raising=False)
    cfg = {
        "ollama": {
            "mode": "cloud",
            "providers": {
                "cloud": {
                    "base_url": "https://ollama.com",
                    "api_key_env": "OLLAMA_API_KEY",
                },
            },
        }
    }
    with pytest.raises(ValueError):
        resolve_ollama_connection(cfg)


def test_resolve_model_connection_by_role_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    cfg = {
        "ollama": {
            "mode": "local",
            "providers": {
                "local": {"base_url": "http://localhost:11434"},
                "cloud": {
                    "base_url": "https://ollama.com",
                    "api_key_env": "OLLAMA_API_KEY",
                },
            },
        },
        "models": {
            "head": {"provider": "cloud", "model": "qwen3:8b"},
            "implementor": {"provider": "local", "model": "qwen2.5-coder:7b"},
            "embedding": {"provider": "local", "model": "nomic-embed-text"},
        },
    }

    head_conn = resolve_model_connection(cfg, "head")
    impl_conn = resolve_model_connection(cfg, "implementor")

    assert head_conn["provider"] == "cloud"
    assert head_conn["base_url"] == "https://ollama.com"
    assert "Authorization" in head_conn["headers"]

    assert impl_conn["provider"] == "local"
    assert impl_conn["base_url"] == "http://localhost:11434"
