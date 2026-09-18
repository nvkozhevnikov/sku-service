from __future__ import annotations

import asyncio
from dataclasses import asdict, dataclass
from email.utils import parsedate_to_datetime
import random
import threading
import time
from typing import Any, Awaitable, Callable, Protocol


TRANSIENT_STATUSES = {429, 500, 502, 503, 504}


class Clock(Protocol):
    def monotonic(self) -> float: ...
    def sleep(self, seconds: float) -> None: ...


class SystemClock:
    monotonic = staticmethod(time.monotonic)
    sleep = staticmethod(time.sleep)


class AsyncClock(Protocol):
    def monotonic(self) -> float: ...
    async def sleep(self, seconds: float) -> None: ...


class SystemAsyncClock:
    monotonic = staticmethod(time.monotonic)

    @staticmethod
    async def sleep(seconds: float) -> None:
        await asyncio.sleep(seconds)


class MaxRunDurationExceeded(RuntimeError):
    """Raised before another HTTP attempt can start after the crawl deadline."""

    def __init__(self, message: str = "MAX_RUN_DURATION_EXCEEDED") -> None:
        super().__init__(message)


@dataclass(frozen=True)
class DomainRatePolicy:
    max_concurrency: int = 1
    min_request_interval_seconds: float = 0.2
    jitter_min_seconds: float = 0.0
    jitter_max_seconds: float = 0.1
    request_timeout_seconds: float = 40.0
    max_attempts: int = 3
    backoff_base_seconds: float = 0.5
    backoff_max_seconds: float = 8.0
    respect_retry_after: bool = True
    consecutive_error_threshold: int = 5
    cooldown_seconds: float = 30.0
    max_run_duration_seconds: float = 7200.0

    def __post_init__(self) -> None:
        if self.max_concurrency < 1 or self.max_concurrency > 64:
            raise ValueError("max_concurrency must be between 1 and 64")
        for name in (
            "min_request_interval_seconds", "jitter_min_seconds", "jitter_max_seconds",
            "backoff_base_seconds", "backoff_max_seconds", "cooldown_seconds",
        ):
            if getattr(self, name) < 0:
                raise ValueError(f"{name} cannot be negative")
        if self.request_timeout_seconds <= 0:
            raise ValueError("request_timeout_seconds must be positive")
        if self.max_run_duration_seconds <= 0:
            raise ValueError("max_run_duration_seconds must be positive")
        if self.jitter_max_seconds < self.jitter_min_seconds:
            raise ValueError("jitter_max_seconds must be >= jitter_min_seconds")
        if self.backoff_max_seconds < self.backoff_base_seconds:
            raise ValueError("backoff_max_seconds must be >= backoff_base_seconds")
        if self.max_attempts < 1 or self.consecutive_error_threshold < 1:
            raise ValueError("attempt and threshold settings must be positive")

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "DomainRatePolicy":
        allowed = cls.__dataclass_fields__
        return cls(**{key: item for key, item in (value or {}).items() if key in allowed})

    def snapshot(self) -> dict[str, Any]:
        return asdict(self)


PARTNER_ST_DEFAULT_RATE_POLICY = DomainRatePolicy(max_concurrency=4, min_request_interval_seconds=0.20, request_timeout_seconds=40)
OPTIMUM_DEFAULT_RATE_POLICY = DomainRatePolicy(max_concurrency=3, min_request_interval_seconds=0.15, request_timeout_seconds=35)


