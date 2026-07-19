# tests/test_activities.py
import pytest
import pybreaker

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


async def test_security_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    activities.get_settings.cache_clear()
    monkeypatch.setattr(activities, "security_breaker", pybreaker.CircuitBreaker(fail_max=2, reset_timeout=60))

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
    monkeypatch.setattr(activities, "style_breaker", pybreaker.CircuitBreaker(fail_max=2, reset_timeout=60))

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
    monkeypatch.setattr(activities, "test_coverage_breaker", pybreaker.CircuitBreaker(fail_max=2, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(activities.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await activities.test_coverage_review_activity(activities.ReviewInput(diff_text="diff-content"))

    result = await activities.test_coverage_review_activity(activities.ReviewInput(diff_text="diff-content"))

    assert result is None
