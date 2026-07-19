# Stage 4: Staleness Handling Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Right before posting, re-fetch the PR's current HEAD SHA from GitHub. If it no longer matches the SHA this workflow was run against (i.e. the PR was force-pushed while this review was in flight), abort without posting and mark the run `stale` instead — the newer push's own independent workflow (Stage 2's per-push workflow ID already guarantees this) handles posting the current review.

**Architecture:** One new activity (`check_staleness_activity`) inserted into the workflow between `aggregate_activity` and `post_comment_activity`. No workflow-ID scheme change and no cancellation of in-flight workflows — see `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`'s "Stages 3-6 implementation notes" for the rationale.

**Tech Stack:** No new dependencies. Extends `github_client.py`, `activity_types.py`, `activities.py`, `workflows.py`, `worker.py`.

## Global Constraints

- `PRReviewWorkflow.run` returns `-1` (sentinel, not a real comment id) when the run is discarded as stale, instead of posting and returning a real comment id.
- **Every task that adds a new activity referenced by string name in `workflows.py` MUST also update `worker.py`'s registration list in the SAME task** — Stage 3 had a plan gap here (a task added a new activity but no task updated `worker.py`, caught late by an implementer's self-review). Do not repeat that gap.
- `status` stays a free-text column; adding `"stale"` as a value needs no schema migration.

---

### Task 1: `get_pr_head_sha` + `check_staleness_activity`

**Files:**
- Modify: `src/prbot/github_client.py` (append)
- Modify: `src/prbot/activity_types.py` (append)
- Modify: `src/prbot/activities.py` (append)
- Modify: `tests/test_github_client_pr.py` (append)
- Modify: `tests/test_activities.py` (append)

**Interfaces:**
- Produces: `prbot.github_client.get_pr_head_sha(token: str, owner: str, repo: str, pr_number: int, base_url: str = "https://api.github.com") -> str` (async); `prbot.activity_types.StalenessCheckInput` (`installation_id: str, owner: str, repo: str, pr_number: int, head_sha: str`); `prbot.activities.check_staleness_activity(StalenessCheckInput) -> bool` (async, `@activity.defn`, `True` means stale)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_client_pr.py`:

```python
@respx.mock
async def test_get_pr_head_sha_returns_current_head_sha():
    respx.get("https://api.github.com/repos/chitaki10/demo/pulls/7").mock(
        return_value=httpx.Response(200, json={"head": {"sha": "newsha123"}})
    )

    result = await get_pr_head_sha("ghs_token", "chitaki10", "demo", 7)

    assert result == "newsha123"
