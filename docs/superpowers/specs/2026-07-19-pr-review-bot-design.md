# Multi-Agent GitHub PR Review Bot — Design

Status: approved
Date: 2026-07-19

## Purpose

PR opened/updated on GitHub → bot reviews the diff across three dimensions in parallel (security, style/lint, test coverage) → an aggregator agent merges results into one PR comment → posted back to GitHub. Built to demonstrate real distributed-systems patterns (durable workflow, versioned state, staleness handling, circuit breaking, saga compensation) around a multi-agent LLM pipeline, fully open source, self-hosted model included.

## Non-goals

- Not a general-purpose CI system. No lint autofix, no blocking merges.
- No multi-repo / multi-tenant SaaS concerns (billing, org-level config UI). Single GitHub App install, single or few test repos.
- No horizontal scaling of the Temporal worker or Ollama server for this build — single worker process, single local GPU. Scaling out is future work, not in scope.

## Architecture

```
GitHub PR (opened/synchronize)
   │  webhook POST (HMAC-signed, GitHub App)
   ▼
FastAPI webhook-service
   │  verify signature → extract {repo, pr_number, head_sha, installation_id}
   │  start/signal Temporal workflow, workflow_id = f"{repo}#{pr_number}"
   │  (Stage 2 uses f"{owner}/{repo}#{pr_number}@{head_sha}" instead — see
   │  Stage 2 implementation notes; the collision-free {repo}#{pr_number}
   │  scheme with explicit supersede handling arrives in Stage 4)
   │  return 200 immediately — no inline work
   ▼
Temporal Server (local dev: `temporal server start-dev`, built-in Web UI)
   ▼
Temporal Worker process
   └─ PRReviewWorkflow
        ├─ Activity: fetch diff (GitHub App installation token)
        ├─ 3x parallel Activities → LangGraph agent nodes
        │     (Security / Style-Lint / Test-Coverage)
        │     each wraps its Ollama call in a pybreaker circuit breaker
        │     each retried independently via Temporal retry policy
        │     each writes a result row to Postgres (agent_results)
        ├─ Activity: staleness check — refetch PR head SHA from GitHub
        │     mismatch → abandon post, mark run `stale` in Postgres;
        │     the newer webhook event's workflow run takes over
        ├─ Activity: Aggregator — LangGraph node reads all agent_results
        │     rows for this head_sha, merges/resolves conflicts, drafts
        │     final markdown comment
        ├─ Activity: post comment → GitHub API
        └─ Compensation (saga): if a later activity fails permanently
              after an earlier partial comment was already posted, a
              compensating activity edits/deletes it. Registered per
              activity, invoked in reverse order on unrecoverable failure.
```

Redis is deliberately **not** in the stack. The original sketch used it for queueing and short-lived locks, but Temporal already owns durable task queues internally, and one workflow per `(repo, pr_number)` gives natural dedupe on concurrent pushes without a separate lock. Add Redis back only if a concrete need shows up later (e.g. caching GitHub API responses) — YAGNI for this build.

## Components

| Component | Responsibility | Depends on |
|---|---|---|
| `webhook-service` (FastAPI) | Verify GitHub signature, parse payload, start/signal Temporal workflow, ack fast | Temporal client SDK |
| `github-client` (shared lib) | GitHub App JWT + installation-token exchange, diff fetch, comment post/edit/delete, HEAD SHA fetch | GitHub REST API |
| `temporal-worker` | Hosts `PRReviewWorkflow` + all Activities; long-running process polling the Temporal task queue | Temporal server, `review-graph`, `github-client`, Postgres |
| `review-graph` (LangGraph) | Supervisor node fans out to 3 agent nodes + aggregator node | Ollama client wrapper |
| Ollama client wrapper | Calls local Ollama server (OpenAI-compatible endpoint), wrapped per-agent in a pybreaker circuit breaker | Ollama server, `pybreaker` |
| Postgres | Versioned review state: `pr_reviews`, `agent_results`, `posted_comments` | — |
| GitHub App + smee.io tunnel client | Local dev webhook delivery from GitHub to `webhook-service` | GitHub App registration |

Each agent runs as its own Temporal Activity (not one Activity wrapping the whole LangGraph run) so a single agent's failure/circuit-break doesn't force a rerun of the other two — this is what makes "partial reviews" possible.

## Model

