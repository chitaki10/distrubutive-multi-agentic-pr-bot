# Stage 6: Saga / Compensation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** If something fails *after* a review comment has already been posted, don't leave a stale/broken artifact on the PR — compensate by deleting the comment, then mark the run failed.

**Architecture:** Since nothing naturally fails after `post_comment_activity` in the current pipeline, a demo-only failure-injection activity (`check_demo_failure_injection_activity`, gated by an env var) creates a controllable, reproducible trigger for the compensation path. Everything downstream of the trigger is real: a real `delete_comment_activity` calls the real GitHub API to remove the real comment that was just posted. See `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`'s "Stages 3-6 implementation notes" for rationale.

**Tech Stack:** No new dependencies. Extends `github_client.py`, `activity_types.py`, `activities.py`, `workflows.py`, `worker.py`.

## Global Constraints

- **Every task that adds a new activity referenced by string name in `workflows.py` MUST also update `worker.py`'s registration list in the SAME task** (same rule as Stage 4; Stage 3 is the one time this was missed).
- `check_demo_failure_injection_activity` takes no input and returns `bool` — `True` means "pretend something failed after posting, run compensation."
- The failure-injection check and its compensation happen *after* `post_comment_activity` succeeds, outside the original `try/except ActivityError` block (that block already handles `post_comment_activity` itself failing) — this is a second, explicit branch for the "succeeded, but something later means we should undo it" case.

---

### Task 1: `delete_pr_comment` + failure-injection + delete activities

**Files:**
- Modify: `src/prbot/github_client.py` (append)
- Modify: `src/prbot/activity_types.py` (append)
- Modify: `src/prbot/activities.py` (append)
- Modify: `tests/test_github_client_pr.py` (append)
- Modify: `tests/test_activities.py` (append)

**Interfaces:**
- Produces: `prbot.github_client.delete_pr_comment(token: str, owner: str, repo: str, comment_id: int, base_url: str = "https://api.github.com") -> None` (async); `prbot.activity_types.DeleteCommentInput` (`installation_id: str, owner: str, repo: str, comment_id: int`); `prbot.activities.check_demo_failure_injection_activity() -> bool` (async, `@activity.defn`, no args); `prbot.activities.delete_comment_activity(DeleteCommentInput) -> None` (async, `@activity.defn`)

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_github_client_pr.py`:

```python
@respx.mock
async def test_delete_pr_comment_sends_delete_request():
    route = respx.delete("https://api.github.com/repos/chitaki10/demo/issues/comments/42").mock(
        return_value=httpx.Response(204)
    )

    await delete_pr_comment("ghs_token", "chitaki10", "demo", 42)

    assert route.called
```

Add `delete_pr_comment` to the existing `from prbot.github_client import ...` line.

Append to `tests/test_activities.py`:

```python
async def test_check_demo_failure_injection_activity_returns_false_by_default(monkeypatch):
    monkeypatch.delenv("PRBOT_DEMO_FORCE_FAILURE_AFTER_POST", raising=False)

    result = await activities.check_demo_failure_injection_activity()

    assert result is False


async def test_check_demo_failure_injection_activity_returns_true_when_set(monkeypatch):
    monkeypatch.setenv("PRBOT_DEMO_FORCE_FAILURE_AFTER_POST", "true")

    result = await activities.check_demo_failure_injection_activity()

    assert result is True


