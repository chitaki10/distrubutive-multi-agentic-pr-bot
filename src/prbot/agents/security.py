import pybreaker
from temporalio import activity

from prbot.integrations import llm_client
from prbot.activity_types import ReviewInput
from prbot.config import get_settings

SECURITY_SYSTEM_PROMPT = (
    "You are a security-focused code reviewer. Look at this GitHub pull "
    "request diff and flag any injection risks, hardcoded secrets, or unsafe "
    "patterns. Keep it under 150 words. If there are no concerns, say so briefly."
)

security_breaker = pybreaker.CircuitBreaker(fail_max=3, reset_timeout=60)


@activity.defn
async def security_review_activity(input: ReviewInput) -> str | None:
    settings = get_settings()

    async def _review_call():
        return await llm_client.review_diff_with_prompt(
            input.diff_text,
            settings.ollama_base_url,
            settings.ollama_model,
            SECURITY_SYSTEM_PROMPT,
        )

    try:
        return await security_breaker.call_async(_review_call)
    except pybreaker.CircuitBreakerError:
        return None