Qwen2.5-Coder-3B-Instruct, Q4_K_M quant, served via **Ollama** (not vLLM — vLLM targets batched/high-VRAM serving; Ollama fits the available RTX 3050 Ti Laptop GPU, 4GB VRAM). Ollama exposes an OpenAI-compatible endpoint, so the client wrapper is swappable to vLLM later without touching the agent code.

Known tradeoff: Ollama serializes requests more than vLLM. Three agent activities calling Ollama "in parallel" will queue at the model server even though Temporal dispatches them concurrently. Acceptable for a demo; call out in the blog post. If latency is too high in practice, fall back to Qwen2.5-Coder-1.5B-Instruct Q4.

## Data model

```sql
pr_reviews
  id            uuid pk
  repo          text
  pr_number     int
  head_sha      text
  status        enum(pending, running, partial, complete, stale, failed)
  created_at    timestamptz
  updated_at    timestamptz
  unique(repo, pr_number, head_sha)   -- version key

agent_results
  id               uuid pk
  review_run_id    fk -> pr_reviews.id
  agent_name       text  (security | style | test_coverage)
  status           enum(pending, success, failed, breaker_open)
  output           jsonb   -- findings
  created_at       timestamptz
  unique(review_run_id, agent_name)

posted_comments
  id                 uuid pk
  review_run_id      fk -> pr_reviews.id
  github_comment_id  bigint
  posted_at          timestamptz
  compensated        boolean default false
```

The aggregator queries `agent_results` by `review_run_id`. The staleness-check activity compares the workflow's `head_sha` against the PR's current HEAD (fetched live from GitHub) before allowing a post.

## Error handling

- **Staleness**: right before posting, refetch PR HEAD SHA. Mismatch → mark the run `stale`, discard, do not post. The `synchronize` event for the new push already started/will start a fresh workflow keyed to the new SHA.
- **Circuit breaker**: `pybreaker` wraps each agent's Ollama call. Repeated timeouts open the breaker for that agent; the workflow proceeds without that agent's result, and the aggregator notes "X check skipped" in the final comment rather than hanging the whole PR.
- **Saga/compensation**: each activity that has a side effect (posting/editing a GitHub comment) registers a compensating action. If a downstream activity fails permanently (all Temporal retries exhausted) after an earlier partial comment was posted, the compensation edits or deletes that comment so no broken half-review is left visible on a real PR.
- **Temporal retries**: standard exponential-backoff retry policy per activity for transient failures (network blips, GitHub API rate limits, Ollama momentarily unavailable); circuit breaker exists specifically to stop repeated retries from being useful once a target is reliably down.

## Testing approach

- Unit tests per component: `github-client` (signature verification, payload parsing) with recorded fixture payloads; Postgres data-access layer against a test database; aggregator merge/conflict-resolution logic against canned `agent_results` fixtures.
- Temporal workflow logic tested with `temporalio`'s test framework (time-skipping test environment) — covers retry, staleness-abort, and compensation paths without waiting on real timers or a real GPU.
- LangGraph agent nodes tested against fixture diffs with the real Ollama model (slower, run less frequently) to validate prompt quality, separate from the fast workflow-logic tests.
- End-to-end: a real GitHub App installed on a scratch/test repo, driven manually during each build stage's demo (see Build stages below) — force-push mid-review for staleness, kill Ollama for circuit breaker, inject a failure for saga compensation.

## Build stages (component by component, vertical-slice-first)

Vertical slice first, not infra-first: get GitHub App + webhook + one real LLM call posting a real comment working before adding Temporal, multi-agent parallelism, or resilience patterns. Retires the riskiest/fiddliest integration (GitHub App + tunnel) first and stays demoable throughout.

| Stage | Adds | Deliverable/demo |
|---|---|---|
| **0 — Scaffold** | Repo layout, docker-compose (Postgres + Temporal dev server), env template | `docker-compose up` → Postgres + Temporal UI reachable |
| **1 — Vertical slice** | GitHub App (manifest, webhook secret, private key), smee.io tunnel, FastAPI webhook, `github-client` (diff fetch + comment post), ONE hardcoded Ollama call — no Temporal, no Postgres yet | Open a real PR → bot posts one LLM-generated review comment. Proves App+webhook+Ollama loop end-to-end |
| **2 — Temporal durability** | Wrap stage-1 logic as `PRReviewWorkflow` + activities; webhook now starts a workflow instead of doing work inline; add `pr_reviews` table | Kill the worker mid-run, restart → workflow resumes from Temporal history |
| **3 — Multi-agent + aggregator** | LangGraph supervisor graph, 3 agent nodes (distinct prompts), aggregator node, `agent_results` table, 3 parallel Temporal activities | Comment is now a 3-section merged review |
| **4 — Staleness** | head_sha versioning throughout; a re-push starts a new workflow; staleness-check activity runs right before posting | Force-push mid-review → old run discarded, new run posts |
| **5 — Circuit breaker** | `pybreaker` around each agent's Ollama call | Stop Ollama mid-demo → breaker opens → partial review posted, noting "security check skipped" |
| **6 — Saga/compensation** | Compensating activity: edit/delete a partial comment if a later activity fails permanently | Inject a failure after a partial post → comment is auto-cleaned up |
| **7 — Polish** | README, architecture diagram, demo script/Makefile | Repeatable, scripted demo run |

