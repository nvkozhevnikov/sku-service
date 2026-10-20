from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace
from typing import Any, Callable
from urllib.parse import urljoin

import httpx

from .constants import USER_AGENT
from universal_supplier.http_capture import MAX_RESPONSE_BYTES, ResponseTooLargeError
from universal_supplier.url_safety import UnsafeUrlError, host_validator
from universal_supplier.control_plane.rate_control import (
    AsyncDomainRateLimiter,
    CircuitBreaker,
    DomainRatePolicy,
    MaxRunDurationExceeded,
    OPTIMUM_DEFAULT_RATE_POLICY,
    RequestMetrics,
    async_request_with_resilience,
)


OPTIMUM_HOSTS = ("optimum.su", "www.optimum.su")
REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})
MAX_REDIRECTS = 5


@dataclass
class _BoundedResponse:
    """Fully read (size-capped) httpx response; the connection is already released."""
    status_code: int
    headers: Any
    url: str
    text: str
    too_large: bool = False


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
                 client: httpx.AsyncClient | None = None,
                 url_validator: Callable[[str], str] | None = None,
                 resolve_dns: bool | None = None):
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
        # DNS is resolved for the production client; an injected client owns its own transport.
        self.url_validator = url_validator or host_validator(
            OPTIMUM_HOSTS, resolve_dns=(client is None) if resolve_dns is None else resolve_dns)
        self.client = client or httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT}, timeout=self.policy.request_timeout_seconds,
            follow_redirects=False,
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
            self.metrics.mark_deadline_exceeded()
            raise MaxRunDurationExceeded("MAX_RUN_DURATION_EXCEEDED")

    async def _send(self, url: str):
        if not isinstance(self.client, httpx.AsyncClient):
            # Injected test doubles expose get() with a ready text.  Pass the
            # timeout per real request too: the policy wins over client defaults.
            return await self.client.get(url, timeout=self.policy.request_timeout_seconds)
        request = self.client.build_request("GET", url, timeout=self.policy.request_timeout_seconds)
        response = await self.client.send(request, stream=True, follow_redirects=False)
        try:
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > MAX_RESPONSE_BYTES:
                    # Reported after the resilience loop: an oversized body is not retried.
                    return _BoundedResponse(response.status_code, response.headers, str(response.url), "", True)
            text = bytes(body).decode(response.encoding or "utf-8", errors="replace")
            return _BoundedResponse(response.status_code, response.headers, str(response.url), text)
        finally:
            await response.aclose()

    async def get(self, url: str) -> FetchResult:
        self.ensure_within_deadline()
        async with self.semaphore:
            current = self.url_validator(url)
            total_attempts = 0
            for _ in range(MAX_REDIRECTS + 1):
                self.ensure_within_deadline()
                target = current
                response, attempts = await async_request_with_resilience(
                    lambda: self._send(target),
                    self.policy, self.limiter,
                    clock=self.clock, metrics=self.metrics, breaker=self.breaker,
                    deadline=self.deadline,
                )
                total_attempts += attempts
                if getattr(response, "too_large", False):
                    raise ResponseTooLargeError(f"response body exceeds {MAX_RESPONSE_BYTES} bytes")
                headers = getattr(response, "headers", None) or {}
                location = headers.get("location") or headers.get("Location")
                if int(response.status_code) in REDIRECT_STATUSES and location:
                    # Validate every hop before any I/O: host allowlist, ports 80/443, public address.
                    current = self.url_validator(urljoin(target, location))
                    continue
                return FetchResult(url, target, response.status_code, response.text, total_attempts)
            raise UnsafeUrlError("Слишком много перенаправлений")
