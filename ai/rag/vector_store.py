"""PostgreSQL storage: text-search anchors plus HNSW, with no query model calls."""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from ai.rag.embeddings import CohereEmbedder, validate_vector

DEFAULT_DATABASE_URL = "postgresql://pwnai:pwnai_dev@localhost:55432/pwnai_rag"
DEFAULT_SNAPSHOT = Path(__file__).parent / "database" / "knowledge.json"
DOCUMENT_FIELDS = ("id", "tool", "category", "content", "embedded_content_hash", "embedding")


def digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def document_hash(document: Dict[str, Any]) -> str:
    return digest({key: document[key] for key in ("tool", "category", "content")})


def read_snapshot(path: Path) -> Dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("format_version") != 1:
        raise ValueError("Unsupported RAG database snapshot format.")
    if set(payload) != {"format_version", "embedding_profile", "documents"}:
        raise ValueError("Unexpected database snapshot fields.")
    profile = payload["embedding_profile"]
    if (not isinstance(profile, dict) or set(profile) != {"provider", "model", "dimensions"}
            or any(not isinstance(profile[key], str) or not profile[key].strip()
                   for key in ("provider", "model"))
            or profile["dimensions"] != 1024):
        raise ValueError("The snapshot needs an embedding profile with 1024 dimensions.")
    documents = payload["documents"]
    if not isinstance(documents, list):
        raise ValueError("Snapshot documents must be a list.")
    ids = set()
    for document in documents:
        if not isinstance(document, dict) or set(document) != set(DOCUMENT_FIELDS):
            raise ValueError("Unexpected document fields in snapshot.")
        for field in ("id", "tool", "category", "content"):
            if not isinstance(document[field], str) or not document[field].strip():
                raise ValueError(f"Document {field} must be a nonempty string.")
        if document["id"] in ids:
            raise ValueError(f"Duplicate document ID: {document['id']}")
        ids.add(document["id"])
        if document["embedded_content_hash"] is not None and not isinstance(document["embedded_content_hash"], str):
            raise ValueError("Invalid embedded content hash.")
        if document["embedding"] is not None:
            validate_vector(document["embedding"])
    payload["documents"] = sorted(documents, key=lambda document: document["id"])
    return payload


def write_snapshot(path: Path, payload: Dict[str, Any]) -> None:
    """Write a deterministic portable database export atomically."""
    content = json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    if path.exists() and path.read_text(encoding="utf-8") == content:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                         prefix=f".{path.name}.", delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class SnapshotConflictError(ValueError):
    pass


