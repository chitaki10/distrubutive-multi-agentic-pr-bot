# Multi-Agent GitHub PR Review Bot

A self-hosted GitHub PR review bot: open or update a pull request, and the bot fetches the diff, sends it to a locally-running open-weight LLM, and posts the review back as a PR comment — built to demonstrate real distributed-systems patterns (durable workflow orchestration, versioned state, staleness handling, circuit breaking, saga compensation) around a multi-agent LLM pipeline. Fully open source, no hosted model APIs required.

## Status

Built stage by stage; each stage is demoable on its own.

- ✅ **Stage 0 — Scaffold**: repo layout, Postgres via docker-compose.
- ✅ **Stage 1 — Vertical slice**: GitHub App + webhook + one hardcoded Ollama review call → real PR comment posted end-to-end.
- ✅ **Stage 2 — Temporal durability**: webhook fast-acks by starting a Temporal workflow instead of blocking; a separate worker process runs the review pipeline as retryable activities, with status tracked in Postgres (`pending → running → complete/failed`). Kill the worker mid-review and restart it — the review resumes and still posts.
   **Stage 3 — Multi-agent review**: LangGraph supervisor fans out to Security / Style-Lint / Test-Coverage agents, an aggregator merges results into one comment.
- ⏳ **Stage 4 — Staleness handling**: a force-push mid-review discards the stale run instead of posting an outdated comment.
- ⏳ **Stage 5 — Circuit breaker**: a repeatedly-failing agent gets skipped rather than hanging the whole review.
- ⏳ **Stage 6 — Saga/compensation**: a broken partial review gets automatically edited/deleted rather than left visible.
- ⏳ **Stage 7 — Polish**: demo script, architecture diagram.

Full design: [`docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`](docs/superpowers/specs/2026-07-19-pr-review-bot-design.md).

## Architecture

```
GitHub PR (opened/synchronize)
   │  webhook POST (HMAC-signed, GitHub App)
   ▼
FastAPI webhook-service — verifies signature, starts a Temporal workflow, acks immediately
   ▼
Temporal Server (local dev)
   ▼
Temporal Worker — hosts the PR review workflow + activities
   ├─ fetch diff (GitHub App installation token)
   ├─ send diff to local Ollama model, get a review
   ├─ post the review as a PR comment
   └─ track status transitions in Postgres
```

## Stack

- **FastAPI** — webhook receiver
- **Temporal** (`temporalio`) — durable workflow orchestration
- **Postgres** (`asyncpg`) — versioned review state
- **Ollama** — local, open-weight model serving (`qwen2.5-coder:3b`, swappable to vLLM later without touching agent code)
- **GitHub App** — scoped installation auth, not a personal access token

## Running locally

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"

docker-compose up -d          # Postgres
temporal server start-dev     # Temporal dev server + Web UI at localhost:8233

.venv/Scripts/python -m prbot.worker      # Temporal worker
.venv/Scripts/uvicorn prbot.app:app --port 8000   # webhook server
```

You'll also need a GitHub App (webhook secret + private key) and a local tunnel (e.g. `smee-client`) pointed at `/webhook` for GitHub to reach your machine, plus `ollama pull qwen2.5-coder:3b`. Copy `.env.example` to `.env` and fill in the values.

## Testing

```bash
.venv/Scripts/pytest -v
```
