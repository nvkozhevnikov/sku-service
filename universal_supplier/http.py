from __future__ import annotations

from dataclasses import dataclass
from typing import Callable
from urllib.parse import urljoin

import requests

from .http_capture import MAX_RESPONSE_BYTES, ResponseTooLargeError
from .models import FetchRecord
from .url_safety import UnsafeUrlError, validate_public_http_url
from .control_plane.rate_control import (
    CircuitBreaker, DomainRateLimiter, DomainRatePolicy, RequestMetrics, SystemClock,
    request_with_resilience,
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    backoff_seconds: float = 0.5
    retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504)


def _bounded_text(response) -> str:  # type: ignore[no-untyped-def]
    """Read a streamed body up to MAX_RESPONSE_BYTES, then decode it as requests does."""
    if not isinstance(response, requests.Response):
        return response.text  # injected test doubles carry a ready text
    try:
        body = bytearray()
        for chunk in response.iter_content(chunk_size=65536):
            body.extend(chunk)
            if len(body) > MAX_RESPONSE_BYTES:
                raise ResponseTooLargeError(f"response body exceeds {MAX_RESPONSE_BYTES} bytes")
        response._content = bytes(body)
    finally:
        response.close()
    return response.text


REDIRECT_STATUSES = frozenset({301, 302, 303, 307, 308})


def _is_redirect(response) -> bool:  # type: ignore[no-untyped-def]
    """As requests defines it: a redirect status *with* a Location header."""
    if isinstance(response, requests.Response):
        return response.is_redirect or response.is_permanent_redirect
    headers = getattr(response, "headers", None) or {}
    return (int(getattr(response, "status_code", 0)) in REDIRECT_STATUSES
            and bool(headers.get("Location") or headers.get("location")))


class HttpTransport:
    """Direct HTTP transport with shared rate limiting and redirect evidence.

    Redirects are never followed by ``requests``: every hop goes through
    ``url_validator`` before any I/O.  Crawlers bind the validator to their
    supplier hosts; the default only refuses non-public targets.
    """

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
        url_validator: Callable[[str], str] | None = None,
    ) -> None:
        self.url_validator = url_validator or validate_public_http_url
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
        return self.get_validated(url, self.url_validator, deadline=deadline)

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
                lambda: self.session.get(current, timeout=self.timeout, allow_redirects=False, stream=True),
                self.policy, self.limiter, clock=self.clock, metrics=self.metrics,
                breaker=self.breaker, deadline=effective_deadline,
            )
            total_attempts += attempts
            if _is_redirect(response):
                headers = getattr(response, "headers", None) or {}
                location = headers.get("Location") or headers.get("location")
                if isinstance(response, requests.Response):
                    response.close()
                redirects.append(current)
                current = validator(urljoin(current, location))
                continue
            return FetchRecord(
                requested_url=url, final_url=current, status_code=response.status_code,
                redirects=tuple(redirects), attempts=total_attempts,
                elapsed_ms=round((self.clock.monotonic() - started) * 1000), text=_bounded_text(response),
            )
        raise UnsafeUrlError("Слишком много перенаправлений")