class ToolVectorStore:
    def __init__(self, database_url: Optional[str] = None,
                 snapshot_path: Path = DEFAULT_SNAPSHOT,
                 embedder: Optional[CohereEmbedder] = None):
        self.database_url = database_url or os.getenv("RAG_DATABASE_URL") or DEFAULT_DATABASE_URL
        self.snapshot_path = Path(snapshot_path)
        self.embedder = embedder or CohereEmbedder()
        self.last_embedded_count = 0

    def _connect(self):
        return psycopg.connect(self.database_url, connect_timeout=10, row_factory=dict_row)

    def _initialize(self, connection) -> None:
        connection.execute((Path(__file__).parent / "database" / "001_schema.sql").read_text(encoding="utf-8"))

    @staticmethod
    def _rows(connection) -> List[Dict[str, Any]]:
        rows = connection.execute(
            "SELECT id, tool, category, content, embedded_content_hash, embedding::text "
            "FROM pwnai_rag.documents ORDER BY id"
        ).fetchall()
        for row in rows:
            row["embedding"] = json.loads(row["embedding"]) if row["embedding"] else None
        return rows

    @staticmethod
    def _payload(rows: List[Dict[str, Any]], profile: Dict[str, Any]) -> Dict[str, Any]:
        return {"format_version": 1, "embedding_profile": profile,
                "documents": sorted(rows, key=lambda document: document["id"])}

    @staticmethod
    def _metadata(connection) -> Dict[str, Any]:
        return {row["key"]: row["value"] for row in connection.execute(
            "SELECT key, value FROM pwnai_rag.metadata"
        ).fetchall()}

    @staticmethod
    def _set_metadata(connection, key: str, value: Any) -> None:
        connection.execute(
            "INSERT INTO pwnai_rag.metadata (key, value) VALUES (%s, %s) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", (key, Jsonb(value))
        )

    @staticmethod
    def _upsert(connection, document: Dict[str, Any]) -> None:
        embedding = json.dumps(document["embedding"]) if document["embedding"] is not None else None
        connection.execute(
            "INSERT INTO pwnai_rag.documents (id, tool, category, content, embedded_content_hash, embedding) "
            "VALUES (%s, %s, %s, %s, %s, %s::vector) ON CONFLICT (id) DO UPDATE SET "
            "tool = EXCLUDED.tool, category = EXCLUDED.category, content = EXCLUDED.content, "
            "embedded_content_hash = EXCLUDED.embedded_content_hash, embedding = EXCLUDED.embedding",
            (document["id"], document["tool"], document["category"], document["content"],
             document["embedded_content_hash"], embedding),
        )

    def synchronize(self, *, force: bool = False, prefer: str = "auto") -> int:
        """Restore pulled snapshots, embed changed rows, and export DB edits.

        An advisory lock serializes updates. Simultaneous file and database edits
        need explicit conflict resolution. Embedding failures roll back SQL changes.
        """
        if prefer not in {"auto", "snapshot", "database"}:
            raise ValueError("prefer must be auto, snapshot, or database.")
        self.last_embedded_count = 0
        with self._connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(715021841)")
            # Read after taking the lock: another synchronizer may have exported
            # this file while we waited. Do not restore an obsolete copy.
            snapshot = read_snapshot(self.snapshot_path)
            snapshot_digest = digest(snapshot)
            self._initialize(connection)
            connection.execute("LOCK TABLE pwnai_rag.documents IN SHARE ROW EXCLUSIVE MODE")
            metadata = self._metadata(connection)
            rows = self._rows(connection)
            stored_profile = metadata.get("embedding_profile", snapshot["embedding_profile"])
            database_digest = digest(self._payload(rows, stored_profile))
            file_changed = snapshot_digest != metadata.get("snapshot_digest")
            database_changed = bool(metadata) and database_digest != metadata.get("database_digest")
            if file_changed and database_changed and prefer == "auto":
                raise SnapshotConflictError(
                    "Both knowledge.json and PostgreSQL changed. Reconcile the edits, then run "
                    "python -m ai.rag.ingest --prefer snapshot or --prefer database."
                )
            if not metadata or (file_changed and prefer != "database"):
                connection.execute("DELETE FROM pwnai_rag.documents")
                rows = snapshot["documents"]
                stored_profile = snapshot["embedding_profile"]
                for document in rows:
                    self._upsert(connection, document)

            profile_changed = stored_profile != self.embedder.profile
            changed = [document for document in rows if force or profile_changed
                       or document["embedding"] is None
                       or document["embedded_content_hash"] != document_hash(document)]
            if changed:
                texts = [f"[{document['tool']}] [{document['category']}]\n{document['content']}"
                         for document in changed]
                vectors = self.embedder.embed_documents(texts)
                if len(vectors) != len(changed):
                    raise ValueError("Embedding provider returned an unexpected vector count.")
                for document, vector in zip(changed, vectors):
                    validate_vector(vector)
                    document["embedding"] = vector
                    document["embedded_content_hash"] = document_hash(document)
                    self._upsert(connection, document)

            payload = self._payload(self._rows(connection), self.embedder.profile)
            exported_digest = digest(payload)
            self._set_metadata(connection, "embedding_profile", self.embedder.profile)
            self._set_metadata(connection, "snapshot_digest", exported_digest)
            self._set_metadata(connection, "database_digest", exported_digest)
            # The export can restore SQL state if the following commit fails.
            write_snapshot(self.snapshot_path, payload)
            connection.execute("ANALYZE pwnai_rag.documents")
        self.last_embedded_count = len(changed)
        return self.last_embedded_count

    @property
    def document_count(self) -> int:
        with self._connect() as connection:
            return connection.execute("SELECT count(*) AS count FROM pwnai_rag.documents").fetchone()["count"]

    @property
    def is_ready(self) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT count(*) AS total, count(embedding) AS embedded FROM pwnai_rag.documents"
            ).fetchone()
        return row["total"] > 0 and row["total"] == row["embedded"]

    def documents_for_tool(self, tool_name: str) -> List[Dict[str, str]]:
        with self._connect() as connection:
            return connection.execute(
                "SELECT id, tool, category, content FROM pwnai_rag.documents WHERE tool = %s ORDER BY id",
                (tool_name,),
            ).fetchall()

    def search(self, query: str, top_k: int = 4,
               tool_filter: Optional[str] = None) -> List[Tuple[Dict[str, str], float]]:
        """Fuse text results and HNSW neighbors of stored anchors, without a model.

        This is document-to-document expansion, not semantic embedding of a novel
        query. All document vectors are computed at synchronization time.
        """
        if top_k <= 0:
            return []
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]*", query.lower())
        if not tokens:
            return []
        lexical_query = " OR ".join(tokens[:80])
        pool_size = max(top_k * 4, 12)
        with self._connect() as connection:
            anchors = connection.execute(
                "SELECT id, tool, category, content, embedding::text, "
                "ts_rank_cd(search_text, websearch_to_tsquery('english', %s), 32) AS rank "
                "FROM pwnai_rag.documents WHERE search_text @@ websearch_to_tsquery('english', %s) "
                "AND (%s::text IS NULL OR tool = %s) ORDER BY rank DESC, id LIMIT %s",
                (lexical_query, lexical_query, tool_filter, tool_filter, pool_size),
            ).fetchall()
            if not anchors:
                if tool_filter is None:
                    return []
                anchors = connection.execute(
                    "SELECT id, tool, category, content, embedding::text FROM pwnai_rag.documents "
                    "WHERE tool = %s ORDER BY (category = 'overview') DESC, id LIMIT 1",
                    (tool_filter,),
                ).fetchall()
            documents: Dict[str, Dict[str, str]] = {}
            scores: Dict[str, float] = {}

            def add(rows, weight: float) -> None:
                for rank, row in enumerate(rows, 1):
                    key = row["id"]
                    documents[key] = {field: row[field] for field in ("id", "tool", "category", "content")}
                    scores[key] = scores.get(key, 0.0) + weight / (60 + rank)

            add(anchors, 2.0)
            connection.execute("SET LOCAL hnsw.ef_search = 64")
            connection.execute("SET LOCAL hnsw.iterative_scan = 'strict_order'")
            # Use HNSW explicitly even for the small testing corpus.
            connection.execute("SET LOCAL enable_seqscan = off")
            connection.execute("SET LOCAL enable_sort = off")
            for anchor in anchors[:3]:
                if anchor["embedding"] is None:
                    continue
                neighbors = connection.execute(
                    "SELECT id, tool, category, content FROM pwnai_rag.documents "
                    "WHERE (%s::text IS NULL OR tool = %s) "
                    "ORDER BY embedding <=> %s::vector LIMIT %s",
                    (tool_filter, tool_filter, anchor["embedding"], pool_size),
                ).fetchall()
                add(neighbors, 1.0)
        ranked = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
        maximum = max(scores.values()) if scores else 1.0
        return [(documents[key], scores[key] / maximum) for key in ranked]
