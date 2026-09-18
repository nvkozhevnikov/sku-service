from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin

import requests

from .models import FetchRecord
from .control_plane.rate_control import (
    CircuitBreaker, DomainRateLimiter, DomainRatePolicy, RequestMetrics, SystemClock,
    request_with_resilience,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)


class HttpTransport:
    """Direct HTTP transport with shared rate limiting and redirect evidence."""

    def __init__(
        self,
        *,
        user_agent: str = "UniversalSupplier/Stage3B (+catalog ingestion; low rate)",
        connect_timeout: float = 10,
        read_timeout: float = 40,
        min_interval_seconds: float = 0.20,
        retry_policy: RetryPolicy | None = None,
        session: requests.Session | None = None,
        domain_policy: DomainRatePolicy | None = None,
        clock=None,
        random_uniform=None,
        deadline: float | None = None,
    ) -> None:
        self.session = session or requests.Session()
        self.session.trust_env = False
        self.session.headers.update({"User-Agent": user_agent, "Accept": "text/html,application/xml;q=0.9,*/*;q=0.8"})
        self.retry = retry_policy or RetryPolicy()
        # A policy supplied by the orchestrator is already the immutable job
        # snapshot.  Keep that exact object all the way to the request layer;
        # rebuilding it here would silently replace configured fields with
        # constructor defaults.
        if domain_policy is not None:
            self.policy = domain_policy
            self.timeout: float | tuple[float, float] = self.policy.request_timeout_seconds
        else:
            # Legacy callers can continue to use the Stage 3B arguments.
            self.policy = DomainRatePolicy(
                max_concurrency=4,
                min_request_interval_seconds=min_interval_seconds,
                request_timeout_seconds=read_timeout,
                max_attempts=self.retry.max_attempts,
                backoff_base_seconds=self.retry.backoff_seconds,
            )
            self.timeout = (connect_timeout, self.policy.request_timeout_seconds)
        self.min_interval = self.policy.min_request_interval_seconds
        self.clock = clock or SystemClock()
        self.deadline = deadline
        self.metrics = RequestMetrics()
        self.limiter = DomainRateLimiter(
            self.policy, clock=self.clock, random_uniform=random_uniform,
        )
        self.breaker = CircuitBreaker(self.policy, clock=self.clock, metrics=self.metrics)

    def _rate_limit(self) -> None:
        self.limiter.wait(self.metrics)

    def get(self, url: str, *, deadline: float | None = None) -> FetchRecord:
        started = self.clock.monotonic()
        effective_deadline = self.deadline if deadline is None else deadline
        response, attempts = request_with_resilience(
            lambda: self.session.get(url, timeout=self.timeout, allow_redirects=True),
            self.policy, self.limiter, clock=self.clock, metrics=self.metrics, breaker=self.breaker,
            deadline=effective_deadline,
        )
        return FetchRecord(
            requested_url=url, final_url=response.url, status_code=response.status_code,
            redirects=tuple(item.url for item in response.history), attempts=attempts,
            elapsed_ms=round((self.clock.monotonic() - started) * 1000), text=response.text,
        )

    def get_validated(self, url: str, validator, *, deadline: float | None = None,
                      max_redirects: int = 5) -> FetchRecord:
        """Follow redirects manually so every target is validated before I/O."""
        started = self.clock.monotonic()
        effective_deadline = self.deadline if deadline is None else deadline
        current = validator(url)
        redirects: list[str] = []
        total_attempts = 0
        for _ in range(max_redirects + 1):
            response, attempts = request_with_resilience(
                lambda: self.session.get(current, timeout=self.timeout, allow_redirects=False),
                self.policy, self.limiter, clock=self.clock, metrics=self.metrics,
                breaker=self.breaker, deadline=effective_deadline,
            )
            total_attempts += attempts
            if response.is_redirect or response.is_permanent_redirect:
                location = response.headers.get("Location")
                if not location:
                    raise ValueError("Redirect без Location")
                redirects.append(current)
                current = validator(urljoin(current, location))
                continue
            return FetchRecord(
                requested_url=url, final_url=current, status_code=response.status_code,
                redirects=tuple(redirects), attempts=total_attempts,
                elapsed_ms=round((self.clock.monotonic() - started) * 1000), text=response.text,
            )
        raise ValueError("Слишком много перенаправлений")
