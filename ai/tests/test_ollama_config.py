from __future__ import annotations

import pytest

from ai.llm.ollama_client import resolve_model_connection, resolve_ollama_connection


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