async def test_delete_comment_activity_calls_apis_in_order(monkeypatch, tmp_path):
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

    async def fake_delete_pr_comment(token, owner, repo, comment_id):
        calls.append(("delete", token, owner, repo, comment_id))

    monkeypatch.setattr(activities.github_client, "generate_app_jwt", fake_generate_app_jwt)
    monkeypatch.setattr(activities.github_client, "get_installation_token", fake_get_installation_token)
    monkeypatch.setattr(activities.github_client, "delete_pr_comment", fake_delete_pr_comment)

    await activities.delete_comment_activity(
        activities.DeleteCommentInput(installation_id="55", owner="chitaki10", repo="demo", comment_id=42)
    )

    assert calls == [
        ("jwt", "1"),
        ("token", "fake.jwt", "55"),
        ("delete", "ghs_token", "chitaki10", "demo", 42),
    ]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_github_client_pr.py tests/test_activities.py -v`
Expected: FAIL — `ImportError`/`AttributeError` for the not-yet-defined names

- [ ] **Step 3: Append to `src/prbot/github_client.py`**

```python
async def delete_pr_comment(
    token: str, owner: str, repo: str, comment_id: int, base_url: str = "https://api.github.com"
) -> None:
    url = f"{base_url}/repos/{owner}/{repo}/issues/comments/{comment_id}"
    headers = {"Authorization": f"Bearer {token}"}
    async with httpx.AsyncClient() as client:
        response = await client.delete(url, headers=headers)
        response.raise_for_status()
```

- [ ] **Step 4: Append to `src/prbot/activity_types.py`**

```python
@dataclass
class DeleteCommentInput:
    installation_id: str
    owner: str
    repo: str
    comment_id: int
```

- [ ] **Step 5: Append to `src/prbot/activities.py`**

Add `import os` near the top if not already present, add `DeleteCommentInput` to the existing `from prbot.activity_types import ...` line, then append:

```python
@activity.defn
async def check_demo_failure_injection_activity() -> bool:
    return os.environ.get("PRBOT_DEMO_FORCE_FAILURE_AFTER_POST", "").lower() == "true"


@activity.defn
async def delete_comment_activity(input: DeleteCommentInput) -> None:
    app_jwt = _generate_jwt()
    token = await github_client.get_installation_token(app_jwt, input.installation_id)
    await github_client.delete_pr_comment(token, input.owner, input.repo, input.comment_id)
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
git commit -m "feat: add delete_pr_comment and saga compensation activities"
```

---

### Task 2: Wire compensation into the workflow (and register with the worker)

**Files:**
- Modify: `src/prbot/workflows.py` (full replacement)
- Modify: `src/prbot/worker.py` (add both new activities to imports and the registration list — do not skip this)
- Modify: `tests/test_workflows.py` (full replacement)

**Interfaces:**
- Consumes: `prbot.activity_types.DeleteCommentInput` (added to the existing import line)
- Produces: same `PRReviewWorkflow.run(self, event: ReviewEvent) -> int` signature; raises (workflow fails) if the demo failure-injection triggers, after compensating

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
    DeleteCommentInput,
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


async def test_workflow_completes_normally_when_no_failure_injected():
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

        @activity.defn(name="check_demo_failure_injection_activity")
        async def fake_check_failure_injection() -> bool:
            calls.append(("check_failure_injection",))
            return False

        @activity.defn(name="delete_comment_activity")
        async def unused_delete_comment(input: DeleteCommentInput) -> None:
            raise AssertionError("should not be called when no failure is injected")

        async with Worker(
            env.client,
            task_queue="test-queue-6-1",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                *_agent_fakes(calls),
                fake_check_staleness,
                fake_post_comment,
                fake_check_failure_injection,
                unused_delete_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-6-1",
                task_queue="test-queue-6-1",
            )

        assert result == 42
        assert ("check_failure_injection",) in calls
        assert calls[-1] == ("set_status", "complete")


async def test_workflow_compensates_when_failure_injected():
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

        @activity.defn(name="check_demo_failure_injection_activity")
        async def fake_check_failure_injection() -> bool:
            calls.append(("check_failure_injection",))
            return True

        @activity.defn(name="delete_comment_activity")
        async def fake_delete_comment(input: DeleteCommentInput) -> None:
            calls.append(("delete_comment", input.comment_id))

        async with Worker(
            env.client,
            task_queue="test-queue-6-2",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                *_agent_fakes(calls),
                fake_check_staleness,
                fake_post_comment,
                fake_check_failure_injection,
                fake_delete_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-6-2",
                    task_queue="test-queue-6-2",
                )

        assert ("delete_comment", 42) in calls
        assert calls[-1] == ("set_status", "failed")


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
            task_queue="test-queue-6-3",
            workflows=[PRReviewWorkflow],
            activities=[fake_set_status, fake_fetch_diff, *_agent_fakes(calls), fake_check_staleness, unused_post_comment],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-6-3",
                task_queue="test-queue-6-3",
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
            task_queue="test-queue-6-4",
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
                    id="test-workflow-6-4",
                    task_queue="test-queue-6-4",
                )

        assert calls == ["running", "failed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: FAIL — the workflow doesn't call `"check_demo_failure_injection_activity"` yet

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
    DeleteCommentInput,
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

        should_fail = await workflow.execute_activity(
            "check_demo_failure_injection_activity",
            start_to_close_timeout=timedelta(seconds=10),
            retry_policy=retry_policy,
        )

        if should_fail:
            await workflow.execute_activity(
                "delete_comment_activity",
                DeleteCommentInput(
                    installation_id=event.installation_id,
                    owner=event.owner,
                    repo=event.repo,
                    comment_id=comment_id,
                ),
                start_to_close_timeout=timedelta(seconds=15),
                retry_policy=retry_policy,
            )
            await set_status("failed")
            raise RuntimeError("Demo failure injection triggered after posting comment")

        await set_status("complete")
        return comment_id
```