@dataclass
class RequestMetrics:
    requests_total: int = 0
    http_2xx: int = 0
    http_3xx: int = 0
    http_4xx: int = 0
    http_5xx: int = 0
    http_429: int = 0
    network_errors: int = 0
    retry_count: int = 0
    response_time_total_seconds: float = 0.0
    rate_limit_waits: int = 0
    rate_limit_wait_seconds: float = 0.0
    circuit_breaker_trips: int = 0
    circuit_breaker_cooldown_seconds: float = 0.0
    retry_after_waits: int = 0
    retry_after_wait_seconds: float = 0.0
    backoff_waits: int = 0
    backoff_wait_seconds: float = 0.0
    max_run_duration_exceeded: bool = False
    requests_skipped_deadline: int = 0
    last_failure_reason: str | None = None

    def record_status(self, status: int, elapsed: float) -> None:
        self.requests_total += 1
        self.response_time_total_seconds += max(0.0, elapsed)
        if 200 <= status < 300:
            self.http_2xx += 1
        elif 300 <= status < 400:
            self.http_3xx += 1
        elif 400 <= status < 500:
            self.http_4xx += 1
        elif 500 <= status < 600:
            self.http_5xx += 1
        if status == 429:
            self.http_429 += 1

    def record_network_error(self, elapsed: float, reason: str) -> None:
        self.requests_total += 1
        self.response_time_total_seconds += max(0.0, elapsed)
        self.network_errors += 1
        self.last_failure_reason = reason

    def as_metadata(self) -> dict[str, Any]:
        result = asdict(self)
        result["average_response_time_seconds"] = (
            round(self.response_time_total_seconds / self.requests_total, 6)
            if self.requests_total else None
        )
        return result


class DomainRateLimiter:
    """One start-time gate shared by every worker for one remote domain."""

    def __init__(self, policy: DomainRatePolicy, *, clock: Clock | None = None,
                 random_uniform: Callable[[float, float], float] | None = None) -> None:
        self.policy = policy
        self.clock = clock or SystemClock()
        self.random_uniform = random_uniform or random.uniform
        self._lock = threading.Lock()
        self._next_start = 0.0

    def wait(self, metrics: RequestMetrics | None = None,
             *, deadline: float | None = None) -> float:
        with self._lock:
            now = self.clock.monotonic()
            if deadline is not None and now >= deadline:
                if metrics:
                    metrics.max_run_duration_exceeded = True
                    metrics.requests_skipped_deadline += 1
                    metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                raise MaxRunDurationExceeded()
            delay = max(0.0, self._next_start - now)
            if delay:
                actual_delay = delay
                if deadline is not None:
                    actual_delay = min(delay, max(0.0, deadline - now))
                if metrics:
                    metrics.rate_limit_waits += 1
                    metrics.rate_limit_wait_seconds += actual_delay
                self.clock.sleep(actual_delay)
                if deadline is not None and self.clock.monotonic() >= deadline:
                    if metrics:
                        metrics.max_run_duration_exceeded = True
                        metrics.requests_skipped_deadline += 1
                        metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                    raise MaxRunDurationExceeded()
            jitter = self.random_uniform(self.policy.jitter_min_seconds, self.policy.jitter_max_seconds)
            self._next_start = self.clock.monotonic() + self.policy.min_request_interval_seconds + jitter
            return delay


class AsyncDomainRateLimiter:
    """Async equivalent: semaphore bounds concurrency, this gate paces starts."""

    def __init__(self, policy: DomainRatePolicy, *, clock: AsyncClock | None = None,
                 random_uniform: Callable[[float, float], float] | None = None) -> None:
        self.policy = policy
        self.clock = clock or SystemAsyncClock()
        self.random_uniform = random_uniform or random.uniform
        self._lock = asyncio.Lock()
        self._next_start = 0.0

    async def wait(self, metrics: RequestMetrics | None = None,
                   *, deadline: float | None = None) -> float:
        async with self._lock:
            now = self.clock.monotonic()
            if deadline is not None and now >= deadline:
                if metrics:
                    metrics.max_run_duration_exceeded = True
                    metrics.requests_skipped_deadline += 1
                    metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                raise MaxRunDurationExceeded()
            delay = max(0.0, self._next_start - now)
            if delay:
                actual_delay = delay
                if deadline is not None:
                    actual_delay = min(delay, max(0.0, deadline - now))
                if metrics:
                    metrics.rate_limit_waits += 1
                    metrics.rate_limit_wait_seconds += actual_delay
                await self.clock.sleep(actual_delay)
                if deadline is not None and self.clock.monotonic() >= deadline:
                    if metrics:
                        metrics.max_run_duration_exceeded = True
                        metrics.requests_skipped_deadline += 1
                        metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                    raise MaxRunDurationExceeded()
            jitter = self.random_uniform(self.policy.jitter_min_seconds, self.policy.jitter_max_seconds)
            self._next_start = self.clock.monotonic() + self.policy.min_request_interval_seconds + jitter
            return delay


