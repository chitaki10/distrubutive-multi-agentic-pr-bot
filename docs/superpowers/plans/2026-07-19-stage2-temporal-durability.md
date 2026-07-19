# Stage 2: Temporal Durability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap Stage 1's synchronous review pipeline in a Temporal workflow + activities, running in a separate worker process. The webhook fast-acks (starts the workflow, returns immediately) instead of blocking on the LLM call. Add a `pr_reviews` Postgres table tracking status transitions. Demo: kill the worker mid-review, restart it, the review completes and posts — proving durability.

**Architecture:** Two local processes — `prbot.app:app` (FastAPI, now only verifies the webhook and starts a Temporal workflow) and `prbot.worker` (new, hosts the workflow + 4 activities). See `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`'s "Stage 2 implementation notes" section for the full rationale.

**Tech Stack:** Adds `temporalio` (workflow/activity/client SDK) and `asyncpg` (Postgres driver) to the existing Stage 1 stack. Temporal CLI (`temporal server start-dev`) for the local dev server — frontend on `localhost:7233`, Web UI on `localhost:8233`. Postgres via the existing `docker-compose.yml` (`postgres:16`, db `prbot`/user `prbot`/password `prbot`).

## Global Constraints

- Workflow ID: `f"{owner}/{repo}#{pr_number}@{head_sha}"` — one workflow per push, not per PR (Stage 4 introduces supersede handling for a collision-free scheme).
- Activities never receive secret material (private key contents) as arguments — each activity reads `Settings` (via `get_settings()`) itself at execution time.
- Activities reference each other across files by **string name** in `workflow.execute_activity(...)` calls — `workflows.py` must NOT import `prbot.activities` (which pulls in `asyncpg`/`httpx`/`jwt`/`cryptography`) to avoid dragging those into Temporal's workflow sandbox. Only `prbot.activity_types` (plain dataclasses, no heavy deps) is shared between `workflows.py` and `activities.py`.
- `review.py`, `test_review.py`, and all Stage 1 tests are left untouched and must keep passing.
- Postgres must be reachable at `postgresql://prbot:prbot@localhost:5432/prbot` (via `docker-compose up -d`) before running Task 2's tests.
- If any `temporalio` API call in this plan (exact keyword args, exception class name, etc.) doesn't match the installed package, introspect it directly — `.venv\Scripts\python -c "from temporalio import workflow; help(workflow.execute_activity)"` (or `temporalio.client.WorkflowFailureError`, `temporalio.exceptions.ActivityError`) — and adapt while preserving the same behavior and test intent. Note any discrepancy found in your report.

---

### Task 1: Dependencies + Settings additions

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/prbot/config.py`
- Modify: `.env.example`
- Modify: `tests/test_config.py` (append one test)

**Interfaces:**
- Produces: `prbot.config.get_settings() -> Settings` (`@lru_cache`), `Settings.postgres_dsn: str = "postgresql://prbot:prbot@localhost:5432/prbot"`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py` (existing `test_settings_loads_from_env` stays as-is above this):

```python
def test_get_settings_is_cached_and_includes_postgres_dsn(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "/tmp/key.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "s3cr3t")

    from prbot.config import get_settings

    get_settings.cache_clear()
    first = get_settings()
    second = get_settings()

    assert first is second
    assert first.postgres_dsn == "postgresql://prbot:prbot@localhost:5432/prbot"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests/test_config.py -v`
Expected: FAIL with `ImportError: cannot import name 'get_settings'`

- [ ] **Step 3: Update `pyproject.toml` dependencies**

In the `[project] dependencies` list, add two entries (keep everything else unchanged):
```toml
    "temporalio>=1.7,<2",
    "asyncpg>=0.29,<1",
```

- [ ] **Step 4: Update `src/prbot/config.py`**

