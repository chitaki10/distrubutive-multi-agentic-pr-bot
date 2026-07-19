# Stage 2 Temporal Durability — E2E Verification

Date: 2026-07-19

## Setup

- Postgres via docker-compose, remapped to host port 5434 (two native Windows Postgres services already occupied 5432/5433 on this machine).
- Temporal dev server (`temporal server start-dev`): frontend `localhost:7233`, Web UI `localhost:8233`.
- Worker: `.venv/Scripts/python -m prbot.worker`.
- Webhook: `.venv/Scripts/uvicorn prbot.app:app --port 8000`, tunneled via smee.io.
- Repo used: `chitaki10/Job_Recommender_System_with_MCP`, PR #9.

## Environment gotchas hit during setup (not code defects)

1. **Postgres port squatting**: two native Windows PostgreSQL services (v16, v18) already listen on 5432/5433 — fixed by remapping docker-compose's container to 5434 (see commit `df557ab`).
2. **`localhost` resolving to the wrong listener for the webhook port too**: smee-client's requests to `http://localhost:8000/webhook` were landing on an unrelated `wslrelay.exe` process bound to `::1:8000` instead of our uvicorn (bound to `127.0.0.1:8000` only), producing silent 404s with nothing logged on our side. Fixed by pointing smee explicitly at `http://127.0.0.1:8000/webhook`.
3. Both gotchas are the same root cause: multiple unrelated services on this dev machine bind to `0.0.0.0`/`::`/dual-stack on ports this project also wants, and Windows' `localhost` resolution order isn't guaranteed to prefer the one you mean. Prefer explicit `127.0.0.1` over `localhost` for local dev tooling on this machine going forward.

## Fast-ack verification

Confirmed via `test-owner`/real webhook logs: `POST /webhook` returns `200` (`{"status": "started", "workflow_id": ...}`) immediately — the response never waits on the Temporal workflow's completion, regardless of worker availability.

## Durability verification (the actual demo)

PR #9's push (head_sha `5be2a14307d35f7f69793e088d56496b2b687ad8`) was used. An automated poller watched `pr_reviews.status` and killed the worker process (`taskkill /F`) the instant it observed `status = 'running'`, to remove human reaction-time as a variable.

`temporal workflow show` on `chitaki10/Job_Recommender_System_with_MCP#9@5be2a14307d35f7f69793e088d56496b2b687ad8` after the kill, before restart:

```
ID  Time                  Type
 1  11:23:09Z  WorkflowExecutionStarted
 5  11:23:09Z  ActivityTaskScheduled     (set_review_status_activity "running")
 7  11:23:09Z  ActivityTaskCompleted
11  11:23:09Z  ActivityTaskScheduled     (fetch_diff_activity -- scheduled, never started: worker died here)
```

`pr_reviews` row for this head_sha stayed frozen at `status='running'`, `created_at == updated_at`, confirming no further progress while the worker was down.

Worker restarted (`.venv/Scripts/python -m prbot.worker`). Full event history after restart:

```
11  11:23:09Z  ActivityTaskScheduled     (fetch_diff_activity)
12  11:24:18Z  ActivityTaskStarted       <- 69s gap: worker was down from ~11:23:09 to ~11:24:18
13  11:24:19Z  ActivityTaskCompleted
...(review_activity, post_comment_activity, final set_review_status_activity "complete")...
35  11:24:29Z  WorkflowExecutionCompleted
Result: 5015525133 (posted comment id)
```

The 69-second gap between event 11 (scheduled before the kill) and event 12 (started after restart) is the worker downtime window. `fetch_diff_activity` was **not** re-scheduled from event 5 or restarted from the top of the workflow — Temporal held the already-scheduled task durably and the new worker process picked it up exactly where the old one left off. `pr_reviews.status` for this row subsequently updated to `complete`, and the bot posted a real review comment on PR #9 (comment id `5015525133`, confirmed via the GitHub API).

## Conclusion

Stage 2 deliverable met: killing the worker mid-review and restarting it resumes the review from the point of failure rather than from scratch, and the review still completes and posts. Two environment-specific port conflicts were found and fixed along the way; no code defects.
