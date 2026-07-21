import pybreaker
import pytest

from prbot.agents import style


async def test_style_review_activity_uses_style_prompt(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    style.get_settings.cache_clear()

    async def fake_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        assert system_prompt == style.STYLE_SYSTEM_PROMPT
        return "style findings"

    monkeypatch.setattr(style.llm_client, "review_diff_with_prompt", fake_review_diff_with_prompt)

    result = await style.style_review_activity(style.ReviewInput(diff_text="diff-content"))

    assert result == "style findings"


async def test_style_review_activity_returns_none_when_breaker_open(monkeypatch):
    monkeypatch.setenv("GITHUB_APP_ID", "1")
    monkeypatch.setenv("GITHUB_PRIVATE_KEY_PATH", "unused.pem")
    monkeypatch.setenv("GITHUB_WEBHOOK_SECRET", "unused")
    style.get_settings.cache_clear()
    monkeypatch.setattr(style, "style_breaker", pybreaker.CircuitBreaker(fail_max=2, reset_timeout=60))

    async def failing_review_diff_with_prompt(diff_text, base_url, model, system_prompt):
        raise RuntimeError("model unreachable")

    monkeypatch.setattr(style.llm_client, "review_diff_with_prompt", failing_review_diff_with_prompt)

    with pytest.raises(RuntimeError):
        await style.style_review_activity(style.ReviewInput(diff_text="diff-content"))

    result = await style.style_review_activity(style.ReviewInput(diff_text="diff-content"))

    assert result is None
