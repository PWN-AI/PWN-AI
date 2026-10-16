# PwnAI Phase 1 (Local Single-Session Orchestrator)

This is the Phase 1 implementation for the AI layer:
- Three independently configured Ollama-backed roles (local or cloud mode): head, implementor, and feedback
- Roles may use the same underlying model with separate prompts and clients
- LangGraph orchestration loop
- Head delegates one bounded objective using current-session evidence and feedback; it has no retrieval or execution tools
- Feedback agent reviews actual attempts and produces validated lessons; it has no planning, retrieval, or execution tools
- Implementor keeps a ReAct conversation within each objective, retrieving docs and inspecting actual results between tool calls
- Per-action permission prompt via `ActionFilter`
- Each tool action runs in a worker with a runtime deadline; the API streams the session output

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
  feedback:
    provider: "local"       # independently configurable
    model: "your-review-model"
    temperature: 0.1
  embedding:
    provider: "local"       # or "cloud"
```

`models.feedback` can override model, provider, temperature, reasoning, and context size.
If the section is omitted, feedback inherits the head settings but still uses a separate
client and prompt. Partial feedback settings override only the specified head settings.
The checked-in config starts feedback on the same underlying model as the head.

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

## Objective planning and execution

The head writes assessment prose freely, without prescribed labels or sections. There is
no ToT candidate tree, scoring, or separate evaluation call. A response requiring another
assessment round ends with `Status: need_more_thinking` and no JSON. The next call receives
the preceding assessments and the same evidence. When ready, the head emits `Status: ready`
followed by exactly one validated final JSON object; a JSON code fence is allowed.
`runtime.head_thinking_rounds` defaults to 3 rounds per phase, plus one repair for malformed
output. Prose is optional when the decision is straightforward.

The planning JSON selects one objective and includes:

- `decision`: continue or stop, with `stop_reason` for a stop.
- `objective`, `observation_summary`, and `evidence_refs`.
- Important `verified_facts`, separate `hypotheses`, and explicit `success_criteria`.
- `constraints` and `return_conditions` for the implementor.
- Head-only `memory_note`, `remaining_objectives`, and `open_questions`.

The runtime adds the original target, selected current application/authentication context,
and goal to the implementor handoff. It does not transfer the head's full assessment
transcript, raw session history, or head-only memory notes. Exact technical values and
references should be preserved in the summary. Model assessments are not automatically
promoted to verified runtime facts.

```text
Head assessment -> ready + validated objective JSON
  -> Implementor retrieves actual documentation
  -> choose one request -> validate -> approve -> execute -> analyze
  -> feed the actual result back to the implementor
  -> continue locally or return an objective report
  -> Feedback agent review -> validate/update session memory -> next Head objective
```

The implementor uses `Observation:`, `Action:`, and `Action Input:` for its ReAct protocol.
Actions are `retrieve_tool_info`, `retrieve_args`, `retrieve_all_tool_docs`, `execute_tool`,
and `return_to_head`. Only the first action in a response is processed. Fabricated
continuations after retrieval/execution requests are discarded. A retrieval returning documents identified as belonging to a tool
must succeed before that tool can execute; documentation remains available within the
objective and can be supplemented. Retrieval logs show the action, retrieval query/tool,
and completion, without printing document bodies. Raw retrieval records remain in the
session archive.

Every execution result, exact executed request, and analyzer findings return to the
implementor before its next decision. One action may suffice; multiple actions are allowed,
but never required. The implementor returns a report with `status` (completed, blocked,
strategy_change, inconclusive), `summary`, `evidence_refs`, and `remaining_uncertainty`.
A completed report requires a reference to actual successful execution, and remains an
assessment for the head to check against evidence. It does not complete the original goal.
Each new objective starts a fresh implementor conversation.

### Runtime controls

Defaults in `ai/config/models.local.yaml`:

```yaml
runtime:
  max_tasks: 12                    # total reviewed execution attempts across the session
  max_replans: 8                   # further head objectives after the initial one
  max_actions_per_objective: 3
  max_consecutive_failures: 2
  objective_timeout_seconds: 600
  head_thinking_rounds: 3
  feedback_validation_retries: 1  # one output repair after the feedback review
rag:
  max_react_iterations_impl: 3     # RAG calls per implementor decision
  max_validation_retries_impl: 2  # separate repair allowance
