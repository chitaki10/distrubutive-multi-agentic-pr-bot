import pybreaker
import pytest

from prbot.agents import security


async def test_security_review_activity_uses_security_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    security.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert diff_text == "diff-content"
        assert system_prompt == security.SECURITY_SYSTEM_PROMPT
        return "security findings"

    monkeypatch.setattr(security.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await security.security_review_activity(security.ReviewInput(diff_text="diff-content"))

    assert result == "security findings"


async def test_security_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    security.get_settings.cache_clear()
    monkeypatch.setattr(security, "security_breaker", pybreaker.CircuitBreaker(fail_max=2, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(security.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await security.security_review_activity(security.ReviewInput(diff_text="diff-content"))

    result = await security.security_review_activity(security.ReviewInput(diff_text="diff-content"))

    assert result is None