Full new content:
```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    github_app_id: str
    github_private_key_path: str
    github_webhook_secret: str
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "qwen2.5-coder:3b"
    postgres_dsn: str = "postgresql://prbot:prbot@localhost:5432/prbot"


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 5: Update `.env.example`**

Add one line (keep existing lines):
```
POSTGRES_DSN=postgresql://prbot:prbot@localhost:5432/prbot
```

- [ ] **Step 6: Install updated dependencies**

Run: `.venv\Scripts\pip install -e ".[dev]"`

- [ ] **Step 7: Run test to verify it passes**

Run: `.venv\Scripts\pytest tests/test_config.py -v`
Expected: PASS (2 tests)

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml src/prbot/config.py .env.example tests/test_config.py
git commit -m "feat: add temporalio/asyncpg deps and postgres_dsn setting"
```

---

### Task 2: Postgres access layer (db.py)

**Files:**
- Create: `src/prbot/db.py`
- Test: `tests/test_db.py`

**Interfaces:**
- Consumes: `prbot.config.get_settings`
- Produces: `prbot.db.init_db() -> None` (async), `prbot.db.set_review_status(repo: str, pr_number: int, head_sha: str, status: str) -> None` (async)

Before starting: run `docker-compose up -d` and confirm Postgres is reachable (`docker-compose ps` shows `postgres` running).

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_db.py
import pytest

from prbot import db


@pytest.fixture(autouse=True)
async def reset_pool():
    db._pool = None
    yield
    if db._pool is not None:
        await db._pool.close()
        db._pool = None


async def test_init_db_creates_table():
    await db.init_db()
    pool = await db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT to_regclass('public.pr_reviews') AS exists")
    assert row["exists"] == "pr_reviews"


async def test_set_review_status_inserts_and_updates():
    await db.init_db()
    await db.set_review_status("test-owner/test-repo", 9001, "test-sha-1", "running")

    pool = await db._get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM pr_reviews WHERE repo = $1 AND pr_number = $2 AND head_sha = $3",
            "test-owner/test-repo", 9001, "test-sha-1",
        )
    assert row["status"] == "running"

    await db.set_review_status("test-owner/test-repo", 9001, "test-sha-1", "complete")

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT status FROM pr_reviews WHERE repo = $1 AND pr_number = $2 AND head_sha = $3",
            "test-owner/test-repo", 9001, "test-sha-1",
        )
    assert row["status"] == "complete"
```

Note the `reset_pool` fixture: `asyncpg` pools are bound to the event loop they were created in, and `pytest-asyncio` gives each test function a fresh loop by default. Without resetting `db._pool` between tests, the second test would fail with "attached to a different loop". This fixture resets the module-level pool before and closes it after each test.

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.db'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/prbot/db.py
import asyncpg

from prbot.config import get_settings

_pool: asyncpg.Pool | None = None


async def _get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(get_settings().postgres_dsn)
    return _pool


async def init_db() -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            CREATE TABLE IF NOT EXISTS pr_reviews (
              id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
              repo text NOT NULL,
              pr_number int NOT NULL,
              head_sha text NOT NULL,
              status text NOT NULL,
              created_at timestamptz NOT NULL DEFAULT now(),
              updated_at timestamptz NOT NULL DEFAULT now(),
              UNIQUE(repo, pr_number, head_sha)
            )
            """
        )


async def set_review_status(repo: str, pr_number: int, head_sha: str, status: str) -> None:
    pool = await _get_pool()
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO pr_reviews (repo, pr_number, head_sha, status)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (repo, pr_number, head_sha)
            DO UPDATE SET status = EXCLUDED.status, updated_at = now()
            """,
            repo, pr_number, head_sha, status,
        )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_db.py -v`
Expected: PASS (2 tests). If connection fails, confirm `docker-compose ps` shows Postgres running and reachable on port 5432.

- [ ] **Step 5: Commit**

```bash
git add src/prbot/db.py tests/test_db.py
git commit -m "feat: add Postgres access layer for review status tracking"
```

---

### Task 3: Activity types + activities

**Files:**
- Create: `src/prbot/activity_types.py`
- Create: `src/prbot/activities.py`
- Test: `tests/test_activities.py`

