# Stage 5: Circuit Breaker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wrap each agent's Ollama call in a `pybreaker.CircuitBreaker`. If an agent's calls keep failing, the breaker opens and that agent's activity returns `None` (skipped) instead of raising — the workflow proceeds without hanging, and the aggregator already notes "X check skipped" (built in Stage 3).

**Architecture:** Purely a change to `src/prbot/activities.py` — no changes needed to `workflows.py` or `worker.py`. `AggregateInput`'s fields were deliberately typed `str | None` back in Stage 3 specifically so this stage wouldn't need to touch the workflow: a skipped agent's `None` flows straight through the existing `asyncio.gather` → `AggregateInput` → `aggregate_activity` path unchanged.

**Tech Stack:** Adds `pybreaker` and `tornado`. **Important, verified during brainstorming (not a guess):** pybreaker 1.4.1's `CircuitBreaker.call_async` internally uses `tornado.gen.coroutine`/`gen.Return` but does not import `tornado` itself — without `tornado` installed, calling `call_async` raises `NameError: name 'gen' is not defined` instead of the real underlying error. This was confirmed by installing pybreaker alone (broken), then installing `tornado` alongside it (fixed) and testing both the real-failure and breaker-open paths directly. `tornado` here is purely to satisfy this internal dependency — nothing in this project uses Tornado's web server or anything else from it.

## Global Constraints

