import asyncio

import pytest

from prbot.circuit_breaker import CircuitBreaker, CircuitBreakerError


async def _ok():
    return "ok"


async def _boom():
    raise RuntimeError("boom")


async def test_closed_breaker_allows_calls_and_resets_fail_count_on_success():
    breaker = CircuitBreaker(fail_max=2, reset_timeout=60)

    with pytest.raises(RuntimeError):
        await breaker.call_async(_boom)

    result = await breaker.call_async(_ok)

    assert result == "ok"
    assert breaker._fail_count == 0


async def test_breaker_opens_after_fail_max_failures_and_rejects_immediately():
    breaker = CircuitBreaker(fail_max=2, reset_timeout=60)

    with pytest.raises(RuntimeError):
        await breaker.call_async(_boom)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_boom)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_ok)


async def test_breaker_allows_trial_call_after_reset_timeout_and_closes_on_success():
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_boom)

    await asyncio.sleep(0.1)

    result = await breaker.call_async(_ok)

    assert result == "ok"
    assert breaker._fail_count == 0
    assert breaker._opened_at is None


async def test_breaker_reopens_immediately_when_half_open_trial_fails():
    breaker = CircuitBreaker(fail_max=1, reset_timeout=0.05)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_boom)

    await asyncio.sleep(0.1)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_boom)

    with pytest.raises(CircuitBreakerError):
        await breaker.call_async(_ok)
