# PwnAI Phase 1 (Local Single-Session Orchestrator)

This is the Phase 1 implementation for the AI layer:
- Two Ollama-backed models (local or cloud mode)
  - Head LLM: cloud `gpt-oss:120b` (change in YAML as needed)
  - Implementor LLM: cloud `gpt-oss:120b`
- LangGraph orchestration loop
- PostgreSQL/pgvector RAG with HNSW and versioned document embeddings
- Per-action permission prompt via `ActionFilter`
- Express API and browser interface; no background workers

## Run

From repo root:

```bash
python main.py ^
  --goal "Validate SQLi path and collect evidence" ^
  --target-url "http://localhost/vulnerabilities/sqli/" ^
  --login-url "http://localhost/login.php" ^
  --username-field "username" ^
  --password-field "password" ^
  --username "admin" ^
  --password "password"
```

Or using a request file:

```bash
python main.py ^
  --goal "Test request-file SQLi workflow" ^
  --target-request-file "sql_injection/req"
```

## Main Runner (Single or Multiple Cases)

Single case with `ai/main.py`:

```bash
python main.py ^
  --goal "Run single-case Phase 1 test" ^
  --target-url "http://localhost/vulnerabilities/sqli/" ^
  --login-url "http://localhost/login.php" ^
  --username "admin" ^
  --password "password"
```

Multi-case batch using JSON file:

```bash
python main.py ^
  --cases-file "ai/cases.example.json" ^
  --continue-on-error ^
  --summary-out "artifacts/phase1/batch_summary.json"
```

You can copy `ai/cases.example.json` and edit it for your own testing matrix.

## Config

Model and runtime config:
- `ai/config/models.local.yaml`

Choose provider per role in config:

```yaml
models:
  head:
    provider: "cloud"       # or "local"
  implementor:
    provider: "local"       # or "cloud"
rag:
  embedding:
    provider: "cohere"      # independent of the chat providers
    model: "embed-v4.0"
    dimensions: 1024
    api_key_env: "COHERE_API_KEY"
```

Cloud API key (recommended via env var):

```bash
# PowerShell
$env:OLLAMA_API_KEY = "your_key_here"
```

Or put it in repo-root `.env` (auto-loaded at startup):

```env
OLLAMA_API_KEY=your_key_here
```

Then set:

```yaml
ollama:
  providers:
    cloud:
      base_url: "https://ollama.com"
      api_key_env: "OLLAMA_API_KEY"
```

## Notes

1. Follow [RAG database setup](rag/README.md) before running. Unchanged knowledge does not call an embedding model.
2. If credentials are provided with URL mode, the runner attempts pre-auth and injects the session cookie into tool requests.
3. Every action asks operator approval unless `--auto-approve` is set.
4. Reports are written to `artifacts/phase1/`.
5. Head-planner reasoning summaries are printed in terminal and written under `Head Planner Trace` in the report.
