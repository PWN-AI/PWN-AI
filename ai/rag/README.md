# Team RAG database

PostgreSQL stores the tool documents, metadata, text-search index and 1024-dimensional
Cohere vectors. pgvector's cosine HNSW index uses `m=16` and `ef_construction=64`.
The Git-tracked `database/knowledge.json` is the portable database export;
`database/001_schema.sql` recreates the schema and indexes. Live PostgreSQL files,
passwords and API keys do not belong in Git.

## Setup

From the repository root, create/activate a Python environment and install
`requirements.txt`. Copy the variable names from `.env.example` into your root
`.env`; keep any existing keys. Then:

```bash
docker compose up -d --wait rag-db
python -m ai.rag.ingest --test-query "sqlmap dump database"
cd api
npm ci
npm run build
npm start
```

Use a different `PORT` in `.env` if 3000 is occupied. The default PostgreSQL port is
55432, bound only to localhost. If you change the database password or port,
also change `RAG_DATABASE_URL`. Compose database initialization variables apply
only when its volume is first created. The default password is development-only.

The committed snapshot contains real Cohere vectors. Restoring an empty database
with that matching snapshot requires **no Cohere key or embedding calls**. A new/unembedded document requires
`COHERE_API_KEY`. Cohere evaluation keys are free but limited, not unlimited:
[current usage limits](https://docs.cohere.com/docs/rate-limits).

## Collaboration

1. Pull the latest code and snapshot.
2. Edit documents in `database/knowledge.json`, keeping existing IDs stable.
   Add a document with unique `id`, `tool`, `category`, `content`,
   `embedding: null` and `embedded_content_hash: null`. Existing documents can
   retain their vectors/hashes while you edit their content.
3. Run `python -m ai.rag.ingest`. Only added/changed documents are embedded,
   in batches of up to 96. Deletions do not call Cohere.
4. Commit code, schema changes and the resulting snapshot together.
5. Teammates pull and run ingestion (also done automatically at session startup).
   Their local database imports the completed snapshot without re-embedding.

SQL edits to `pwnai_rag.documents` are also detected and exported on ingestion.
Do not edit the synchronization metadata. If both the snapshot and local SQL
database changed, ingestion stops instead of silently discarding edits.
Back up/reconcile them before explicitly choosing `--prefer snapshot` or
`--prefer database`; these choices replace the other side. Review JSON merge
conflicts before ingesting. Git is versioning a portable snapshot, not making
four separate running databases automatically live-synchronized.

For a genuinely shared live environment, connect all four developers to one
managed/private PostgreSQL instance using `RAG_DATABASE_URL`, and coordinate
snapshot exports and branch changes. Avoid using different branch snapshots
against a shared database: an imported snapshot can replace its document rows.

An advisory lock serializes ingestions; database edits are locked during sync.
Embedding failures roll back SQL changes. Snapshot writes are atomic.
Changing the embedding model/profile or using `--force` re-embeds all documents.
Changing dimensions requires a new schema/index; currently only 1024 is supported.

## Retrieval without embedding calls

Normal queries use PostgreSQL full-text search to choose anchor documents, then
HNSW finds neighbors using those documents' **already stored vectors**. Reciprocal
rank fusion combines the results. Tool filters are applied to both stages.
There is no downloaded embedding model and no query-embedding API call.

Planning, replanning and compilation retrieve initial database documentation
before invoking the LLM, so models that return plain JSON are still grounded.
ReAct requests can retrieve additional context; generated observations are never
accepted as a substitute for actual database results.

This is document-to-document semantic expansion, not arbitrary semantic query
embedding. Queries with no lexical overlap can miss relevant documents;
tool-scoped queries fall back to that tool's overview. For fully semantic novel
queries, a query embedding API call would be necessary.

## Tests

The integration tests must use a disposable database whose name ends in
`_test`. They reset only that test database's `pwnai_rag` schema and use synthetic
vectors; synthetic vectors are never written to the shipped database snapshot.

```bash
docker compose exec -T rag-db psql -U pwnai -d pwnai_rag -c 'CREATE DATABASE pwnai_rag_test;'
RAG_TEST_DATABASE_URL=postgresql://pwnai:pwnai_dev@localhost:55432/pwnai_rag_test python -m pytest -q
```

Install `pytest` separately for development. Without the test URL, PostgreSQL
tests are skipped; provider/snapshot validation and existing unit tests still run.

For an opt-in live scanner check, start an isolated DVWA at
`http://127.0.0.1:8088`, initialize its database, and provide the scanner binaries.
Then run `python -m ai.tests.smoke_local_scanners`. It tests pre-authentication,
SQL injection, command injection, one known login credential and reflected XSS
against that local instance only. The Nuclei template in `ai/tests/fixtures`
is a DVWA test fixture, not a production template library. Results are written
to ignored `artifacts/smoke/`. Stop the disposable DVWA after testing.

Install a real Nuclei template library separately, for example
`nuclei -update-templates -update-template-dir tools/nuclei-templates` from the
repository root. The wrapper checks populated project, tools and user template
directories; a scan does not silently download updates. You can pass a specific
directory in the Nuclei tool request's `args.nuclei_templates`.