**Interfaces:**
- Consumes: `prbot.github_client.generate_app_jwt/get_installation_token/fetch_pr_diff/post_pr_comment`, `prbot.llm_client.review_diff`, `prbot.db.set_review_status`, `prbot.config.get_settings`
- Produces: dataclasses `FetchDiffInput`, `ReviewInput`, `PostCommentInput`, `SetStatusInput` (in `activity_types.py`); activities `fetch_diff_activity(FetchDiffInput) -> str`, `review_activity(ReviewInput) -> str`, `post_comment_activity(PostCommentInput) -> int`, `set_review_status_activity(SetStatusInput) -> None` (in `activities.py`, all async, all `@activity.defn`)

- [ ] **Step 1: Write `src/prbot/activity_types.py`**

```python
from dataclasses import dataclass


@dataclass
class FetchDiffInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int


@dataclass
class ReviewInput:
    diff_text: str


@dataclass
class PostCommentInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int
    body: str


@dataclass
class SetStatusInput:
    repo: str
    pr_number: int
    head_sha: str
    status: str
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_activities.py
from prbot import activities


async def test_fetch_diff_activity_calls_apis_in_order(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")

    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    calls = []

    def fake_generate_app_jwt(app_id, key):
        calls.append(("jwt", app_id))
        return "fake.jwt"

    async def fake_get_installation_token(app_jwt, installation_id):
        calls.append(("token", app_jwt, installation_id))
        return "ghs_token"

    async def fake_fetch_pr_diff(token, owner, repo, pr_number):
        calls.append(("diff", token, owner, repo, pr_number))
        return "diff-content"

    monkeypatch.setattr(activities.github_client, "generate_app_jwt", fake_generate_app_jwt)
    monkeypatch.setattr(activities.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(activities.github_client, "fetch_pr_diff", fake_fetch_pr_diff)

    result = await activities.fetch_diff_activity(
        activities.FetchDiffInput(installation_id="55", owner="chitaki10", repo="demo", pr_number=7)
    )

    assert result == "diff-content"
    assert calls == [
        ("jwt", "1"),
        ("token", "fake.jwt", "55"),
        ("diff", "ghs_token", "chitaki10", "demo", 7),
    ]


async def test_review_activity_calls_review_diff(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    async def fake_review_diff(diff_text, base_url, model):
        assert diff_text == "diff-content"
        assert base_url == "http://localhost:11434"
        assert model == "qwen2.5-coder:3b"
        return "review-body"

    monkeypatch.setattr(activities.llm_client, "review_diff", fake_review_diff)

    result = await activities.review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result == "review-body"


async def test_post_comment_activity_calls_apis_in_order(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")

    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    calls = []

    def fake_generate_app_jwt(app_id, key):
        calls.append(("jwt", app_id))
        return "fake.jwt"

    async def fake_get_installation_token(app_jwt, installation_id):
        calls.append(("token", app_jwt, installation_id))
        return "ghs_token"

    async def fake_post_pr_comment(token, owner, repo, pr_number, body):
        calls.append(("comment", token, owner, repo, pr_number, body))
        return 99

    monkeypatch.setattr(activities.github_client, "generate_app_jwt", fake_generate_app_jwt)
    monkeypatch.setattr(activities.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(activities.github_client, "post_pr_comment", fake_post_pr_comment)

    result = await activities.post_comment_activity(
        activities.PostCommentInput(installation_id="55", owner="chitaki10", repo="demo", pr_number=7, body="nice PR")
    )

    assert result == 99
    assert calls == [
        ("jwt", "1"),
        ("token", "fake.jwt", "55"),
        ("comment", "ghs_token", "chitaki10", "demo", 7, "nice PR"),
    ]


async def test_set_review_status_activity_calls_db(monkeypatch):
    calls = []

    async def fake_set_review_status(repo, pr_number, head_sha, status):
        calls.append((repo, pr_number, head_sha, status))

    monkeypatch.setattr(activities.db, "set_review_status", fake_set_review_status)

    await activities.set_review_status_activity(
        activities.SetStatusInput(repo="demo", pr_number=7, head_sha="abc123", status="running")
    )

    assert calls == [("demo", 7, "abc123", "running")]
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.activities'`

