"""Build or rebuild the FAISS index from tool knowledge base.

Can be run as a script: python -m ai.rag.ingest
Or called programmatically via ensure_index_ready().
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Dict, Optional

from ai.rag.tool_knowledge import get_tool_documents
from ai.rag.vector_store import ToolVectorStore


DEFAULT_PERSIST_DIR = Path(__file__).resolve().parents[1] / "rag" / "index"


def build_index(
    persist_dir: Optional[Path] = None,
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    ollama_headers: Optional[Dict[str, str]] = None,
    force: bool = False,
) -> ToolVectorStore:
    """Build the FAISS index from tool knowledge documents.

    Args:
        persist_dir: Where to save the index files. Defaults to ai/rag/index/
        ollama_base_url: Ollama API endpoint
        embedding_model: Embedding model name
        ollama_headers: Optional HTTP headers (e.g. Authorization) for Ollama API
        force: If True, rebuild even if index exists and docs haven't changed

    Returns:
        Initialized ToolVectorStore ready for queries
    """
    persist_dir = persist_dir or DEFAULT_PERSIST_DIR
    store = ToolVectorStore(
        persist_dir=persist_dir,
        ollama_base_url=ollama_base_url,
        embedding_model=embedding_model,
        ollama_headers=ollama_headers,
    )

    documents = get_tool_documents()
    current_hash = ToolVectorStore.compute_docs_hash(documents)

    # Check if rebuild is needed
    hash_file = persist_dir / "docs_hash.txt"
    if not force and store.load():
        if hash_file.exists() and hash_file.read_text().strip() == current_hash:
            print(f"[RAG] Index up-to-date ({len(documents)} docs). Skipping rebuild.")
            return store
        print("[RAG] Tool docs changed. Rebuilding index...")

    store.build_index(documents)
    hash_file.write_text(current_hash, encoding="utf-8")
    return store


def ensure_index_ready(
    persist_dir: Optional[Path] = None,
    ollama_base_url: str = "http://localhost:11434",
    embedding_model: str = "nomic-embed-text",
    ollama_headers: Optional[Dict[str, str]] = None,
) -> ToolVectorStore:
    """Load existing index or build one if needed.

    This is the primary entry point for other modules — it guarantees
    a ready-to-use vector store without unnecessary rebuilds.
    """
    persist_dir = persist_dir or DEFAULT_PERSIST_DIR
    store = ToolVectorStore(
        persist_dir=persist_dir,
        ollama_base_url=ollama_base_url,
        embedding_model=embedding_model,
        ollama_headers=ollama_headers,
    )

    documents = get_tool_documents()
    current_hash = ToolVectorStore.compute_docs_hash(documents)
    hash_file = persist_dir / "docs_hash.txt"

    if store.load():
        if hash_file.exists() and hash_file.read_text().strip() == current_hash:
            return store
        print("[RAG] Tool docs changed since last index. Rebuilding...")

    store.build_index(documents)
    persist_dir.mkdir(parents=True, exist_ok=True)
    hash_file.write_text(current_hash, encoding="utf-8")
    return store


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Build/rebuild RAG index for PwnAI tools")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if up-to-date")
    parser.add_argument("--ollama-url", default="http://localhost:11434", help="Ollama base URL")
    parser.add_argument("--model", default="nomic-embed-text", help="Embedding model name")
    parser.add_argument("--api-key-env", default="OLLAMA_API_KEY", help="Environment variable holding API key")
    parser.add_argument("--api-key", help="API key (discouraged, prefer --api-key-env)")
    parser.add_argument("--auth-header", default="Authorization", help="Auth header name")
    parser.add_argument("--auth-scheme", default="Bearer", help="Auth scheme prefix")
    parser.add_argument("--test-query", help="Run a test query after building")
    args = parser.parse_args()

    api_key = (args.api_key or os.getenv(args.api_key_env, "")).strip()
    headers: Dict[str, str] = {}
    if api_key:
        token = f"{args.auth_scheme} {api_key}".strip() if args.auth_scheme else api_key
        headers[args.auth_header] = token

    store = build_index(
        ollama_base_url=args.ollama_url,
        embedding_model=args.model,
        ollama_headers=headers or None,
        force=args.force,
    )

    if args.test_query:
        from ai.rag.retriever import ToolRetriever
        retriever = ToolRetriever(store)
        print(f"\n[Test Query] '{args.test_query}'")
        print(retriever.retrieve(args.test_query))