class CircuitOpen(RuntimeError):
    pass


class CircuitBreaker:
    def __init__(self, policy: DomainRatePolicy, *, clock: Clock | AsyncClock | None = None,
                 metrics: RequestMetrics | None = None) -> None:
        self.policy = policy
        self.clock = clock or SystemClock()
        self.metrics = metrics or RequestMetrics()
        self.consecutive_failures = 0
        self.open_until = 0.0

    def before_request(self) -> None:
        if self.clock.monotonic() < self.open_until:
            raise CircuitOpen(f"circuit open for {self.open_until - self.clock.monotonic():.3f}s")

    def success(self) -> None:
        self.consecutive_failures = 0
        self.open_until = 0.0

    def failure(self, reason: str) -> None:
        self.metrics.last_failure_reason = reason
        self.consecutive_failures += 1
        if self.consecutive_failures >= self.policy.consecutive_error_threshold:
            self.open_until = self.clock.monotonic() + self.policy.cooldown_seconds
            self.metrics.circuit_breaker_trips += 1
            self.metrics.circuit_breaker_cooldown_seconds += self.policy.cooldown_seconds


def retry_after_seconds(headers: Any, *, now_epoch: float | None = None) -> float | None:
    raw = None
    if headers is not None:
        raw = headers.get("Retry-After") or headers.get("retry-after")
    if raw in (None, ""):
        return None
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        try:
            value = parsedate_to_datetime(str(raw)).timestamp() - (time.time() if now_epoch is None else now_epoch)
            return max(0.0, value)
        except (TypeError, ValueError, OverflowError):
            return None


def backoff_seconds(policy: DomainRatePolicy, attempt: int) -> float:
    return min(policy.backoff_max_seconds, policy.backoff_base_seconds * (2 ** max(0, attempt - 1)))


def request_with_resilience(
    send: Callable[[], Any], policy: DomainRatePolicy, limiter: DomainRateLimiter,
    *, clock: Clock | None = None, metrics: RequestMetrics | None = None,
    breaker: CircuitBreaker | None = None,
    deadline: float | None = None,
) -> tuple[Any, int]:
    clock = clock or SystemClock()
    metrics = metrics or RequestMetrics()
    breaker = breaker or CircuitBreaker(policy, clock=clock, metrics=metrics)
    last_error: Exception | None = None
    for attempt in range(1, policy.max_attempts + 1):
        if deadline is not None and clock.monotonic() >= deadline:
            metrics.max_run_duration_exceeded = True
            metrics.requests_skipped_deadline += 1
            metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded()
        breaker.before_request()
        limiter.wait(metrics, deadline=deadline)
        if deadline is not None and clock.monotonic() >= deadline:
            metrics.max_run_duration_exceeded = True
            metrics.requests_skipped_deadline += 1
            metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded()
        # Another worker can open the shared circuit while this request waits
        # at the domain pacing gate.
        breaker.before_request()
        started = clock.monotonic()
        retry_after = None
        try:
            response = send()
            status = int(response.status_code)
            metrics.record_status(status, clock.monotonic() - started)
            if status not in TRANSIENT_STATUSES:
                breaker.success()
                return response, attempt
            breaker.failure(f"HTTP_{status}")
            if attempt == policy.max_attempts:
                return response, attempt
            retry_after = retry_after_seconds(getattr(response, "headers", {})) if status == 429 and policy.respect_retry_after else None
            delay = retry_after if retry_after is not None else backoff_seconds(policy, attempt)
        except Exception as error:
            last_error = error
            metrics.record_network_error(clock.monotonic() - started, type(error).__name__)
            breaker.failure(type(error).__name__)
            if attempt == policy.max_attempts:
                raise
            delay = backoff_seconds(policy, attempt)
        metrics.retry_count += 1
        actual_delay = delay
        if deadline is not None:
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                metrics.max_run_duration_exceeded = True
                metrics.requests_skipped_deadline += 1
                metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                raise MaxRunDurationExceeded()
            if delay >= remaining:
                actual_delay = remaining
        if retry_after is not None:
            metrics.retry_after_waits += 1
            metrics.retry_after_wait_seconds += actual_delay
        else:
            metrics.backoff_waits += 1
            metrics.backoff_wait_seconds += actual_delay
        if deadline is not None and delay >= remaining:
            clock.sleep(actual_delay)
            metrics.max_run_duration_exceeded = True
            metrics.requests_skipped_deadline += 1
            metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded()
        clock.sleep(delay)
    raise RuntimeError(f"request failed: {last_error}")


