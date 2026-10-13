"""FAISS vector store for tool knowledge retrieval.

Wraps FAISS with Ollama embeddings to provide semantic search over
tool argument documentation. Persists the index to disk so it only
needs to be built once (or rebuilt when tool docs change).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    import faiss  # type: ignore
except ImportError:
    faiss = None


class ToolVectorStore:
    """Lightweight FAISS vector store with Ollama embeddings."""

    INDEX_FILE = "tool_knowledge.faiss"
    META_FILE = "tool_knowledge_meta.json"

    def __init__(
        self,
        persist_dir: str | Path,
        ollama_base_url: str = "http://localhost:11434",
        embedding_model: str = "nomic-embed-text",
        ollama_headers: Optional[Dict[str, str]] = None,
    ):
        if faiss is None:
            raise ImportError("faiss-cpu is required. Install with: pip install faiss-cpu")

        self.persist_dir = Path(persist_dir)
        self.persist_dir.mkdir(parents=True, exist_ok=True)
        self.ollama_base_url = ollama_base_url.rstrip("/")
        self.embedding_model = embedding_model
        self.ollama_headers = dict(ollama_headers or {})

        self._index: Optional[faiss.IndexFlatIP] = None
        self._documents: List[Dict[str, str]] = []
        self._embeddings_dim: int = 0

    # ── Embedding ──────────────────────────────────────────────

    def _embed_texts(self, texts: List[str]) -> np.ndarray:
        """Get embeddings from Ollama embedding endpoint."""
        import urllib.request

        embeddings: List[List[float]] = []
        for text in texts:
            payload = json.dumps({
                "model": self.embedding_model,
                "prompt": text,
            }).encode("utf-8")
            headers = {"Content-Type": "application/json"}
            headers.update(self.ollama_headers)
            req = urllib.request.Request(
                f"{self.ollama_base_url}/api/embeddings",
                data=payload,
                headers=headers,
            )
            with urllib.request.urlopen(req, timeout=60) as resp:
                result = json.loads(resp.read().decode("utf-8"))
                embeddings.append(result["embedding"])

        arr = np.array(embeddings, dtype=np.float32)
        # L2-normalize for cosine similarity via inner product
        norms = np.linalg.norm(arr, axis=1, keepdims=True)
        norms = np.where(norms == 0, 1, norms)
        return arr / norms

    # ── Index management ──────────────────────────────────────

    def build_index(self, documents: List[Dict[str, str]]) -> None:
        """Build FAISS index from tool knowledge documents."""
        texts = [
            f"[{doc['tool']}] [{doc['category']}]\n{doc['content']}"
            for doc in documents
        ]
        print(f"[RAG] Embedding {len(texts)} documents with {self.embedding_model}...")
        embeddings = self._embed_texts(texts)

        self._embeddings_dim = embeddings.shape[1]
        self._index = faiss.IndexFlatIP(self._embeddings_dim)
        self._index.add(embeddings)
        self._documents = documents

        self._save()
        print(f"[RAG] Index built: {len(documents)} docs, {self._embeddings_dim}d vectors.")

    def _save(self) -> None:
        """Persist index and metadata to disk."""
        if self._index is None:
            return
        index_path = self.persist_dir / self.INDEX_FILE
        meta_path = self.persist_dir / self.META_FILE

        faiss.write_index(self._index, str(index_path))
        meta = {
            "documents": self._documents,
            "dim": self._embeddings_dim,
            "embedding_model": self.embedding_model,
        }
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

    def load(self) -> bool:
        """Load persisted index from disk. Returns True if successful."""
        index_path = self.persist_dir / self.INDEX_FILE
        meta_path = self.persist_dir / self.META_FILE

        if not index_path.exists() or not meta_path.exists():
            return False

        try:
            self._index = faiss.read_index(str(index_path))
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            self._documents = meta["documents"]
            self._embeddings_dim = meta["dim"]
            return True
        except Exception as exc:
            print(f"[RAG] Failed to load index: {exc}")
            return False

    @property
    def is_ready(self) -> bool:
        return self._index is not None and len(self._documents) > 0

    # ── Search ────────────────────────────────────────────────

    def search(
        self,
        query: str,
        top_k: int = 4,
        tool_filter: Optional[str] = None,
    ) -> List[Tuple[Dict[str, str], float]]:
        """Search for documents similar to the query.

        Returns list of (document, score) tuples sorted by relevance.
        """
        if not self.is_ready:
            return []

        query_vec = self._embed_texts([query])
        # Search more if filtering by tool to ensure enough results
        search_k = top_k * 3 if tool_filter else top_k
        scores, indices = self._index.search(query_vec, min(search_k, len(self._documents)))

        results: List[Tuple[Dict[str, str], float]] = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._documents):
                continue
            doc = self._documents[idx]
            if tool_filter and doc["tool"] != tool_filter:
                continue
            results.append((doc, float(score)))
            if len(results) >= top_k:
                break

        return results

    @staticmethod
    def compute_docs_hash(documents: List[Dict[str, str]]) -> str:
        """Compute hash of documents to detect changes."""
        content = json.dumps(documents, sort_keys=True)
        return hashlib.sha256(content.encode()).hexdigest()[:16]
