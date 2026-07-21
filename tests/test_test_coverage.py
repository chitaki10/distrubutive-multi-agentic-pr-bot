import pytest

from prbot.agents import test_coverage
from prbot.circuit_breaker import CircuitBreaker


async def test_test_coverage_review_activity_uses_test_coverage_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    test_coverage.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert system_prompt == test_coverage.TEST_COVERAGE_SYSTEM_PROMPT
        return "test coverage findings"

    monkeypatch.setattr(test_coverage.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await test_coverage.test_coverage_review_activity(test_coverage.ReviewInput(diff_text="diff-content"))

    assert result == "test coverage findings"


async def test_test_coverage_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    test_coverage.get_settings.cache_clear()
    monkeypatch.setattr(test_coverage, "test_coverage_breaker", CircuitBreaker(fail_max=2, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(test_coverage.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await test_coverage.test_coverage_review_activity(test_coverage.ReviewInput(diff_text="diff-content"))

    result = await test_coverage.test_coverage_review_activity(test_coverage.ReviewInput(diff_text="diff-content"))

    assert result is None