async def async_request_with_resilience(
    send: Callable[[], Awaitable[Any]], policy: DomainRatePolicy, limiter: AsyncDomainRateLimiter,
    *, clock: AsyncClock | None = None, metrics: RequestMetrics | None = None,
    breaker: CircuitBreaker | None = None,
    deadline: float | None = None,
) -> tuple[Any, int]:
    clock = clock or SystemAsyncClock()
    metrics = metrics or RequestMetrics()
    breaker = breaker or CircuitBreaker(policy, clock=clock, metrics=metrics)
    for attempt in range(1, policy.max_attempts + 1):
        if deadline is not None and clock.monotonic() >= deadline:
            metrics.max_run_duration_exceeded = True
            metrics.requests_skipped_deadline += 1
            metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded()
        breaker.before_request()
        await limiter.wait(metrics, deadline=deadline)
        if deadline is not None and clock.monotonic() >= deadline:
            metrics.max_run_duration_exceeded = True
            metrics.requests_skipped_deadline += 1
            metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded()
        # Another concurrent attempt may have opened the shared breaker while
        # this task was waiting at the start-time gate.
        breaker.before_request()
        started = clock.monotonic()
        retry_after = None
        try:
            response = await send()
            status = int(response.status_code)
            metrics.record_status(status, clock.monotonic() - started)
            if status not in TRANSIENT_STATUSES:
                breaker.success()
                return response, attempt
            breaker.failure(f"HTTP_{status}")
            if attempt == policy.max_attempts:
                return response, attempt
            retry_after = retry_after_seconds(getattr(response, "headers", {})) if status == 429 and policy.respect_retry_after else None
            delay = retry_after if retry_after is not None else backoff_seconds(policy, attempt)
        except Exception as error:
            metrics.record_network_error(clock.monotonic() - started, type(error).__name__)
            breaker.failure(type(error).__name__)
            if attempt == policy.max_attempts:
                raise
            delay = backoff_seconds(policy, attempt)
        metrics.retry_count += 1
        if retry_after is not None:
            metrics.retry_after_waits += 1
            metrics.retry_after_wait_seconds += delay
        else:
            metrics.backoff_waits += 1
            metrics.backoff_wait_seconds += delay
        if deadline is not None:
            remaining = deadline - clock.monotonic()
            if remaining <= 0:
                metrics.max_run_duration_exceeded = True
                metrics.requests_skipped_deadline += 1
                metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                raise MaxRunDurationExceeded()
            if delay >= remaining:
                await clock.sleep(remaining)
                metrics.max_run_duration_exceeded = True
                metrics.requests_skipped_deadline += 1
                metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
                raise MaxRunDurationExceeded()
        await clock.sleep(delay)
    raise RuntimeError("request failed")