```

The runtime returns control for exhausted action/time budgets, consecutive failures, two
successive unchanged results, authentication problems, denied actions, and duplicate
requests. The implementor can return earlier for completed criteria, missing prerequisites,
strategic changes, or another head return condition. Every actual request still passes
validation and the existing action filter. Head objectives and repeated invalid/duplicate
requests consume the finite replan budget; they cannot restart an unlimited recovery loop.
Budget exhaustion is not reported as goal completion.

All retrieval actions provide document text together with the tool identities from the
actual returned store records. General searches satisfy the documentation prerequisite
for every tool present in their results. Queries and text inside documents cannot grant
eligibility to a tool absent from the returned metadata. The implementor receives the
runtime-confirmed documented tool identities with each retrieval. Retrieval, validation
repairs, and the final execution/return decision have separate allowances: at most
`max_react_iterations_impl + max_validation_retries_impl + 1` model calls per decision.
Even the last allowed retrieval is followed by an opportunity to submit a request or
return to the head, within the objective deadline. Runtime rejections are printed explicitly;
retrieved document bodies remain hidden from the terminal.

The objective deadline is checked between model/retrieval decisions and before execution.
Running tool adapters execute in a separate process with the remaining deadline, allowing
the runtime to stop the worker and its subprocess group on POSIX. An in-flight model or
retrieval call still uses its client's timeout; an expired objective cannot start a further
tool action. The implementor gets a final opportunity to inspect the last result and return
when the action budget ends, provided time remains.

CLI `--max-actions-per-objective` overrides the config. The old
`--max-new-tasks-per-replan` flag remains an alias. Batch cases accept the new
`max_actions_per_objective` key, and the API accepts `maxActionsPerObjective` with
`maxNewTasksPerReplan` retained as an alias. `--max-tasks` now enforces the session-wide
attempt limit as documented.

### Current-session memory and feedback

The head's prompt and planner contain no reflection or session-summary phases. The
feedback role has a separate prompt at `ai/agents/feedback/system_prompt.md`, a stateless
review client, and no tool loop. Its input includes the original goal, delegated objective,
exact attempt/results, implementor report, findings, relevant previous lessons, and open
questions. The implementor report is an assessment; execution records remain authoritative.
Its `next_adjustment` is advice, and the head retains the next-objective decision.

Feedback returns one JSON record, without the head's readiness/prose protocol. Invalid
records can be repaired up to `runtime.feedback_validation_retries` times (default 1);
transport failures use conservative runtime fallback. Outcome validation and duplicate
rejection lessons are runtime-owned, so a failed/incomplete attempt cannot become a
completed negative test or verified progress through model output. Feedback is validated
and stored **before** the next head planning call; no intermediate head review is added.

The same feedback role produces the final session assessment, with original attempt
lessons attached unchanged by the runtime. Logs use `[Feedback]` / `[Feedback LLM]`;
objective review traces live in `head_iterations[].feedback_steps`, and the final trace
lives in `session_feedback_steps`. These are session archives, not feedback-agent chat
history. Each review starts a fresh conversation, even when the same model client is reused.

After **every actual action**, the runtime updates the factual ledger before another
implementor decision. At the objective boundary, the dedicated feedback agent reviews all
actual/rejected attempts and the implementor report. It writes evidence-based lessons,
corrections, next adjustments, and retry conditions before another objective is selected.
Execution failures, missing documentation, authentication failures, timeouts, denied actions,
completed negative tests, and positive findings remain distinct. A failed command does not
disprove a hypothesis. Invalid/unavailable feedback falls back to conservative runtime
records, preserving uncertainty and the actual execution status.

Before each planning call, `working_memory` contains:

- `goal_and_constraints`: original goal/target, remaining objectives, operator constraints,
  and runtime limits.
- `verified_facts`: exact supplied/observed values, encoded URL queries and parsed
  parameters, authentication state, and analyzer findings with provenance/confidence.
- `attempt_ledger`: exact requests, execution statuses, errors, outcomes, and resolved
  questions. Pending feedback is labeled `awaiting_reflection`.
- `feedback` and `objective_feedback`: current-session attempt lessons and objective reviews.
- `open_questions`, `active_objective`, and the newest actual `latest_result` in full.
- `relevant_older_evidence`: at most three relevant older attempts, selected by objective
  citations, overlap with the goal/current objective/recent feedback, then recency. Each
  nonempty output field supplies a verbatim window of at most 1,200 characters with offsets
  and truncation metadata. Exact requests and artifact references remain intact.

Full raw executions and public assessment/action traces stay in `runs`, `attempt_records`,
and `head_iterations` during the session. Memory is rebuilt from these authoritative
records instead of repeatedly summarizing summaries. A duplicate is recorded as rejected
without approval or execution; `latest_result` remains the last real execution record.
The head uses the validated feedback to choose another objective or delegate corrected inputs. Unique task IDs can have
gaps for rejected/invalid requests.

The Markdown report retains actual outputs, objective feedback, and original attempt
lessons. A final session assessment does not rewrite those lessons. Every new session starts
with fresh history; no feedback is read from or written to standalone files for reuse in
later sessions. Existing feedback files from older versions are ignored and left untouched.
