# PwnAI Phase 1 (Local Single-Session Orchestrator)

This is the Phase 1 implementation for the AI layer:
- Two Ollama-backed models (local or cloud mode)
  - Head LLM: `qwen3:8b`
  - Implementor LLM: `qwen2.5-coder:7b`
- LangGraph orchestration loop
- Head observes in prose and retains session memory; it has no RAG or JSON output
- Stateless implementor observes each handoff, retrieves actual tool documentation, and builds one JSON request
- Per-action permission prompt via `ActionFilter`
- No API and no background workers in this phase

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
  embedding:
    provider: "local"       # or "cloud"
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

1. RAG must be enabled and available. Missing retrieval or invalid agent output stops execution rather than generating a guessed fallback action.
2. If credentials are provided with URL mode, the runner attempts pre-auth and injects the session cookie into tool requests.
3. Every action asks operator approval unless `--auto-approve` is set.
4. Reports are written to `artifacts/phase1/`.
5. Head-planner reasoning summaries are printed in terminal and written under `Head Planner Trace` in the report.

## Observation and action flow

The head emits multiple `Observation` evidence assessments, then `Summary`, `Next step`,
`Memory note`, and `Decision` prose sections. These are public decision summaries, not
private internal chain-of-thought. Only the head receives accumulated memory notes,
complete observation records, implementor RAG traces, all attempted requests and execution
outputs/results, runtime notes, and findings. `head_iterations` retains each iteration's
observations, summary, objective, memory notes, and implementor actions in session state.
The implementor receives a fresh handoff containing `observation_summary`, `next_step`, original
target URL/request file, discovered endpoints, authentication status/cookie, navigation, and
a page sample. Relevant remembered facts must be included explicitly by the head.

Each implementor call runs its own bounded observation/action loop. Retrieval actions execute
in the runtime; results return in the next model call before a final JSON request can be accepted.
Each accepted assessment/action and real RAG result is recorded in session state. During
retrieval, session output shows only the action, retrieval progress, and completion message;
retrieved document text is not printed. The implementor can request additional documentation before submitting one executable
tool request. Actual execution remains in the runtime, and its complete results return to the head.
Model-generated retrieval observations and answers following a retrieval in the same response
are discarded. `rag.max_react_iterations_impl` bounds calls and must be at least two.
The existing approval, execution, cookie refresh, analysis, and reporting stages remain in place.