```

Add `get_pr_head_sha` to the existing `from prbot.github_client import ...` line.

Append to `tests/test_activities.py`:

```python
async def test_check_staleness_activity_returns_true_when_head_sha_differs(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    def fake_generate_app_jwt(app_id, key):
        return "fake.jwt"

    async def fake_get_installation_token(app_jwt, installation_id):
        return "ghs_token"

    async def fake_get_pr_head_sha(token, owner, repo, pr_number):
        return "new-sha"

    monkeypatch.setattr(activities.github_client, "generate_app_jwt", fake_generate_app_jwt)
    monkeypatch.setattr(activities.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(activities.github_client, "get_pr_head_sha", fake_get_pr_head_sha)

    result = await activities.check_staleness_activity(
        activities.StalenessCheckInput(
            installation_id="55", owner="chitaki10", repo="demo", pr_number=7, head_sha="old-sha"
        )
    )

    assert result is True


async def test_check_staleness_activity_returns_false_when_head_sha_matches(monkeypatch, tmp_path):
    key_file = tmp_path / "key.pem"
    key_file.write_text("dummy-key")
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", str(key_file))
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    def fake_generate_app_jwt(app_id, key):
        return "fake.jwt"

    async def fake_get_installation_token(app_jwt, installation_id):
        return "ghs_token"

    async def fake_get_pr_head_sha(token, owner, repo, pr_number):
        return "same-sha"

    monkeypatch.setattr(activities.github_client, "generate_app_jwt", fake_generate_app_jwt)
    monkeypatch.setattr(activities.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(activities.github_client, "get_pr_head_sha", fake_get_pr_head_sha)

    result = await activities.check_staleness_activity(
        activities.StalenessCheckInput(
            installation_id="55", owner="chitaki10", repo="demo", pr_number=7, head_sha="same-sha"
        )
    )

    assert result is False
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_github_client_pr.py tests/test_activities.py -v`
Expected: FAIL — `ImportError: cannot import name 'get_pr_head_sha'` / `AttributeError: ... no attribute 'check_staleness_activity'`

- [ ] **Step 3: Append to `src/prbot/github_client.py`**

```python
async def get_pr_head_sha(
    token: str, owner: str, repo: str, pr_number: int, base_url: str = "https://api.github.com"
) -> str:
    url = f"{base_url}/repos/{owner}/{repo}/pulls/{pr_number}"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
    }
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        return response.json()["head"]["sha"]
```

- [ ] **Step 4: Append to `src/prbot/activity_types.py`**

```python
@dataclass
class StalenessCheckInput:
    installation_id: str
    owner: str
    repo: str
    pr_number: int
    head_sha: str
```

- [ ] **Step 5: Append to `src/prbot/activities.py`**

Add `StalenessCheckInput` to the existing `from prbot.activity_types import ...` line, then append:

```python
@activity.defn
async def check_staleness_activity(input: StalenessCheckInput) -> bool:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    current_head_sha = await github_client.get_pr_head_sha(token, input.owner, input.repo, input.pr_number)
    return current_head_sha != input.head_sha
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_github_client_pr.py tests/test_activities.py -v`
Expected: PASS (all tests, including 4 new)

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/prbot/github_client.py src/prbot/activity_types.py src/prbot/activities.py tests/test_github_client_pr.py tests/test_activities.py
git commit -m "feat: add get_pr_head_sha and check_staleness_activity"
```

---

### Task 2: Wire staleness check into the workflow (and register it with the worker)

**Files:**
- Modify: `src/prbot/workflows.py` (full replacement)
- Modify: `src/prbot/worker.py` (add the new activity to imports and the registration list — do not skip this, see Global Constraints)
- Modify: `tests/test_workflows.py` (full replacement)

**Interfaces:**
- Consumes: `prbot.activity_types.StalenessCheckInput` (added to the existing import line)
- Produces: same `PRReviewWorkflow.run(self, event: ReviewEvent) -> int` signature; returns `-1` when the run is discarded as stale

- [ ] **Step 1: Write the failing tests**

Full replacement content for `tests/test_workflows.py`:

```python
import pytest
from temporalio import activity
from temporalio.client import WorkflowFailureError
from temporalio.testing import WorkflowEnvironment
from temporalio.worker import Worker

from prbot.activity_types import (
    AggregateInput,
    FetchDiffInput,
    PostCommentInput,
    ReviewInput,
    SetStatusInput,
    StalenessCheckInput,
)
from prbot.workflows import PRReviewWorkflow, ReviewEvent


def _agent_fakes(calls):
    @activity.defn(name="security_review_activity")
    async def fake_security(input: ReviewInput) -> str:
        calls.append(("security", input.diff_text))
        return "security-result"

    @activity.defn(name="style_review_activity")
    async def fake_style(input: ReviewInput) -> str:
        calls.append(("style", input.diff_text))
        return "style-result"

    @activity.defn(name="test_coverage_review_activity")
    async def fake_test_coverage(input: ReviewInput) -> str:
        calls.append(("test_coverage", input.diff_text))
        return "test-coverage-result"

    @activity.defn(name="aggregate_activity")
    async def fake_aggregate(input: AggregateInput) -> str:
        calls.append(("aggregate", input.security_result, input.style_result, input.test_coverage_result))
        return "aggregated-body"

    return [fake_security, fake_style, fake_test_coverage, fake_aggregate]


async def test_workflow_posts_when_not_stale():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="check_staleness_activity")
        async def fake_check_staleness(input: StalenessCheckInput) -> bool:
            calls.append(("check_staleness", input.head_sha))
            return False

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        async with Worker(
            env.client,
            task_queue="test-queue-4-1",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, fake_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-4-1",
                task_queue="test-queue-4-1",
            )

        assert result == 42
        call_types = [c[0] for c in calls]
        assert "check_staleness" in call_types
        assert call_types.index("check_staleness") > call_types.index("aggregate")
        assert call_types.index("post_comment") > call_types.index("check_staleness")
        assert calls[-1] == ("set_status", "complete")


async def test_workflow_discards_stale_run_without_posting():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

        @activity.defn(name="check_staleness_activity")
        async def fake_check_staleness(input: StalenessCheckInput) -> bool:
            calls.append(("check_staleness", input.head_sha))
            return True

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called when stale")

        async with Worker(
            env.client,
            task_queue="test-queue-4-2",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-4-2",
                task_queue="test-queue-4-2",
            )

        assert result == -1
        assert calls[-1] == ("set_status", "stale")


async def test_workflow_marks_failed_when_activity_exhausts_retries():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(input.status)

        @activity.defn(name="fetch_diff_activity")
        async def failing_fetch_diff(input: FetchDiffInput) -> str:
            raise RuntimeError("boom")

        @activity.defn(name="security_review_activity")
        async def unused_security(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="style_review_activity")
        async def unused_style(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="test_coverage_review_activity")
        async def unused_test_coverage(input: ReviewInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="aggregate_activity")
        async def unused_aggregate(input: AggregateInput) -> str:
            raise AssertionError("should not be called")

        @activity.defn(name="check_staleness_activity")
        async def unused_check_staleness(input: StalenessCheckInput) -> bool:
            raise AssertionError("should not be called")

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-4-3",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                failing_fetch_diff,
                unused_security,
                unused_style,
                unused_test_coverage,
                unused_aggregate,
                unused_check_staleness,
                unused_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-4-3",
                    task_queue="test-queue-4-3",
                )

        assert calls == ["running", "failed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: FAIL — the workflow doesn't call `"check_staleness_activity"` yet, so the stale-path test never sees it return `-1`, and the not-stale test's ordering assertions fail.

- [ ] **Step 3: Update `src/prbot/workflows.py`**

Full new content:

```python
import asyncio
from dataclasses import dataclass
from datetime import timedelta

from temporalio import workflow
from temporalio.common import RetryPolicy
from temporalio.exceptions import ActivityError

from prbot.activity_types import (
    AggregateInput,
    FetchDiffInput,
    PostCommentInput,
    ReviewInput,
    SetStatusInput,
    StalenessCheckInput,
)


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

            review_input = ReviewInput(diff_text=diff)
            security_result, style_result, test_coverage_result = await asyncio.gather(
                workflow.execute_activity(
                    "security_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
                workflow.execute_activity(
                    "style_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
                workflow.execute_activity(
                    "test_coverage_review_activity",
                    review_input,
                    start_to_close_timeout=timedelta(seconds=180),
                    retry_policy=retry_policy,
                ),
            )

            review_body = await workflow.execute_activity(
                "aggregate_activity",
                AggregateInput(
                    security_result=security_result,
                    style_result=style_result,
                    test_coverage_result=test_coverage_result,
                ),
                start_to_close_timeout=timedelta(seconds=10),
                retry_policy=retry_policy,
            )

            is_stale = await workflow.execute_activity(
                "check_staleness_activity",
                StalenessCheckInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    pr_number=event.pr_number,
                    head_sha=event.head_sha,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=retry_policy,
            )

            if is_stale:
                await set_status("stale")
                return -1

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

- [ ] **Step 4: Update `src/prbot/worker.py`**

Add `check_staleness_activity` to the existing `from prbot.activities import (...)` block (keep alphabetical or existing ordering convention) and to the `activities=[...]` list passed to `Worker(...)`. Read the current file first — do not guess at its exact current shape.

- [ ] **Step 5: Update `tests/test_worker.py`**

Add `check_staleness_activity` to the expected `activities=[...]` assertion list, in whatever position matches how you just ordered it in `worker.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_workflows.py tests/test_worker.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/prbot/workflows.py src/prbot/worker.py tests/test_workflows.py tests/test_worker.py
git commit -m "feat: check PR staleness before posting, discard stale runs"
```

---

### Task 3: Manual E2E verification — force-push mid-review

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage4-verification.md`

No automated test beyond Task 1-2 — this is the Stage 4 demo checkpoint, the specific scenario GitHub described as the best demo moment for this whole project.

- [ ] **Step 1: Restart the worker** (must reload the new workflow/activity code)

```bash
.venv\Scripts\python -m prbot.worker
```

- [ ] **Step 2: Open a PR, then force-push a new commit onto the same branch WHILE the first review is still running**

The window is short (the full pipeline — diff fetch + 3 concurrent agent calls + aggregate + staleness check + post — takes several seconds on the local model). Push the second commit as soon as you see the first webhook delivery land.

- [ ] **Step 3: Confirm the outcome**

- The FIRST push's workflow should either: post successfully if it finished before the second push's HEAD SHA changed, OR get marked `stale` in `pr_reviews` and post nothing, if the second push landed before its own staleness check ran.
- The SECOND push's workflow (a separate workflow, per Stage 2's per-push workflow ID) should complete normally and post the current review.
- Query `pr_reviews` for both head_shas to see their final `status` values directly:
  ```bash
  .venv\Scripts\python -c "
  import asyncio, asyncpg
  async def main():
      conn = await asyncpg.connect('postgresql://prbot:prbot@localhost:5434/prbot')
      rows = await conn.fetch(\"SELECT head_sha, status, created_at FROM pr_reviews ORDER BY created_at DESC LIMIT 5\")
      for r in rows: print(dict(r))
      await conn.close()
  asyncio.run(main())
  "
  ```

- [ ] **Step 4: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage4-verification.md` noting the PR used, both head_shas, and their final statuses (one `stale` or both `complete` if the timing didn't actually produce an overlapping stale run — note whichever actually happened, don't force a result).

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-07-19-stage4-verification.md
git commit -m "docs: record Stage 4 staleness E2E verification"
```

---

## After this plan

Stage 5 (circuit breaker) gets its own implementation plan when reached.