- [ ] **Step 4: Write `src/prbot/activities.py`**

```python
from pathlib import Path

from temporalio import activity

from prbot import db, github_client, llm_client
from prbot.activity_types import FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput
from prbot.config import get_settings


def _generate_jwt() -> str:
    settings = get_settings()
    private_key = Path(settings.github_private_key_path).read_text()
    return github_client.generate_app_jwt(settings.github_app_id, private_key)


@activity.defn
async def fetch_diff_activity(input: FetchDiffInput) -> str:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    return await github_client.fetch_pr_diff(token, input.owner, input.repo, input.pr_number)


@activity.defn
async def review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff(input.diff_text, settings.ollama_base_url, settings.ollama_model)


@activity.defn
async def post_comment_activity(input: PostCommentInput) -> int:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    return await github_client.post_pr_comment(token, input.owner, input.repo, input.pr_number, input.body)


@activity.defn
async def set_review_status_activity(input: SetStatusInput) -> None:
    await db.set_review_status(input.repo, input.pr_number, input.head_sha, input.status)
```

Note: `activities.py` re-exports `FetchDiffInput` etc. by importing them at module level (`from prbot.activity_types import ...`) — the tests reference them as `activities.FetchDiffInput` etc., which works because of this import.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: PASS (4 tests)

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass (Stage 1's 16 plus this stage's additions)

- [ ] **Step 7: Commit**

```bash
git add src/prbot/activity_types.py src/prbot/activities.py tests/test_activities.py
git commit -m "feat: add Temporal activities wrapping the review pipeline"
```

---

### Task 4: PRReviewWorkflow

**Files:**
- Create: `src/prbot/workflows.py`
- Test: `tests/test_workflows.py`

**Interfaces:**
- Consumes: `prbot.activity_types.FetchDiffInput/ReviewInput/PostCommentInput/SetStatusInput` (only these — NOT `prbot.activities`, per Global Constraints)
- Produces: `prbot.workflows.ReviewEvent` dataclass (`owner: str, repo: str, pr_number: int, head_sha: str, installation_id: str`), `prbot.workflows.PRReviewWorkflow` (`@workflow.defn`, `run(self, event: ReviewEvent) -> int`)

- [ ] **Step 1: Write `src/prbot/workflows.py`**

```python
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from prbot.activity_types import FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput


@dataclass
class ReviewEvent:
    owner: str
    repo: str
    pr_number: int
    head_sha: str
    installation_id: str


@workflow.defn
class PRReviewWorkflow:
    @workflow.run
    async def run(self, event: ReviewEvent) -> int:
        retry_policy = RetryPolicy(maximum_attempts=3)

        async def set_status(status: str) -> None:
            await workflow.execute_activity(
                "set_review_status_activity",
                SetStatusInput(repo=event.repo, pr_number=event.pr_number, head_sha=event.head_sha, status=status),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )

        await set_status("running")

        try:
            diff = await workflow.execute_activity(
                "fetch_diff_activity",
                FetchDiffInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )

            review_body = await workflow.execute_activity(
                "review_activity",
                ReviewInput(diff_text=diff),
                start_to_close_timeout=timedelta(seconds=180),
                retry_policy=retry_policy,
            )

            comment_id = await workflow.execute_activity(
                "post_comment_activity",
                PostCommentInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                    body=review_body,
                ),
                start_to_close_timeout=timedelta(seconds=30),
                retry_policy=retry_policy,
            )
        except ActivityError:
            await set_status("failed")
            raise

        await set_status("complete")
        return comment_id
```

- [ ] **Step 2: Write the failing tests**

