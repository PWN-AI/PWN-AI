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

The head first writes normal public assessments, then emits a final validated JSON record
only when ready. `Thought:`, `Observation:`, and `Correction:` are optional: the model may
use any, repeat them, omit individual labels, or use plain prose. It can also omit prose
entirely when ready. These are concise evidence/decision summaries, not private internal
chain-of-thought. No fixed assessment sections or minimum observation count are required.

A response that needs another assessment round ends with:

```text
Observation: The available evidence leaves a prerequisite unresolved.
Status: need_more_thinking
```

The runtime continues the same phase with its previous assessments and original evidence.
There is no JSON or implementor handoff for that response. When ready, the head ends with
`Status: ready`, immediately followed by exactly one final JSON object for the current
phase (optionally in a JSON fence). The prompt describes the required fields in readable
text; the runtime retains the full schema validation. Status alone cannot bypass schema, evidence-reference,
or candidate-review validation. Each head phase follows this protocol:

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
   The original attempt lessons are attached unchanged to the Markdown report for review.
   No standalone feedback files are written or loaded for later sessions. Every new
   session starts with empty history, an empty attempt ledger, and no previous feedback,
   even when the same target, goal, and output directory are reused. Existing feedback
   artifacts from older versions are ignored and left untouched.

### Current-session working memory

Before each planning call the head receives `working_memory` with:

- `goal_and_constraints`: the original goal/target, remaining objectives, operator constraints,
  and runtime limits. Remaining objectives and open questions are planning assessments.
- `verified_facts`: exact supplied target and application context, URLs with original encoded
  queries plus parsed parameter names/values, authentication state, and analyzer findings
  with provenance/confidence. A finding or operator input is not automatically proof of
  a vulnerability. Attempt arguments are kept in the ledger, not promoted to facts.
- `attempt_ledger`: every current-session attempt's objective, exact request/arguments,
  execution status, return code/error, reflected outcome, progress, and explicitly resolved
  question (if any). Missing reflection is labeled rather than treated as a negative result.
- `feedback`: accumulated current-session lessons, corrections, and retry conditions.
- `open_questions` and `candidate_objectives`: unresolved questions and retained alternatives.
- `latest_result`: the newest actual execution output in full.
- `relevant_older_evidence`: at most three older attempts, selected by explicit candidate
  citations, relevance to the goal/candidates/recent feedback, then recency. Each nonempty
  output field contributes a verbatim window of at most 1,200 characters, with field name,
  offsets, total length, and a truncation flag. New proposal citations trigger fresh evidence
  selection for evaluation. Exact requests, technical values, and artifact paths are retained.

The runtime builds memory directly from authoritative session records; it does not repeatedly
summarize summaries. After execution and reflection, both the ledger and feedback are updated
**before** the next objective is chosen. Full raw executions, observations, candidate evaluations,
and implementor RAG traces remain available in `runs` and `head_iterations` during the session,
but are not replayed in every model prompt. The last three head memory notes and last eight
runtime notes are included as context; they are explicitly separate from observed facts.
`candidate_frontier`, `task_candidates`, `attempt_reflections`, `working_memory`, and
`session_reflection` retain the planning/feedback state. There are no model-weight updates.
`runtime.head_thinking_rounds` sets the assessment-round limit per head phase (default 3,
minimum 1), plus one repair for invalid output. `need_more_thinking` is a real continuation,
not a malformed-output repair. Exhaustion before a valid `ready` prevents executable
handoffs; failed reflection retains conservative runtime feedback. Proposed candidates
still receive a separate evaluation phase before the implementor runs. The same protocol
applies to attempt reflection and session consolidation. Public assessment/status records
are kept under `thinking_steps` in each search-trace phase, `reflection_thinking_steps`
in the attempt's head iteration, and `session_thinking_steps` for consolidation. They stay
in the session archive rather than being replayed in every scoped-memory prompt.

The existing `max_replans` bounds planning/recovery attempts independently. Each planning
decision has proposal + review phases, each executed attempt adds a reflection phase, and
session end adds consolidation; each phase can use its bounded assessment rounds.

If a proposed request repeats a previous tool/action/target/argument combination, the
runtime rejects it without approval or execution and writes `duplicate_rejected` feedback
into the same-session ledger. The head then replans with that feedback and can correct
the request or choose a retained alternative. Changing an objective title alone does not
make a request new. Rejected proposals count against `max_replans`; repeated duplicates
eventually stop with an explicit budget-exhaustion reason. Proposal/attempt IDs remain
unique across rejected and executed requests, so execution IDs may have gaps. Raw output
and `latest_result` still represent actual executions, not rejected proposals.

Each implementor call runs its own bounded observation/action loop. Retrieval actions execute
in the runtime; results return in the next model call before a final JSON request can be accepted.
Each accepted assessment/action and real RAG result is recorded in session state. During
retrieval, session output shows only the action, retrieval progress, and completion message;
retrieved document text is not printed. The implementor can request additional documentation before submitting one executable
tool request. Actual execution remains in the runtime, and its complete results return to the head.
Model-generated retrieval observations and answers following a retrieval in the same response
are discarded. `rag.max_react_iterations_impl` bounds calls and must be at least two.
The existing approval, execution, cookie refresh, analysis, and reporting stages remain in place.