Each stage after 0 ends in something demoable; stages are additive and don't require rework of earlier stages' code, only wrapping/extending it.

## Stage 2 implementation notes

Decisions made when brainstorming Stage 2 in detail (the table row above only sketched the goal).

**Process topology:** two separate local processes, matching the Components table above — `prbot.app:app` (FastAPI, unchanged role except the webhook now starts a workflow instead of running the review inline) and a new `prbot.worker` entrypoint (long-running Temporal Worker hosting the workflow + activities). New files: `src/prbot/workflows.py` (`PRReviewWorkflow`), `src/prbot/activities.py` (`fetch_diff_activity`, `review_activity`, `post_comment_activity`, `set_review_status_activity`), `src/prbot/worker.py`, `src/prbot/db.py` (`asyncpg`-based, `init_db`/`set_review_status`, idempotent `CREATE TABLE IF NOT EXISTS` — no migration framework, YAGNI at this scope). New deps: `temporalio`, `asyncpg`.

**Webhook behavior:** fast-ack — webhook calls the Temporal client's `start_workflow` (non-blocking, returns once the workflow is accepted, not once it completes) and responds immediately. Fixes the GitHub ~10s webhook-timeout cosmetic issue noted in Stage 1's final review.

**Workflow ID:** `f"{owner}/{repo}#{pr_number}@{head_sha}"` — includes head_sha so each push gets its own workflow run, sidestepping Temporal's "already running" collision. Stage 4 introduces the collision-free `{repo}#{pr_number}` scheme with explicit cancel-superseded-run handling; Stage 2 doesn't need that complexity yet.

**Activity granularity:** 3 activities for the review pipeline (`fetch_diff_activity`, `review_activity`, `post_comment_activity`), each independently retried by Temporal's default policy. This is what makes the durability demo real — killing the worker mid-LLM-call and restarting resumes from that activity, not from the top. Activities call `github_client`/`llm_client` functions directly (not through Stage 1's `review.py:run_stage1_review`, which bundles all four steps into one call) — `review.py` and its tests are left untouched.

**Secrets stay out of workflow history:** the workflow only carries plain identifiers (owner, repo, pr_number, installation_id, head_sha) as activity arguments. Each activity reads `Settings` (App ID, private key path) itself at execution time rather than the workflow passing key material through Temporal's persisted event history.