- Three module-level `pybreaker.CircuitBreaker` instances in `activities.py`, one per agent (`security_breaker`, `style_breaker`, `test_coverage_breaker`), `fail_max=3, reset_timeout=60`. Breaker state persists across workflow executions within a worker process's lifetime (intended — it tracks repeated failures over time, not per-review).
- Each agent activity catches `pybreaker.CircuitBreakerError` specifically and returns `None` — any *other* exception still propagates as before (Temporal's retry policy still applies to genuine transient failures; the breaker only changes behavior once its own threshold is reached).
- The 3 agent activities' return type changes from `str` to `str | None` in their type hints — this is a hint-only change, not a dataclass change, so `activity_types.py` is untouched.

---

### Task 1: Wrap agent activities in circuit breakers

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/prbot/activities.py`
- Modify: `tests/test_activities.py` (append)

**Interfaces:**
- Produces: `prbot.activities.security_breaker`, `style_breaker`, `test_coverage_breaker` (module-level `pybreaker.CircuitBreaker` instances); `security_review_activity`/`style_review_activity`/`test_coverage_review_activity` now return `str | None` (unchanged signature otherwise)

- [ ] **Step 1: Update `pyproject.toml` dependencies**

Add two entries to `[project] dependencies`:
```toml
    "pybreaker>=1.4,<2",
    "tornado>=6.0,<7",
```

- [ ] **Step 2: Write the failing test**

Append to `tests/test_activities.py`. Add `import pytest` and `import pybreaker` to the top of the file if not already present.

```python
async def test_security_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()
    monkeypatch.setattr(activities, "security_breaker", pybreaker.CircuitBreaker(fail_max=1, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await activities.security_review_activity(activities.ReviewInput(diff_text="diff-content"))

    result = await activities.security_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result is None


async def test_style_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()
    monkeypatch.setattr(activities, "style_breaker", pybreaker.CircuitBreaker(fail_max=1, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await activities.style_review_activity(activities.ReviewInput(diff_text="diff-content"))

    result = await activities.style_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result is None


async def test_test_coverage_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()
    monkeypatch.setattr(activities, "test_coverage_breaker", pybreaker.CircuitBreaker(fail_max=1, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await activities.test_coverage_review_activity(activities.ReviewInput(diff_text="diff-content"))

    result = await activities.test_coverage_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result is None
```

Note: the existing happy-path tests (`test_security_review_activity_uses_security_prompt` etc. from Stage 3) need NO changes — `monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", fake)` still correctly intercepts the call even once it's routed through `breaker.call_async(llm_client.review_diff_with_prompt, ...)`, since that's still a plain attribute lookup on the `llm_client` module at call time.

- [ ] **Step 3: Run tests to verify they fail**

Run: `.venv\Scripts\pip install -e ".[dev]"` (picks up the new pybreaker/tornado deps), then `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: FAIL — `AttributeError: module 'prbot.activities' has no attribute 'security_breaker'` (or similar; the activities don't use a breaker yet)

- [ ] **Step 4: Update `src/prbot/activities.py`**

Add `import pybreaker` near the top (alongside the other imports), then add the three breaker instances right after the prompt constants, then replace the three agent activity functions with breaker-wrapped versions:

```python
security_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=60)
style_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=60)
test_coverage_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=60)


@activity.defn
async def security_review_activity(input: ReviewInput) -> str | None:
    settings = get_settings()
    try:
        return await security_breaker.call_async(
            llm_client.review_diff_with_prompt,
            input.diff_text,
            settings.ollama_base_url,
            settings.ollama_model,
            SECURITY_SYSTEM_PROMPT,
        )
    except pybreaker.CircuitBreakerError:
        return None


@activity.defn
async def style_review_activity(input: ReviewInput) -> str | None:
    settings = get_settings()
    try:
        return await style_breaker.call_async(
            llm_client.review_diff_with_prompt,
            input.diff_text,
            settings.ollama_base_url,
            settings.ollama_model,
            STYLE_SYSTEM_PROMPT,
        )
    except pybreaker.CircuitBreakerError:
        return None


@activity.defn
async def test_coverage_review_activity(input: ReviewInput) -> str | None:
    settings = get_settings()
    try:
        return await test_coverage_breaker.call_async(
            llm_client.review_diff_with_prompt,
            input.diff_text,
            settings.ollama_base_url,
            settings.ollama_model,
            TEST_COVERAGE_SYSTEM_PROMPT,
        )
    except pybreaker.CircuitBreakerError:
        return None
```

Do not modify `fetch_diff_activity`, `post_comment_activity`, `set_review_status_activity`, `check_staleness_activity`, or `aggregate_activity` — only the three agent activities change.

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv\Scripts\pytest tests/test_activities.py -v`
Expected: PASS (all tests, including the 3 new breaker tests and the unmodified Stage 3 happy-path tests for the same 3 activities)

- [ ] **Step 6: Run the full suite**

Run: `.venv\Scripts\pytest -v`
Expected: all tests pass — `workflows.py`/`test_workflows.py` are untouched by this stage and should need no changes, since `AggregateInput`'s `str | None` typing already anticipated this.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/prbot/activities.py tests/test_activities.py
git commit -m "feat: wrap agent activities in per-agent circuit breakers"
```

---

### Task 2: Manual E2E verification — stop Ollama mid-demo

**Files:**
- Create: `docs/superpowers/plans/2026-07-19-stage5-verification.md`

No automated test beyond Task 1 — this is the Stage 5 demo checkpoint.

- [ ] **Step 1: Restart the worker** (must reload the new activity code)

```bash
.venv\Scripts\python -m prbot.worker
```

- [ ] **Step 2: Stop Ollama**

Stop the Ollama service/process so all three agent calls will fail to connect.

- [ ] **Step 3: Trigger a review**

Open a PR (or push a commit).

- [ ] **Step 4: Confirm the outcome**

Expect: since all three agents share the same unreachable Ollama target, all three will likely fail and get skipped on this run (not just one) — that's expected and still proves the mechanism (each agent's own breaker independently trips). Confirm via the posted comment that skipped sections say "check skipped" rather than the workflow hanging or failing outright. Also confirm `pr_reviews.status` reaches `complete` (not `failed`) — a skipped agent is not a workflow failure.

- [ ] **Step 5: Restart Ollama, trigger one more review, confirm normal operation resumes**

Once `reset_timeout` (60s) has passed and Ollama is back, a fresh review should get real content again (the breaker moves to half-open, a successful call closes it).

- [ ] **Step 6: Record the verification**

Write `docs/superpowers/plans/2026-07-19-stage5-verification.md` noting the PR used, the comment's actual content confirming skip notices, and the recovery after restarting Ollama.

- [ ] **Step 7: Commit**

```bash
git add docs/superpowers/plans/2026-07-19-stage5-verification.md
git commit -m "docs: record Stage 5 circuit breaker E2E verification"
```

---

## After this plan

Stage 6 (saga/compensation) gets its own implementation plan when reached.