```python
# tests/test_workflows.py
import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from prbot.activity_types import FetchDiffInput, PostCommentInput, ReviewInput, SetStatusInput
from prbot.workflows import PRReviewWorkflow, ReviewEvent


async def test_workflow_calls_activities_in_order_and_completes():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="review_activity")
        async def fake_review(input: ReviewInput) -> str:
            calls.append(("review", input.diff_text))
            return "review-body"

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        async with Worker(
            env.client,
            task_queue="test-queue-1",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, fake_review, fake_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-1",
                task_queue="test-queue-1",
            )

        assert result == 42
        assert calls == [
            ("set_status", "running"),
            ("fetch_diff",),
            ("review", "diff-text"),
            ("post_comment", "review-body"),
            ("set_status", "complete"),
        ]


async def test_workflow_marks_failed_when_activity_exhausts_retries():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(input.status)

        @activity.defn(name="fetch_diff_activity")
        async def failing_fetch_diff(input: FetchDiffInput) -> str:
            raise RuntimeError("boom")

        @activity.defn(name="review_activity")
        async def unused_review(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-2",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, failing_fetch_diff, unused_review, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-2",
                    task_queue="test-queue-2",
                )

        assert calls == ["running", "failed"]
```

If `WorkflowFailureError` isn't importable from `temporalio.client`, check `.venv\Scripts\python -c "import temporalio.client as c; print([n for n in dir(c) if 'Error' in n or 'Fail' in n])"` and use whatever the installed version actually calls it — same behavior (workflow-level failure raised to the caller of `execute_workflow`) is what matters, not the exact class name.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.workflows'`

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: PASS (2 tests). These tests use Temporal's time-skipping test server (started automatically by `WorkflowEnvironment.start_time_skipping()`) — no real Temporal server, Postgres, or Ollama needed for this file.

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/prbot/workflows.py tests/test_workflows.py
git commit -m "feat: add PRReviewWorkflow"
```

---

### Task 5: Worker entrypoint

**Files:**
- Create: `src/prbot/worker.py`
- Test: `tests/test_worker.py`

