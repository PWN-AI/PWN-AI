"""Cohere document embeddings, used only by knowledge synchronization."""

from __future__ import annotations

import json
import math
import os
import time
import urllib.error
import urllib.request
from typing import Dict, List


class CohereEmbedder:
    def __init__(self, model: str = "embed-v4.0", dimensions: int = 1024,
                 api_key_env: str = "COHERE_API_KEY", timeout: int = 60):
        if dimensions != 1024:
            raise ValueError("The pgvector schema requires 1024-dimensional embeddings.")
        self.model = model
        self.dimensions = dimensions
        self.api_key_env = api_key_env
        self.timeout = timeout

    @property
    def profile(self) -> Dict[str, object]:
        return {"provider": "cohere", "model": self.model, "dimensions": self.dimensions}

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        if not texts:
            return []
        api_key = os.getenv(self.api_key_env, "").strip()
        if not api_key:
            raise ValueError(
                f"Knowledge changed and needs embeddings. Set {self.api_key_env} in .env "
                "and run python -m ai.rag.ingest. Unchanged knowledge needs no key."
            )
        vectors: List[List[float]] = []
        # Cohere allows up to 96 texts per request. Keep changed chunks in batches.
        for offset in range(0, len(texts), 96):
            batch = texts[offset:offset + 96]
            body = json.dumps({
                "model": self.model, "texts": batch,
                "input_type": "search_document", "embedding_types": ["float"],
                "output_dimension": self.dimensions, "truncate": "NONE",
            }).encode("utf-8")
            for attempt in range(3):
                request = urllib.request.Request(
                    "https://api.cohere.com/v2/embed", data=body,
                    headers={"Content-Type": "application/json",
                             "Authorization": f"Bearer {api_key}"},
                )
                try:
                    with urllib.request.urlopen(request, timeout=self.timeout) as response:
                        payload = json.loads(response.read().decode("utf-8"))
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code in {429, 500, 502, 503, 504} and attempt < 2:
                        retry = exc.headers.get("Retry-After", "")
                        delay = min(float(retry), 10) if retry.isdigit() else 2 ** attempt
                        time.sleep(delay)
                        continue
                    # Never include request headers or API keys in exceptions.
                    raise RuntimeError(f"Cohere embedding request failed (HTTP {exc.code}).") from None
            batch_vectors = payload.get("embeddings", {}).get("float", [])
            if len(batch_vectors) != len(batch):
                raise ValueError("Cohere returned an unexpected embedding count.")
            for vector in batch_vectors:
                validate_vector(vector, self.dimensions)
                vectors.append([float(value) for value in vector])
        return vectors


def validate_vector(vector: object, dimensions: int = 1024) -> None:
    if not isinstance(vector, list) or len(vector) != dimensions:
        raise ValueError(f"Expected an embedding with {dimensions} dimensions.")
    if any(isinstance(value, bool) or not isinstance(value, (int, float))
           or not math.isfinite(value) for value in vector):
        raise ValueError("Embeddings must contain finite numeric values.")
    if not any(value != 0 for value in vector):
        raise ValueError("Zero vectors cannot be used for cosine retrieval.")
