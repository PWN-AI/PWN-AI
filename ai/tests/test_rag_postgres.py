"""Real PostgreSQL integration tests; synthetic vectors never enter the shipped snapshot."""
import copy
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlparse

import psycopg
import pytest

from ai.rag.embeddings import CohereEmbedder
from ai.rag.retriever import ToolRetriever
from ai.rag.vector_store import (
    DEFAULT_SNAPSHOT, SnapshotConflictError, ToolVectorStore, document_hash, read_snapshot, write_snapshot,
)


class FakeEmbedder:
    profile = {"provider": "cohere", "model": "embed-v4.0", "dimensions": 1024}

    def __init__(self):
        self.calls = []
        self.fail = False

    def embed_documents(self, texts):
        assert not self.fail, "Embedding must not be invoked"
        self.calls.append(texts)
        return [[(hashlib.sha256(text.encode()).digest()[i % 32] - 127) / 127
                 for i in range(1024)] for text in texts]


def test_versioned_snapshot_is_ready_to_restore_without_embeddings():
    payload = read_snapshot(DEFAULT_SNAPSHOT)
    assert payload["documents"]
    assert all(doc["embedding"] is not None and
               doc["embedded_content_hash"] == document_hash(doc)
               for doc in payload["documents"])


@pytest.fixture
def store(tmp_path):
    url = os.getenv("RAG_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Set RAG_TEST_DATABASE_URL to a dedicated database ending in _test")
    assert urlparse(url).path.endswith("_test"), "Refusing to reset a non-test database"
    with psycopg.connect(url) as conn:
        conn.execute("DROP SCHEMA IF EXISTS pwnai_rag CASCADE")
    snapshot = read_snapshot(DEFAULT_SNAPSHOT)
    # Test ingestion independently of whether the shipped database is already populated.
    for doc in snapshot["documents"]:
        doc["embedding"] = doc["embedded_content_hash"] = None
    path = tmp_path / "knowledge.json"
    write_snapshot(path, snapshot)
    return ToolVectorStore(url, path, FakeEmbedder())


def edit_snapshot(store, mutate):
    payload = read_snapshot(store.snapshot_path)
    mutate(payload)
    write_snapshot(store.snapshot_path, payload)


def test_initial_ingestion_and_zero_call_restart(store, monkeypatch):
    assert store.synchronize() == 23
    assert store.document_count == 23
    assert store.is_ready
    before = store.snapshot_path.read_bytes()
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    store.embedder = CohereEmbedder()
    assert store.synchronize() == 0
    assert store.snapshot_path.read_bytes() == before
    retriever = ToolRetriever(store)
    assert "sql_injection" in retriever.retrieve("sqlmap dump database")
    assert "Complete Documentation" in retriever.retrieve_all_for_tool("command_injection")
    assert store.search("unmatchedxyz", tool_filter="sql_injection")
    assert store.search("123") == []
    assert store.search("sqlmap", top_k=0) == []
    assert store.search("sqlmap", tool_filter="nonexistent") == []
    assert all(doc["tool"] == "sql_injection" for doc, _ in
               store.search("database", tool_filter="sql_injection"))


def test_restore_snapshot_without_key(store, monkeypatch):
    store.synchronize()
    before = store.snapshot_path.read_bytes()
    with psycopg.connect(store.database_url) as conn:
        conn.execute("DROP SCHEMA pwnai_rag CASCADE")
    monkeypatch.delenv("COHERE_API_KEY", raising=False)
    store.embedder = CohereEmbedder()
    assert store.synchronize() == 0
    assert store.snapshot_path.read_bytes() == before
    assert store.document_count == 23


def test_edit_add_delete_only_embeds_changes(store):
    store.synchronize()
    edit_snapshot(store, lambda p: p["documents"][0].update(content="Updated tool description"))
    assert store.synchronize() == 1
    edit_snapshot(store, lambda p: p["documents"].append({
        "id": "new:overview", "tool": "new", "category": "overview",
        "content": "New tool documentation", "embedding": None, "embedded_content_hash": None,
    }))
    assert store.synchronize() == 1
    edit_snapshot(store, lambda p: p["documents"].pop())
    assert store.synchronize() == 0
    assert store.document_count == 23


