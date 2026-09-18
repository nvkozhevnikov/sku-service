from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace

import httpx

from .constants import USER_AGENT
from universal_supplier.control_plane.rate_control import (
    AsyncDomainRateLimiter,
    CircuitBreaker,
    DomainRatePolicy,
    MaxRunDurationExceeded,
    OPTIMUM_DEFAULT_RATE_POLICY,
    RequestMetrics,
    async_request_with_resilience,
)


@dataclass
class FetchResult:
    requested_url: str
    final_url: str
    status_code: int
    text: str
    attempt_count: int = 1


class HttpFetcher:
    def __init__(self, concurrency: int | None = None, delay: float | None = None,
                 timeout: float | None = None,
                 *, rate_policy: DomainRatePolicy | None = None, clock=None, random_uniform=None,
                 client: httpx.AsyncClient | None = None):
        # A supplied snapshot is already authoritative.  In particular, do not
        # rebuild it from concurrency/delay and silently restore other defaults.
        self.policy = rate_policy or replace(
            OPTIMUM_DEFAULT_RATE_POLICY,
            max_concurrency=(OPTIMUM_DEFAULT_RATE_POLICY.max_concurrency
                             if concurrency is None else concurrency),
            min_request_interval_seconds=(OPTIMUM_DEFAULT_RATE_POLICY.min_request_interval_seconds
                                          if delay is None else delay),
            request_timeout_seconds=(OPTIMUM_DEFAULT_RATE_POLICY.request_timeout_seconds
                                     if timeout is None else timeout),
        )
        self.semaphore = asyncio.Semaphore(self.policy.max_concurrency)
        self.delay = self.policy.min_request_interval_seconds
        self.metrics = RequestMetrics()
        self.clock = clock
        self.limiter = AsyncDomainRateLimiter(
            self.policy, clock=self.clock, random_uniform=random_uniform)
        # Use the exact clock selected by the limiter when the caller did not
        # inject one, keeping pacing, circuit state and the crawl deadline on
        # one monotonic timeline.
        self.clock = self.limiter.clock
        self.breaker = CircuitBreaker(self.policy, clock=self.clock, metrics=self.metrics)
        self.started_monotonic = self.clock.monotonic()
        self.deadline = self.started_monotonic + self.policy.max_run_duration_seconds
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=self.policy.request_timeout_seconds,
            follow_redirects=True,
        )

    async def close(self):
        await self.client.aclose()

    @property
    def deadline_exceeded(self) -> bool:
        return bool(
            self.metrics.max_run_duration_exceeded
            or self.clock.monotonic() >= self.deadline
        )

    def ensure_within_deadline(self) -> None:
        if self.clock.monotonic() >= self.deadline:
            self.metrics.max_run_duration_exceeded = True
            self.metrics.requests_skipped_deadline += 1
            self.metrics.last_failure_reason = "MAX_RUN_DURATION_EXCEEDED"
            raise MaxRunDurationExceeded("MAX_RUN_DURATION_EXCEEDED")

    async def get(self, url: str) -> FetchResult:
        self.ensure_within_deadline()
        async with self.semaphore:
            self.ensure_within_deadline()
            response, attempts = await async_request_with_resilience(
                # Pass timeout per real request too.  This enforces the policy
                # even when tests or an embedding application inject a client
                # whose own default timeout differs.
                lambda: self.client.get(
                    url, timeout=self.policy.request_timeout_seconds),
                self.policy, self.limiter,
                clock=self.clock, metrics=self.metrics, breaker=self.breaker,
                deadline=self.deadline,
            )
            return FetchResult(
                url, str(response.url), response.status_code, response.text, attempts)