**Interfaces:**
- Consumes: `prbot.db.init_db`, `prbot.workflows.PRReviewWorkflow`, `prbot.activities.fetch_diff_activity/review_activity/post_comment_activity/set_review_status_activity`
- Produces: `prbot.worker.TASK_QUEUE` (str constant, must equal `"pr-review-task-queue"` — Task 6's webhook uses the same value), `prbot.worker.main() -> None` (async)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_worker.py
from prbot import worker


async def test_main_wires_db_client_and_worker_together(monkeypatch):
    calls = []

    async def fake_init_db():
        calls.append("init_db")

    class FakeClient:
        pass

    async def fake_connect(target):
        calls.append(("connect", target))
        return FakeClient()

    class FakeWorker:
        def __init__(self, client, *, task_queue, workflows, activities):
            calls.append(("worker_init", task_queue, workflows, activities))
            self.client = client

        async def run(self):
            calls.append("worker_run")

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

    monkeypatch.setattr(worker, "init_db", fake_init_db)
    monkeypatch.setattr(worker.Client, "connect", staticmethod(fake_connect))
    monkeypatch.setattr(worker, "Worker", FakeWorker)

    await worker.main()

    assert calls[0] == "init_db"
    assert calls[1] == ("connect", "localhost:7233")
    assert calls[2][0] == "worker_init"
    assert calls[2][1] == worker.TASK_QUEUE
    assert calls[3] == "worker_run"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests/test_worker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'prbot.worker'`

- [ ] **Step 3: Write `src/prbot/worker.py`**

```python
import asyncio

from temporalio.client import Client
from temporalio.worker import Worker

from prbot.activities import (
    fetch_diff_activity,
    post_comment_activity,
    review_activity,
    set_review_status_activity,
)
from prbot.db import init_db
from prbot.workflows import PRReviewWorkflow

TASK_QUEUE = "pr-review-task-queue"


async def main() -> None:
    await init_db()
    client = await Client.connect("localhost:7233")
    worker = Worker(
        client,
        task_queue=TASK_QUEUE,
        workflows=[PRReviewWorkflow],
        activities=[
            fetch_diff_activity,
            review_activity,
            post_comment_activity,
            set_review_status_activity,
        ],
    )
    await worker.run()


if __name__ == "__main__":
    asyncio.run(main())
```

If `Worker(...).run()` isn't an async context-free call in the installed version (e.g. it requires `async with Worker(...) as w: await w.run_forever()` or similar), adapt `main()` to match — check `.venv\Scripts\python -c "from temporalio.worker import Worker; help(Worker)"` — and update the test's `FakeWorker` fixture to match whatever shape you land on, keeping the same wiring-order assertions (init_db → connect → construct worker with the right task queue/workflows/activities → run).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv\Scripts\pytest tests/test_worker.py -v`
Expected: PASS

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/prbot/worker.py tests/test_worker.py
git commit -m "feat: add Temporal worker entrypoint"
```

---

### Task 6: Webhook fast-ack via Temporal

**Files:**
- Modify: `src/prbot/app.py` (full rewrite of the file's content, shown below)
- Modify: `tests/test_app.py` (full rewrite of the file's content, shown below)

**Interfaces:**
- Consumes: `prbot.config.get_settings`, `prbot.events.verify_signature/parse_pull_request_event`, `prbot.workflows.PRReviewWorkflow/ReviewEvent`
- Produces: `prbot.app.get_temporal_client() -> Client` (async), `prbot.app.TASK_QUEUE` (must equal `prbot.worker.TASK_QUEUE`'s value, `"pr-review-task-queue"`)
- Removes: `prbot.app.get_settings` as a locally-defined function (now imported from `prbot.config`) — `generate_app_jwt` and `run_stage1_review` are no longer imported or called by `app.py`; the webhook process never touches the GitHub App private key directly, only the worker process does (via Task 3's activities)

- [ ] **Step 1: Write the failing tests**

Full replacement content for `tests/test_app.py`:

```python
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

from prbot import app as app_module


def _sign(body: bytes, secret: str) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class FakeHandle:
    def __init__(self, id: str):
        self.id = id


class FakeTemporalClient:
    def __init__(self):
        self.start_workflow_calls = []

    async def start_workflow(self, workflow_run, event, *, id, task_queue):
        self.start_workflow_calls.append((workflow_run, event, id, task_queue))
        return FakeHandle(id)


@pytest.fixture
def client(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")

    monkeypatch.setenv("GITHUB_APP_ID", "123")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "testsecret")

    app_module.get_settings.cache_clear()

    fake_client = FakeTemporalClient()

    async def fake_get_temporal_client():
        return fake_client

    monkeypatch.setattr(app_module, "get_temporal_client", fake_get_temporal_client)

    test_client = TestClient(app_module.app)
    test_client.fake_temporal = fake_client
    return test_client


def test_webhook_starts_workflow_on_valid_signature(client):
    payload = {
        "action": "opened",
        "pull_request": {"number": 7, "head": {"sha": "abc123"}},
        "repository": {"name": "demo", "owner": {"login": "chitaki10"}},
        "installation": {"id": 55},
    }
    body = json.dumps(payload).encode()
    signature = _sign(body, "testsecret")

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "started", "workflow_id": "chitaki10/demo#7@abc123"}
    assert len(client.fake_temporal.start_workflow_calls) == 1


def test_webhook_rejects_invalid_signature(client):
    body = json.dumps({"action": "opened"}).encode()

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": "sha256=deadbeef", "content-type": "application/json"},
    )

    assert response.status_code == 401


