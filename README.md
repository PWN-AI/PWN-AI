# PWN-AI

AI-assisted security testing for explicitly authorized targets, with two
Ollama chat models, PostgreSQL/pgvector RAG, an Express API and browser interface.

See [application setup](ai/README.md), [team RAG database workflow](ai/rag/README.md)
and [API documentation](api/README.md). Configure secrets in the root `.env`
using [.env.example](.env.example); never commit real API keys.

The Head and Implementor currently use cloud `gpt-oss:120b`. Knowledge changes
use Cohere; unchanged knowledge and ordinary retrieval make no embedding calls.
Start PostgreSQL with `docker compose up -d --wait rag-db`, then
`python -m ai.rag.ingest` before running scans.

Scanner dependencies are separate: provide sqlmap and Commix in the expected
`tools/` paths, and a platform-compatible Nuclei binary plus Nuclei templates.
These tools are not distributed through Python requirements.
