# PwnAI Phase 1 (Local Single-Session Orchestrator)

This is the Phase 1 implementation for the AI layer:
- Two Ollama-backed models (local or cloud mode)
  - Head LLM: `qwen3:8b`
  - Implementor LLM: `qwen2.5-coder:7b`
- LangGraph orchestration loop
- Head performs bounded candidate search and per-attempt/session reflection; it has no retrieval or execution tools
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

The head uses phase-specific, validated planning JSON. These records contain public
evidence assessments and decisions, not private internal chain-of-thought:

1. **Propose:** initially generate two or three distinct candidate objectives. On later
   calls, use branching for uncertainty, failure, or stalled progress; one direct objective
   is allowed after verified progress. Each candidate carries its evidence references,
   prerequisites, expected information, a self-contained summary, and up to two conditional
   followups. Lookahead is limited to the immediate objective plus one hypothetical level.
2. **Evaluate:** a separate model call rates every candidate's evidence strength,
   information gain, goal alignment, and cost. The runtime prunes/defers unsuitable options,
   blocks unmet prerequisites, and selects one viable candidate by ordinal score:
   `2 * evidence_strength + information_gain + goal_alignment - cost`. Scores are not
   success probabilities. The working frontier holds at most six candidates; retained
   alternatives are reassessed against current evidence during branching. Reconsidering
   a plan never rolls back changes to the target environment.
3. **Execute:** only the selected `observation_summary` and `next_step`, original target,
   and current application context go to the existing stateless implementor. It receives
   no candidate tree or full head history. Authentication, discovered endpoints,
   navigation, and a page sample remain available as before.
4. **Reflect:** after every approved/denied execution, the head records evidence references,
   outcome category, lesson, next adjustment, retry condition, and whether verified progress
   occurred. Compilation failures are also recorded before final reporting. Execution
   errors, missing documentation, failed authentication, timeouts, completed negative tests,
   and positive findings are distinguished. If reflection fails, a runtime record preserves
   uncertainty instead of inventing a lesson or successful result.
5. **Consolidate:** every exit, including attempt-limit exits, produces a session assessment.
   The runtime attaches the original attempt lessons unchanged to the Markdown report and
   `<session_id>_feedback.json` in the output directory. Later sessions recall at most 20
   validated lessons from the 10 most recent feedback artifacts, scoped to the exact same
   target and goal. Historical feedback is guidance, not proof of the current target state.

Only the head receives full iteration history, actual execution results, implementor RAG
traces, findings, runtime notes, and retained feedback. `head_iterations` stores search and
implementor traces; `candidate_frontier`, `task_candidates`, `attempt_reflections`, and
`session_reflection` retain the planning/feedback state. There are no model-weight updates.
Each structured phase allows one correction for invalid output. An invalid proposal or
review stops executable handoffs. The existing `max_replans` bounds real attempts; each
planning decision takes proposal + review calls, each attempt adds a reflection call, and
session end adds a consolidation call (plus at most one correction per phase).

Each implementor call runs its own bounded observation/action loop. Retrieval actions execute
in the runtime; results return in the next model call before a final JSON request can be accepted.
Each accepted assessment/action and real RAG result is recorded in session state. During
retrieval, session output shows only the action, retrieval progress, and completion message;
retrieved document text is not printed. The implementor can request additional documentation before submitting one executable
tool request. Actual execution remains in the runtime, and its complete results return to the head.
Model-generated retrieval observations and answers following a retrieval in the same response
are discarded. `rag.max_react_iterations_impl` bounds calls and must be at least two.
The existing approval, execution, cookie refresh, analysis, and reporting stages remain in place.