**DB schema for this stage** (subset of the Data model above — `agent_results`/`posted_comments` arrive in Stage 3/6):
```sql
CREATE TABLE IF NOT EXISTS pr_reviews (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  repo text NOT NULL,
  pr_number int NOT NULL,
  head_sha text NOT NULL,
  status text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  UNIQUE(repo, pr_number, head_sha)
);
```
`set_review_status_activity` upserts on `(repo, pr_number, head_sha)`, called at workflow start (`running`), and at the end (`complete` or `failed` — the workflow catches an exhausted-retries `ActivityError`, records `failed`, then re-raises so Temporal's own history also shows the failure).

**Testing:** activities are plain async functions, testable directly without the Temporal runtime (same respx/monkeypatch style as Stage 1). The workflow is tested with `temporalio.testing.WorkflowEnvironment` (time-skipping test server) with mocked activities, covering call order and the retry-to-failed path. `db.py` is tested against the real docker-compose Postgres, using a unique `(repo, pr_number, head_sha)` per test to avoid collisions — no separate test-DB teardown machinery at this scope.

**Demo checkpoint:** open a PR, kill the worker process while the LLM call is in flight, restart the worker, confirm the comment still posts and Temporal's Web UI (`localhost:8233`) shows the workflow resumed from history rather than restarting from step 1.

## Stages 3-6 implementation notes

Decided together when brainstorming the remaining stages in one pass, since the user wanted to move through all of them consecutively. Each stage still gets its own plan and subagent-driven build, but the cross-cutting decisions are recorded once here.

**Stage 3 — no LangGraph dependency.** Temporal already provides the parallel fan-out, retries, and durability that a LangGraph graph would otherwise provide a second time. The "supervisor/agent/aggregator" shape is implemented directly as Temporal activities: `security_review_activity`, `style_review_activity`, `test_coverage_review_activity` (all reuse the existing `ReviewInput` dataclass — same `diff_text` field, different system prompt baked into each activity), run concurrently in the workflow via `asyncio.gather` over three `execute_activity` calls, then `aggregate_activity` merges the three text results into one markdown comment body (`### Security` / `### Style` / `### Test Coverage` sections, simple concatenation — no synthesis LLM call). `llm_client.py` gains `review_diff_with_prompt(diff_text, base_url, model, system_prompt) -> str`; the existing `review_diff` becomes a thin wrapper calling it with the original `REVIEW_SYSTEM_PROMPT`, so Stage 1/2's `review_activity`/`review.py` stay behaviorally unchanged. `AggregateInput`'s three result fields are typed `str | None` from the start (not `str`), even though Stage 3 alone always populates all three — this is so Stage 5's circuit breaker doesn't need to touch this dataclass again when an agent gets skipped.

**Stage 4 — staleness-check activity, no workflow-cancellation.** Stage 2 already keys each push to its own workflow ID (`.../{pr_number}@{head_sha}`), so a force-push during review simply starts a second, independent workflow for the new SHA — no need to cancel the in-flight one. Add `check_staleness_activity(StalenessCheckInput) -> bool`: refetches the PR's current HEAD SHA from GitHub (new `github_client.get_pr_head_sha(token, owner, repo, pr_number) -> str`, using the JSON media type to read `head.sha`, distinct from `fetch_pr_diff`'s `.diff` media type) and compares it to the workflow's own `head_sha`. Called after `aggregate_activity`, before `post_comment_activity`: if stale, `set_status("stale")` and return early without posting. `status` is a free-text column (not a real Postgres enum), so no schema migration is needed to add the `"stale"` value.