def test_webhook_ignores_non_pr_events(client):
    body = json.dumps({"action": "closed"}).encode()
    signature = _sign(body, "testsecret")

    response = client.post(
        "/webhook",
        content=body,
        headers={"x-hub-signature-256": signature, "content-type": "application/json"},
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ignored"}
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_app.py -v`
Expected: FAIL — `AttributeError` on `app_module.get_temporal_client` (doesn't exist yet) or assertion mismatch against the old response shape

- [ ] **Step 3: Write full replacement content for `src/prbot/app.py`**

```python
from fastapi import FastAPI, HTTPException, Request
from temporalio.client import Client

from prbot.config import get_settings
from prbot.events import parse_pull_request_event, verify_signature
from prbot.workflows import PRReviewWorkflow, ReviewEvent

TASK_QUEUE = "pr-review-task-queue"

app = FastAPI()

_temporal_client: Client | None = None


async def get_temporal_client() -> Client:
    global _temporal_client
    if _temporal_client is None:
        _temporal_client = await Client.connect("localhost:7233")
    return _temporal_client


@app.post("/webhook")
async def handle_webhook(request: Request):
    settings = get_settings()
    body = await request.body()
    signature = request.headers.get("x-hub-signature-256")

    if not verify_signature(body, signature, settings.github_webhook_secret):
        raise HTTPException(status_code=401, detail="invalid signature")

    payload = await request.json()
    event = parse_pull_request_event(payload)
    if event is None:
        return {"status": "ignored"}

    client = await get_temporal_client()
    review_event = ReviewEvent(
        owner=event.owner,
        repo=event.repo,
        pr_number=event.pr_number,
        head_sha=event.head_sha,
        installation_id=event.installation_id,
    )
    workflow_id = f"{event.owner}/{event.repo}#{event.pr_number}@{event.head_sha}"
    handle = await client.start_workflow(
        PRReviewWorkflow.run,
        review_event,
        id=workflow_id,
        task_queue=TASK_QUEUE,
    )
    return {"status": "started", "workflow_id": handle.id}
```

Confirm `TASK_QUEUE` here matches `prbot.worker.TASK_QUEUE` from Task 5 exactly (`"pr-review-task-queue"`) — a real workflow start would silently never be picked up by the worker if these two constants drift apart, since Temporal only routes work between clients and workers sharing the same task queue name.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_app.py -v`
Expected: PASS (3 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass (Stage 1's tests plus this stage's — `review.py`/`test_review.py` untouched and still passing)

- [ ] **Step 6: Commit**

```bash
git add src/prbot/app.py tests/test_app.py
git commit -m "feat: webhook fast-acks via Temporal workflow start"
```

---

### Task 7: Manual E2E durability verification

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage2-verification.md`

No automated test — this is the Stage 2 demo checkpoint: prove a review survives killing and restarting the worker mid-run.

- [ ] **Step 1: Start infrastructure**

```bash
docker-compose up -d
temporal server start-dev
```
Confirm Temporal's Web UI is reachable at `http://localhost:8233`.

- [ ] **Step 2: Start the worker**

```bash
.venv\Scripts\python -m prbot.worker
```

- [ ] **Step 3: Start the webhook server**

```bash
.venv\Scripts\uvicorn prbot.app:app --port 8000
```
(smee tunnel from Stage 1 should already be forwarding to this port — restart it if it's not still running: `npx smee-client -u <your smee URL> -t http://localhost:8000/webhook`.)

- [ ] **Step 4: Trigger a review and kill the worker mid-run**

Open a new PR (or push a commit) on the demo repo. As soon as you see the worker's log show the `review_activity` (Ollama call) has started, kill the worker process (Ctrl+C or close its terminal) before it finishes.

Confirm: the webhook responded `200` immediately (fast-ack) regardless of the worker being alive — check via the smee/uvicorn logs, the response doesn't wait on the worker at all.

- [ ] **Step 5: Restart the worker and confirm resumption**

```bash
.venv\Scripts\python -m prbot.worker
```
Confirm the review completes and the PR comment posts. Check Temporal's Web UI (`http://localhost:8233`) for the workflow — its event history should show the activities from before the kill, not a restart from the top (i.e. `fetch_diff_activity` doesn't re-run if it already completed before the kill).

- [ ] **Step 6: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage2-verification.md` — note the PR used, what point in the pipeline the worker was killed at, and what Temporal's Web UI showed for that workflow's history (which activities had already completed before the kill vs. which ran after restart).

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/plans/2026-07-19-stage2-verification.md
git commit -m "docs: record Stage 2 durability E2E verification"
```

Note: pushing to `origin` is done by the user, not automatically.

---

## After this plan

Stage 3 (multi-agent LangGraph + aggregator) gets its own implementation plan when reached, per `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`.
