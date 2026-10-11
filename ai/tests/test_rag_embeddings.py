import io
import json
import urllib.error
import urllib.request
from unittest.mock import patch

import pytest

from ai.rag.embeddings import CohereEmbedder, validate_vector


def test_key_is_lazy(monkeypatch):
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    provider = CohereEmbedder()
    assert provider.embed_documents([]) == []
    with pytest.raises(ValueError, match="Knowledge changed"):
        provider.embed_documents(["Document"])


def test_cohere_batches_and_payload(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-secret")
    sizes = []

    def response(request, timeout):
        payload = json.loads(request.data)
        assert request.full_url == "https://api.cohere.com/v2/embed"
        assert payload["input_type"] == "search_document"
        assert payload["output_dimension"] == 1024
        assert payload["embedding_types"] == ["float"]
        sizes.append(len(payload["texts"]))
        return io.BytesIO(json.dumps({"embeddings": {
            "float": [[1.0] * 1024 for _ in payload["texts"]]
        }}).encode())

    with patch.object(urllib.request, "urlopen", response):
        assert len(CohereEmbedder().embed_documents(["text"] * 97)) == 97
    assert sizes == [96, 1]


def test_api_failure_does_not_leak_key(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-secret")
    error = urllib.error.HTTPError("https://api.cohere.com/v2/embed", 401, "Unauthorized", {}, None)
    with patch.object(urllib.request, "urlopen", side_effect=error):
        with pytest.raises(RuntimeError, match="HTTP 401") as exc:
            CohereEmbedder().embed_documents(["text"])
    assert "test-secret" not in str(exc.value)


def test_rate_limit_retries(monkeypatch):
    monkeypatch.setenv("COHERE_API_KEY", "test-secret")
    error = urllib.error.HTTPError("", 429, "", {"Retry-After": "0"}, None)
    success = io.BytesIO(json.dumps({"embeddings": {"float": [[1.0] * 1024]}}).encode())
    with patch.object(urllib.request, "urlopen", side_effect=[error, success]) as call:
        assert len(CohereEmbedder().embed_documents(["text"])) == 1
        assert call.call_count == 2


@pytest.mark.parametrize("vector", [[0] * 1024, [1] * 3, [True] * 1024, [float("inf")] * 1024])
def test_bad_vectors(vector):
    with pytest.raises(ValueError):
        validate_vector(vector)
