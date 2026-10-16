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


def test_feedback_inherits_head_provider_and_settings_but_can_override_them(monkeypatch):
    from ai.llm.ollama_client import resolve_model_config
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    cfg = {"ollama": {"mode": "local", "providers": {"cloud": {"base_url": "https://ollama.com"}}},
           "models": {"head": {"model": "head-fixture", "provider": "cloud", "num_ctx": 16384}}}
    assert resolve_model_config(cfg, "feedback") == cfg["models"]["head"]
    assert resolve_model_connection(cfg, "feedback")["provider"] == "cloud"
    cfg["models"]["feedback"] = {"model": "review-fixture", "provider": "local", "temperature": 0.1}
    assert resolve_model_connection(cfg, "feedback")["provider"] == "local"
    assert resolve_model_config(cfg, "feedback") == {
        "model": "review-fixture", "provider": "local", "num_ctx": 16384, "temperature": 0.1}


@pytest.mark.parametrize("separate_model", [False, True])
def test_feedback_client_is_independent_and_standalone_chat_builder_still_returns_three_values(tmp_path, monkeypatch, separate_model):
    import yaml
    from types import SimpleNamespace
    import ai.llm.ollama_client as module
    cfg = {"ollama": {"base_url": "http://localhost:11434"}, "models": {
        "head": {"model": "head-fixture", "num_ctx": 16384, "reasoning": False},
        "implementor": {"model": "executor-fixture"}}}
    if separate_model:
        cfg["models"]["feedback"] = {"model": "review-fixture", "temperature": 0.05}
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(cfg))
    clients = []
    def client(**kwargs):
        result = SimpleNamespace(**kwargs)
        clients.append(result)
        return result
    monkeypatch.setattr(module, "ChatOllama", client)
    head, implementor, loaded = module.build_ollama_clients(path)
    feedback = module.build_feedback_client(loaded)
    assert len(clients) == 3 and feedback._llm is not head._llm
    assert head._label == "Head LLM" and feedback._label == "Feedback LLM"
    assert feedback.model == ("review-fixture" if separate_model else "head-fixture")
    assert feedback.num_ctx == 16384 and feedback.reasoning is False
    assert head.model == "head-fixture" and implementor.model == "executor-fixture"