**Stage 5 — pybreaker per agent, activities swallow `CircuitBreakerError` internally.** One module-level `pybreaker.CircuitBreaker` instance per agent in `activities.py` (breaker state persists across workflow executions within a worker process's lifetime, which is the intended behavior — it tracks repeated failures over time, not per-review). Each agent activity calls its Ollama request through `breaker.call_async(...)` and catches `pybreaker.CircuitBreakerError` specifically, returning `None` instead of raising. This means a skipped agent is a normal (non-exceptional) activity result — `asyncio.gather` in the workflow doesn't need new error-handling, and `aggregate_activity` already treats `None` as "skipped" per the Stage 3 design above. Any *other* exception from an agent activity still propagates as `ActivityError` and fails the workflow, same as before — the breaker only changes behavior for the specific "this target is reliably down" case it exists for.

**Stage 6 — saga triggered by a demo-only failure-injection activity.** In the current pipeline, `post_comment_activity` is the last side-effecting step before the final `set_review_status_activity("complete")` call — there's no naturally-occurring "later activity fails after an earlier partial post" scenario to demo without manufacturing one. Add `check_demo_failure_injection_activity() -> bool`, reading an env var (`PRBOT_DEMO_FORCE_FAILURE_AFTER_POST`) — activities can freely do I/O/env reads (unlike workflow code, which must stay deterministic). Called right after `post_comment_activity` succeeds; if it returns `True`, the workflow raises, triggering compensation: `delete_comment_activity(DeleteCommentInput) -> None` (new `github_client.delete_pr_comment(token, owner, repo, comment_id) -> None`) removes the just-posted comment, then `set_status("failed")`, then re-raise. This gives a reliably reproducible demo (flip the env var, push, watch the comment appear and then get deleted) without depending on genuine flakiness.

**Stage 7 — polish, lighter process.** README and demo script are documentation/tooling, not core logic — handled as direct edits rather than full subagent-driven-development ceremony, proportional to the risk (low) of getting them wrong.

## Stage 8 — Versioned state log + data contract gate (folder reorg)

Added after all 7 stages were complete, in response to a gap-analysis against a conference talk on production multi-agent patterns: the project already had orchestration, circuit breakers, retries, and saga compensation, but no per-agent-step immutable version history and no validation gate on agent output content (only shape, via dataclasses). This closes both gaps and reorganizes the flat `src/prbot/*.py` layout into responsibility-based subpackages.

**Folder reorg.** Pure move + import-path fixup, no behavior change, verified by the full test suite staying green on the move alone before new logic lands:
```
src/prbot/
  orchestration/  workflows.py, worker.py
  agents/         activities.py (fetch_diff/post_comment/set_status/aggregate/staleness/demo-injection/delete-comment/legacy review), security.py, style.py, test_coverage.py (each split out with its own prompt + breaker)
  state/          db.py, versioned_log.py (new)
  contracts/      schemas.py (new), validation.py (new)
  integrations/   github_client.py, llm_client.py
  api/            app.py, events.py
  config.py, activity_types.py   -- stay at root, shared by everything
```

**Versioned state log.** New append-only table, one row per data-producing handoff (not every activity — status-only and side-effect-only steps like `set_status`/`post_comment`/`staleness_check` don't produce data another agent consumes, so they're excluded):
```sql
CREATE TABLE IF NOT EXISTS pr_review_state_versions (
    id BIGSERIAL PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    step_seq INTEGER NOT NULL,
    agent TEXT NOT NULL,
    status TEXT NOT NULL,        -- 'ok' | 'circuit_breaker_open' | 'contract_rejected:<reason>'
    output TEXT,                  -- null unless status='ok'
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (workflow_id, step_seq)
);
```
`step_seq` 1=fetch_diff, 2=security_review, 3=style_review, 4=test_coverage_review, 5=aggregate. `workflow_id` reuses Temporal's own workflow ID (`owner/repo#pr@sha`, via `workflow.info().workflow_id` inside workflow code — deterministic, no new ID scheme). Insert-only by convention (no code path issues an UPDATE/DELETE against this table) — ordering by `step_seq` within a `workflow_id` reconstructs the full per-run agent lineage for replay/debugging.

**Contract gate.** `contracts/validation.py::validate_agent_output(output: str, *, reference_text: str | None) -> ContractResult` — lightweight heuristics on the output envelope, not a structured-JSON/confidence-score contract (the review agents produce free-form markdown by design, and forcing structured output onto the 3B local model was judged a needless prompt-engineering risk for this gap-fix). Rejects: empty/whitespace-only; length <20 or >20000 chars; output identical to `reference_text` (the diff, for review-step echo detection); output starting with `Traceback`/`Error:`. `ContractResult`/thresholds live in `contracts/schemas.py`.

**Wiring.** New `record_state_version_activity` (in `state/versioned_log.py`, registered in `worker.py` per the standing global constraint) does validation + the INSERT in one activity call — no extra Temporal round-trip beyond the write itself. Workflow calls it explicitly after each data-producing activity, mirroring the orchestrator-owns-state-versioning shape: after `fetch_diff_activity` (step 1) — since a contract rejection here comes back as a normal (non-exceptional) `None` result, not an `ActivityError`, the workflow explicitly checks for it (same explicit-check shape as Stage 6's demo-injection check) and if rejected, aborts itself: `set_status("failed")`, raise `ApplicationError`; after the 3 concurrent reviews (steps 2-4, recorded concurrently, each already `None` from a tripped breaker skips validation and records `circuit_breaker_open` directly) — the *validated* result (possibly newly-`None` from a contract rejection) replaces the raw one before `aggregate_activity` runs, so a contract-rejected section shows "check skipped" exactly like a breaker-open one; after `aggregate_activity` (step 5, always accepted in practice, recorded for lineage completeness).

**Debug tooling.** `scripts/replay_state.py <workflow_id>` — queries `pr_review_state_versions` for that workflow_id, prints step_seq/agent/status/output(truncated) in order. Concrete demo of the "replay version history to binary-search a regression" capability the talk calls out.

**Testing.** Unit tests for `validate_agent_output` (all 4 reject paths + accept), `versioned_log.record_step`/query (same autouse pool-reset fixture pattern as `test_db.py`), updated `test_workflows.py` (fake worker now registers `record_state_version_activity`), updated `test_activities.py` for the new recording call sites. Live E2E: push a real PR, confirm 5 rows land in `pr_review_state_versions` in the right order; separately force a contract rejection (e.g. temporarily feed a review activity output identical to its diff) and confirm `status='contract_rejected:...'` is recorded and the section reads "check skipped" in the posted comment.
