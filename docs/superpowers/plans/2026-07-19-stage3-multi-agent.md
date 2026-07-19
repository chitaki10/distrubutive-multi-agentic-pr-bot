# Stage 3: Multi-Agent Review + Aggregator Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace Stage 2's single generic review call with three parallel agent activities (Security / Style / Test Coverage), each with a distinct prompt, run concurrently inside the workflow, then merged by an aggregator activity into one markdown comment with three sections.

**Architecture:** No LangGraph dependency — Temporal's own `asyncio.gather` over three `execute_activity` calls provides the parallel fan-out; an `aggregate_activity` does simple concatenation under headers. See `docs/superpowers/specs/2026-07-19-pr-review-bot-design.md`'s "Stages 3-6 implementation notes" for full rationale.

**Tech Stack:** No new dependencies. Extends `llm_client.py`, `activity_types.py`, `activities.py`, `workflows.py` from Stage 2.

## Global Constraints

- `AggregateInput`'s three fields are typed `str | None` (not `str`) from this stage onward — Stage 5's circuit breaker will populate `None` for a skipped agent without needing to touch this dataclass again.
- `review_diff`/`review_activity` (Stage 1/2) stay behaviorally unchanged and their existing tests must keep passing untouched.
- The three agent activities reuse the existing `ReviewInput` dataclass (just `diff_text`) — no new per-agent input type needed, only the system prompt differs internally.
- Aggregator does plain concatenation under `### Security` / `### Style` / `### Test Coverage` headers — no synthesis LLM call.

---

### Task 1: `review_diff_with_prompt` in llm_client.py

**Files:**
- Modify: `src/prbot/llm_client.py`
- Modify: `tests/test_llm_client.py` (append, keep existing test)

**Interfaces:**
- Produces: `prbot.llm_client.review_diff_with_prompt(diff_text: str, base_url: str, model: str, system_prompt: str) -> str` (async)
- `prbot.llm_client.review_diff(diff_text, base_url, model) -> str` keeps its exact signature but now delegates to the new function internally

- [ ] **Step 1: Write the failing test**

Append to `tests/test_llm_client.py`:

```python
async def test_review_diff_with_prompt_sends_custom_system_prompt():
    route = respx.post("http://localhost:11434/v1/chat/completions").mock(
        return_value=httpx.Response(
            200,
            json={"choices": [{"message": {"content": "Custom agent review."}}]},
        )
    )

    result = await review_diff_with_prompt(
        "diff --git a/x.py b/x.py\n+print(1)",
        "http://localhost:11434",
        "qwen2.5-coder:3b",
        "You are a security-focused reviewer.",
    )

    assert result == "Custom agent review."
    sent_body = route.calls.last.request.content
    assert b"You are a security-focused reviewer." in sent_body
```

Add `review_diff_with_prompt` to the existing import line at the top of the file (`from prbot.llm_client import review_diff, review_diff_with_prompt`).

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv\Scripts\pytest tests/test_llm_client.py -v`
Expected: FAIL with `ImportError: cannot import name 'review_diff_with_prompt'`

- [ ] **Step 3: Update `src/prbot/llm_client.py`**

Full new content:

```python
import httpx

REVIEW_SYSTEM_PROMPT = (
    "You are a senior software engineer reviewing a GitHub pull request diff. "
    "Give a concise, actionable code review in markdown covering security, "
    "style, and missing test coverage. Keep it under 300 words."
)


async def review_diff_with_prompt(diff_text: str, base_url: str, model: str, system_prompt: str) -> str:
    url = f"{base_url}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": diff_text},
        ],
        "stream": False,
    }
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(url, json=payload)
        response.raise_for_status()
        return response.json()["choices"][0]["message"]["content"]


async def review_diff(diff_text: str, base_url: str, model: str) -> str:
    return await review_diff_with_prompt(diff_text, base_url, model, REVIEW_SYSTEM_PROMPT)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_llm_client.py -v`
Expected: PASS (2 tests — the pre-existing `test_review_diff_returns_model_content` must still pass unchanged, since `review_diff`'s external behavior is identical)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/prbot/llm_client.py tests/test_llm_client.py
git commit -m "feat: add review_diff_with_prompt for per-agent system prompts"
```

---

### Task 2: Agent activities + aggregator

**Files:**
- Modify: `src/prbot/activity_types.py` (append `AggregateInput`)
- Modify: `src/prbot/activities.py` (append 3 agent activities + prompts + `aggregate_activity`)
- Modify: `tests/test_activities.py` (append tests)

**Interfaces:**
- Consumes: `prbot.llm_client.review_diff_with_prompt`, `prbot.activity_types.ReviewInput`
- Produces: `prbot.activity_types.AggregateInput` (`security_result: str | None, style_result: str | None, test_coverage_result: str | None`); `prbot.activities.security_review_activity(ReviewInput) -> str`, `style_review_activity(ReviewInput) -> str`, `test_coverage_review_activity(ReviewInput) -> str`, `aggregate_activity(AggregateInput) -> str` (all `@activity.defn`, all async)

