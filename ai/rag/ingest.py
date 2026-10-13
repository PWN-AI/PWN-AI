"""Synchronize the versioned SQL database dump with PostgreSQL/pgvector."""
from __future__ import annotations

import argparse
import os
from pathlib import Path

import yaml

from ai.config.env_loader import load_project_env
from ai.rag.embeddings import CohereEmbedder
from ai.rag.vector_store import DEFAULT_SNAPSHOT, ToolVectorStore


def ensure_index_ready(
    *, database_url=None, snapshot_path=DEFAULT_SNAPSHOT, model="embed-v4.0",
    dimensions=1024, api_key_env="COHERE_API_KEY", force=False, prefer="auto",
) -> ToolVectorStore:
    store = ToolVectorStore(
        database_url=database_url, snapshot_path=Path(snapshot_path),
        embedder=CohereEmbedder(model=model, dimensions=dimensions, api_key_env=api_key_env),
    )
    changed = store.synchronize(force=force, prefer=prefer)
    print(f"[RAG] PostgreSQL ready ({store.document_count} documents); {changed} embedded.")
    return store


def store_from_config(cfg, project_root: Path, **overrides) -> ToolVectorStore:
    rag = cfg.get("rag", {})
    embedding = rag.get("embedding", {})
    if embedding.get("provider", "cohere") != "cohere":
        raise ValueError("RAG embeddings require provider: cohere")
    settings = {
        "database_url": os.getenv(rag.get("database_url_env", "RAG_DATABASE_URL")),
        "snapshot_path": project_root / rag.get("snapshot_path", "ai/rag/database/knowledge.sql"),
        "model": embedding.get("model", "embed-v4.0"),
        "dimensions": embedding.get("dimensions", 1024),
        "api_key_env": embedding.get("api_key_env", "COHERE_API_KEY"),
    }
    settings.update(overrides)
    return ensure_index_ready(**settings)


def main():
    root = Path(__file__).resolve().parents[2]
    load_project_env(root)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", default=str(root / "ai/config/models.local.yaml"))
    parser.add_argument("--database-url")
    parser.add_argument("--snapshot", type=Path, help="Path to the versioned SQL database dump")
    parser.add_argument("--force", action="store_true", help="Re-embed every document")
    parser.add_argument("--prefer", choices=("auto", "snapshot", "database"), default="auto")
    parser.add_argument("--test-query")
    args = parser.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8"))
    overrides = {"force": args.force, "prefer": args.prefer}
    if args.database_url:
        overrides["database_url"] = args.database_url
    if args.snapshot:
        overrides["snapshot_path"] = args.snapshot
    store = store_from_config(cfg, root, **overrides)
    if args.test_query:
        from ai.rag.retriever import ToolRetriever
        print(ToolRetriever(store).retrieve(args.test_query))


if __name__ == "__main__":
    main()
