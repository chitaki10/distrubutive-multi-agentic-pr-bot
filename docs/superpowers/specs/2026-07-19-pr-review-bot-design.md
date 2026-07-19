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