- [ ] **Step 1: Append to `src/prbot/activity_types.py`**

```python
@dataclass
class AggregateInput:
    security_result: str | None
    style_result: str | None
    test_coverage_result: str | None
```

- [ ] **Step 2: Write the failing tests**

Append to `tests/test_activities.py` (add `AggregateInput` to the existing import from `activity_types` if imported directly, or reference via `activities.AggregateInput` matching the existing test file's style of accessing re-exported names):

```python
async def test_security_review_activity_uses_security_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert diff_text == "diff-content"
        assert system_prompt == activities.SECURITY_SYSTEM_PROMPT
        return "security findings"

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await activities.security_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result == "security findings"


async def test_style_review_activity_uses_style_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert system_prompt == activities.STYLE_SYSTEM_PROMPT
        return "style findings"

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await activities.style_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result == "style findings"


async def test_test_coverage_review_activity_uses_test_coverage_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert system_prompt == activities.TEST_COVERAGE_SYSTEM_PROMPT
        return "test coverage findings"

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await activities.test_coverage_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result == "test coverage findings"


async def test_aggregate_activity_merges_all_three_sections():
    result = await activities.aggregate_activity(
        activities.AggregateInput(
            security_result="no issues found",
            style_result="looks clean",
            test_coverage_result="missing a test for the new branch",
        )
    )

    assert "### Security" in result
    assert "no issues found" in result
    assert "### Style" in result
    assert "looks clean" in result
    assert "### Test Coverage" in result
    assert "missing a test for the new branch" in result


async def test_aggregate_activity_notes_skipped_agent():
    result = await activities.aggregate_activity(
        activities.AggregateInput(
            security_result=None,
            style_result="looks clean",
            test_coverage_result="all covered",
        )
    )

    assert "### Security" in result
    assert "skipped" in result.lower()
```

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: FAIL with `AttributeError: module 'prbot.activities' has no attribute 'security_review_activity'` (or similar)

- [ ] **Step 4: Append to `src/prbot/activities.py`**

Add `AggregateInput` to the existing `from prbot.activity_types import ...` line, then append:

```python
SECURITY_SYSTEM_PROMPT = (
    "You are a security-focused code reviewer. Look at this GitHub pull "
    "request diff and flag any injection risks, hardcoded secrets, or unsafe "
    "patterns. Keep it under 150 words. If there are no concerns, say so briefly."
)

STYLE_SYSTEM_PROMPT = (
    "You are a style and convention reviewer. Look at this GitHub pull request "
    "diff and flag convention violations, overly complex code, or naming "
    "issues. Keep it under 150 words. If there are no concerns, say so briefly."
)

TEST_COVERAGE_SYSTEM_PROMPT = (
    "You are a test-coverage reviewer. Look at this GitHub pull request diff "
    "and flag any new logic that appears to lack test coverage. Keep it "
    "under 150 words. If there are no concerns, say so briefly."
)


@activity.defn
async def security_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, SECURITY_SYSTEM_PROMPT
    )


@activity.defn
async def style_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, STYLE_SYSTEM_PROMPT
    )


@activity.defn
async def test_coverage_review_activity(input: ReviewInput) -> str:
    settings = get_settings()
    return await llm_client.review_diff_with_prompt(
        input.diff_text, settings.ollama_base_url, settings.ollama_model, TEST_COVERAGE_SYSTEM_PROMPT
    )


@activity.defn
async def aggregate_activity(input: AggregateInput) -> str:
    sections = [
        ("Security", input.security_result),
        ("Style", input.style_result),
        ("Test Coverage", input.test_coverage_result),
    ]
    parts = []
    for title, result in sections:
        if result is None:
            parts.append(f"### {title}\n\n_{title} check skipped._")
        else:
            parts.append(f"### {title}\n\n{result}")
    return "\n\n".join(parts)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: PASS (9 tests — 4 existing + 5 new)

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 7: Commit**

```bash
git add src/prbot/activity_types.py src/prbot/activities.py tests/test_activities.py
git commit -m "feat: add security/style/test-coverage agent activities and aggregator"
```

---

### Task 3: Wire the workflow to run agents concurrently

**Files:**
- Modify: `src/prbot/workflows.py`
- Modify: `tests/test_workflows.py` (full replacement — the activity call sequence changes)

**Interfaces:**
- Consumes: `prbot.activity_types.AggregateInput` (added to the existing import line)
- Produces: same `PRReviewWorkflow.run(self, event: ReviewEvent) -> int` signature, unchanged externally — only the internal activity sequence changes

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
)
from prbot.workflows import PRReviewWorkflow, ReviewEvent


async def test_workflow_runs_agents_concurrently_and_completes():
    async with await WorkflowEnvironment.start_time_skipping() as env:
        calls = []

        @activity.defn(name="set_review_status_activity")
        async def fake_set_status(input: SetStatusInput) -> None:
            calls.append(("set_status", input.status))

        @activity.defn(name="fetch_diff_activity")
        async def fake_fetch_diff(input: FetchDiffInput) -> str:
            calls.append(("fetch_diff",))
            return "diff-text"

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

        @activity.defn(name="post_comment_activity")
        async def fake_post_comment(input: PostCommentInput) -> int:
            calls.append(("post_comment", input.body))
            return 42

        async with Worker(
            env.client,
            task_queue="test-queue-3-1",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                fake_fetch_diff,
                fake_security,
                fake_style,
                fake_test_coverage,
                fake_aggregate,
                fake_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=7, head_sha="abc123", installation_id="55")
            result = await env.client.execute_workflow(
                PRReviewWorkflow.run,
                event,
                id="test-workflow-3-1",
                task_queue="test-queue-3-1",
            )

        assert result == 42

        call_types = [c[0] for c in calls]
        assert call_types[0] == "set_status"
        assert calls[0][1] == "running"
        assert call_types[1] == "fetch_diff"
        # the three agent calls happen concurrently; assert all three occurred
        # between fetch_diff and aggregate, in any relative order
        agent_calls = {c[0] for c in calls if c[0] in ("security", "style", "test_coverage")}
        assert agent_calls == {"security", "style", "test_coverage"}
        for c in calls:
            if c[0] in ("security", "style", "test_coverage"):
                assert c[1] == "diff-text"
        aggregate_call = next(c for c in calls if c[0] == "aggregate")
        assert aggregate_call == ("aggregate", "security-result", "style-result", "test-coverage-result")
        assert call_types[-2] == "post_comment"
        assert calls[-2][1] == "aggregated-body"
        assert call_types[-1] == "set_status"
        assert calls[-1][1] == "complete"


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

        @activity.defn(name="post_comment_activity")
        async def unused_post_comment(input: PostCommentInput) -> int:
            raise AssertionError("should not be called")

        async with Worker(
            env.client,
            task_queue="test-queue-3-2",
            workflows=[PRReviewWorkflow],
            activities=[
                fake_set_status,
                failing_fetch_diff,
                unused_security,
                unused_style,
                unused_test_coverage,
                unused_aggregate,
                unused_post_comment,
            ],
        ):
            event = ReviewEvent(owner="chitaki10", repo="demo", pr_number=8, head_sha="def456", installation_id="55")

            with pytest.raises(WorkflowFailureError):
                await env.client.execute_workflow(
                    PRReviewWorkflow.run,
                    event,
                    id="test-workflow-3-2",
                    task_queue="test-queue-3-2",
                )

        assert calls == ["running", "failed"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: FAIL — the workflow still only calls `"review_activity"`, so the new fakes (`security_review_activity` etc.) are never invoked and the aggregate/order assertions fail.

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

If `asyncio.gather` over `workflow.execute_activity(...)` calls doesn't behave as expected in the installed temporalio version, introspect: `.venv\Scripts\python -c "from temporalio import workflow; help(workflow.execute_activity)"`. This is the standard documented pattern for concurrent activities in Temporal Python workflows, so it should work as written — Task 4 of Stage 2 found zero API discrepancies against temporalio 1.30.0.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_workflows.py -v`
Expected: PASS (2 tests)

- [ ] **Step 5: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass

- [ ] **Step 6: Commit**

```bash
git add src/prbot/workflows.py tests/test_workflows.py
git commit -m "feat: run security/style/test-coverage agents concurrently in the workflow"
```

---

### Task 4: Manual E2E verification

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage3-verification.md`

No automated test beyond Tasks 1-3 — this is the Stage 3 demo checkpoint.

- [ ] **Step 1: Restart the worker** (it must reload the new workflow/activity code)

```bash
.venv\Scripts\python -m prbot.worker
```

- [ ] **Step 2: Trigger a review**

Open a PR (or push a commit) on the demo repo.

- [ ] **Step 3: Confirm the posted comment has three sections**

Check the PR comment has `### Security`, `### Style`, and `### Test Coverage` headers, each with real model-generated content (not "skipped" — Stage 5 is what introduces skipping).

- [ ] **Step 4: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage3-verification.md` noting the PR used and confirming all three sections appeared with real content.

- [ ] **Step 5: Commit**

```bash
git add docs/superpowers/plans/2026-07-19-stage3-verification.md
git commit -m "docs: record Stage 3 multi-agent E2E verification"
```

---

## After this plan

Stage 4 (staleness) gets its own implementation plan when reached.