def test_direct_database_edit_and_export(store):
    store.synchronize()
    with psycopg.connect(store.database_url) as conn:
        conn.execute("UPDATE pwnai_rag.documents SET content = 'SQL-edited documentation' "
                     "WHERE id = (SELECT min(id) FROM pwnai_rag.documents)")
    assert store.synchronize() == 1
    assert read_snapshot(store.snapshot_path)["documents"][0]["content"] == "SQL-edited documentation"
    with psycopg.connect(store.database_url) as conn:
        conn.execute("DELETE FROM pwnai_rag.documents WHERE id = "
                     "(SELECT min(id) FROM pwnai_rag.documents)")
    assert store.synchronize() == 0
    assert len(read_snapshot(store.snapshot_path)["documents"]) == 22


@pytest.mark.parametrize("prefer", ["snapshot", "database"])
def test_conflicting_edits_require_explicit_choice(store, prefer):
    store.synchronize()
    edit_snapshot(store, lambda p: p["documents"][0].update(content="File edit"))
    with psycopg.connect(store.database_url) as conn:
        conn.execute("UPDATE pwnai_rag.documents SET content = 'SQL edit' WHERE id = "
                     "(SELECT min(id) FROM pwnai_rag.documents)")
    with pytest.raises(SnapshotConflictError):
        store.synchronize()
    assert store.synchronize(prefer=prefer) == 1
    assert read_snapshot(store.snapshot_path)["documents"][0]["content"] == (
        "File edit" if prefer == "snapshot" else "SQL edit")


def test_embedding_failure_rolls_back(store):
    store.synchronize()
    edit_snapshot(store, lambda p: p["documents"][0].update(content="Changed"))
    before = store.snapshot_path.read_bytes()
    store.embedder.fail = True
    with pytest.raises(AssertionError):
        store.synchronize()
    assert store.snapshot_path.read_bytes() == before
    with store._connect() as conn:
        assert store._rows(conn)[0]["content"] != "Changed"
    store.embedder.fail = False
    assert store.synchronize() == 1


def test_model_change_and_force_reembed(store):
    store.synchronize()
    store.embedder.profile = dict(store.embedder.profile, model="test-model")
    assert store.synchronize() == 23
    assert store.synchronize() == 0
    assert store.synchronize(force=True) == 23


def test_empty_corpus_needs_no_embedding_calls(store):
    store.synchronize()
    edit_snapshot(store, lambda p: p["documents"].clear())
    store.embedder.fail = True
    assert store.synchronize() == 0
    assert store.document_count == 0
    assert not store.is_ready
    assert "not initialized" in ToolRetriever(store).retrieve("sqlmap")


def test_concurrent_startups_embed_once(store):
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = [executor.submit(store.synchronize) for _ in range(2)]
        assert sorted(f.result() for f in futures) == [0, 23]
    assert len(store.embedder.calls) == 1


@pytest.mark.parametrize("tool_filter", [None, "sql_injection"])
def test_hnsw_index_used(store, tool_filter):
    store.synchronize()
    with store._connect() as conn:
        conn.execute("SET LOCAL enable_seqscan = off")
        conn.execute("SET LOCAL enable_sort = off")
        vector = conn.execute("SELECT embedding::text FROM pwnai_rag.documents LIMIT 1").fetchone()["embedding"]
        plan = conn.execute(
            "EXPLAIN (FORMAT JSON) SELECT id FROM pwnai_rag.documents "
            "WHERE (%s::text IS NULL OR tool = %s) "
            "ORDER BY embedding <=> %s::vector LIMIT 4", (tool_filter, tool_filter, vector),
        ).fetchone()["QUERY PLAN"]
        assert "documents_embedding_hnsw" in json.dumps(plan)


@pytest.mark.parametrize("change", [
    lambda p: p.update(format_version=99),
    lambda p: p["documents"].append(copy.deepcopy(p["documents"][0])),
    lambda p: p["documents"][0].update(embedding=[0] * 1024),
    lambda p: p["documents"][0].update(embedding=[float("nan")] * 1024),
    lambda p: p["documents"][0].update(embedding=[1] * 3),
    lambda p: p["documents"][0].update(content=""),
    lambda p: p.update(embedding_profile={"provider": "cohere", "model": "embed-v4.0", "dimensions": 3}),
    lambda p: p["embedding_profile"].update(model=123),
    lambda p: p["embedding_profile"].update(provider=[]),
])
def test_invalid_snapshots_rejected(tmp_path, change):
    payload = read_snapshot(DEFAULT_SNAPSHOT)
    change(payload)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        read_snapshot(path)