- [ ] **Step 4: Update `src/prbot/worker.py`**

Add `check_demo_failure_injection_activity` and `delete_comment_activity` to the existing `from prbot.activities import (...)` block and to the `activities=[...]` list passed to `Worker(...)`. Read the current file first — do not guess at its exact current shape.

- [ ] **Step 5: Update `tests/test_worker.py`**

Add both new activities to the expected `activities=[...]` assertion list, matching whatever order you used in `worker.py`.

- [ ] **Step 6: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_workflows.py tests/test_worker.py -v`
Expected: PASS (all tests)

- [ ] **Step 7: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 8: Commit**

```bash
git add src/prbot/workflows.py src/prbot/worker.py tests/test_workflows.py tests/test_worker.py
git commit -m "feat: saga compensation for post-post-comment failures"
```

---

### Task 3: Manual E2E verification — trigger and observe compensation

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage6-verification.md`

No automated test beyond Tasks 1-2 — this is the Stage 6 demo checkpoint.

- [ ] **Step 1: Restart the worker with the failure injection enabled**

```bash
set PRBOT_DEMO_FORCE_FAILURE_AFTER_POST=true
.venv\Scripts\python -m prbot.worker
```
(On PowerShell: `$env:PRBOT_DEMO_FORCE_FAILURE_AFTER_POST = "true"` before starting the worker in that same shell.)

- [ ] **Step 2: Trigger a review**

Open a PR (or push a commit).

- [ ] **Step 3: Confirm the outcome**

Watch the PR: a comment should appear briefly, then be deleted. Confirm via the GitHub API (list comments on the PR — should end up with none from this run) and confirm `pr_reviews.status` for this head_sha is `failed`, not `complete`.

- [ ] **Step 4: Restart the worker without the failure injection, confirm normal operation**

```bash
.venv\Scripts\python -m prbot.worker
```
(New shell/without the env var set, or explicitly unset it.) Push one more commit, confirm the comment posts and stays.

- [ ] **Step 5: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage6-verification.md` noting the PR used, confirmation the comment was posted then deleted (with timestamps/IDs if available), the final `pr_reviews` status, and confirmation normal operation resumed after disabling the injection.

- [ ] **Step 6: Commit**

```bash
git add docs/superpowers/plans/2026-07-19-stage6-verification.md
git commit -m "docs: record Stage 6 saga compensation E2E verification"
```

---

## After this plan

Stage 7 (polish) is handled directly (README, demo script) rather than through the full subagent-driven-development process, per the design spec's note that documentation/tooling work doesn't need the same ceremony as core logic changes.
