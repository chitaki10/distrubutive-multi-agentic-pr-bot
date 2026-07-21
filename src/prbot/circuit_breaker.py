import time
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class CircuitBreakerError(Exception):
    pass


class CircuitBreaker:
    def __init__(self, fail_max: int, reset_timeout: float):
        self.fail_max = fail_max
        self.reset_timeout = reset_timeout
        self._fail_count = 0
        self._opened_at: float | None = None

    def _state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if time.monotonic() - self._opened_at >= self.reset_timeout:
            return "half-open"
        return "open"

    async def call_async(self, fn: Callable[..., Awaitable[T]], *args, **kwargs) -> T:
        state = self._state()
        if state == "open":
            raise CircuitBreakerError("Circuit breaker is open")

        try:
            result = await fn(*args, **kwargs)
        except Exception as exc:
            self._fail_count += 1
            if state == "half-open" or self._fail_count >= self.fail_max:
                self._opened_at = time.monotonic()
                raise CircuitBreakerError(f"Circuit breaker opened after {self._fail_count} failures") from exc
            raise
        else:
            self._fail_count = 0
            self._opened_at = None
            return result
