from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import threading

import pytest

from universal_supplier.control_plane.rate_control import (
    CircuitOpen, DomainRatePolicy, MaxRunDurationExceeded,
)
from universal_supplier.crawl import run_partner_st_live
from universal_supplier.http import HttpTransport
from universal_supplier.models import DiscoveryResult, ProductCard


class FakeClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []
        self._lock = threading.Lock()

    def monotonic(self) -> float:
        with self._lock:
            return self.value

    def sleep(self, seconds: float) -> None:
        with self._lock:
            self.sleeps.append(seconds)
            self.value += seconds

    def advance(self, seconds: float) -> None:
        with self._lock:
            self.value += seconds


class FakeResponse:
    def __init__(self, status_code: int = 200, *, headers: dict | None = None,
                 url: str = "https://partner-st.ru/product/1", text: str = "ok") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text
        self.history: list[FakeResponse] = []


class FakeSession:
    def __init__(self, responses, *, clock: FakeClock | None = None,
                 advance_per_call: float = 0.0) -> None:
        self.responses = list(responses)
        self.clock = clock
        self.advance_per_call = advance_per_call
        self.headers: dict[str, str] = {}
        self.trust_env = True
        self.calls: list[tuple[str, dict]] = []
        self._lock = threading.Lock()

    def get(self, url: str, **kwargs):
        with self._lock:
            self.calls.append((url, kwargs))
            response = self.responses.pop(0)
        if self.clock is not None and self.advance_per_call:
            self.clock.advance(self.advance_per_call)
        if isinstance(response, Exception):
            raise response
        return response


def policy(**overrides) -> DomainRatePolicy:
    values = {
        "max_concurrency": 1,
        "min_request_interval_seconds": 0.0,
        "jitter_min_seconds": 0.0,
        "jitter_max_seconds": 0.0,
        "request_timeout_seconds": 7.5,
        "max_attempts": 3,
        "backoff_base_seconds": 0.5,
        "backoff_max_seconds": 8.0,
        "respect_retry_after": True,
        "consecutive_error_threshold": 5,
        "cooldown_seconds": 30.0,
        "max_run_duration_seconds": 60.0,
    }
    values.update(overrides)
    return DomainRatePolicy(**values)


def card(url: str, position: int = 1) -> ProductCard:
    return ProductCard(
        supplier_code="partner_st",
        external_id=str(position),
        sku=f"SKU-{position}",
        name=f"Partner product {position}",
        requested_url=url,
        final_url=url,
        canonical_url=url,
        http_status=200,
        redirect_urls=(),
        price=Decimal("1"),
        old_price=None,
        currency="RUB",
        availability_raw="InStock",
        availability_normalized="in_stock",
        quantity=None,
        description_text="",
        description_html="",
    )


class FakeAdapter:
    base_url = "https://partner-st.ru/"

    def __init__(self, count: int = 1) -> None:
        self.urls = tuple(f"https://partner-st.ru/product/{value}" for value in range(1, count + 1))

    def discover(self, transport) -> DiscoveryResult:
        return DiscoveryResult(
            "https://partner-st.ru/sitemap.xml", (), (), self.urls,
        )

    def parse_product(self, fetched) -> ProductCard:
        position = int(fetched.requested_url.rstrip("/").rsplit("/", 1)[-1])
        return card(fetched.requested_url, position)


def make_transport(session: FakeSession, seen: dict | None = None):
    def factory(**kwargs) -> HttpTransport:
        if seen is not None:
            seen.update(kwargs)
        return HttpTransport(session=session, **kwargs)
    return factory


def test_partner_full_policy_propagation():
    clock = FakeClock()
    configured = policy(
        max_concurrency=2,
        min_request_interval_seconds=0.2,
        jitter_min_seconds=0.3,
        jitter_max_seconds=0.3,
        max_attempts=2,
        backoff_base_seconds=2.0,
        backoff_max_seconds=3.0,
        respect_retry_after=False,
        consecutive_error_threshold=4,
        cooldown_seconds=17.0,
        max_run_duration_seconds=99.0,
    )
    session = FakeSession([FakeResponse(), FakeResponse()])
    seen: dict = {}

    result = run_partner_st_live(
        baseline=None,
        # Deliberately conflicting legacy values prove they do not rebuild a
        # supplied job policy.
        workers=9,
        interval=9.0,
        rate_policy=configured,
        clock=clock,
        random_uniform=lambda low, high: low,
        transport_factory=make_transport(session, seen),
        adapter=FakeAdapter(2),
    )

    assert seen["domain_policy"] is configured
    assert seen["clock"] is clock
    assert result.requested_rate_policy == configured.snapshot()
    assert result.effective_rate_policy == configured.snapshot()
    assert result.requested_rate_policy == result.effective_rate_policy
    assert len(result.cards) == 2
    assert result.request_metrics["requests_total"] == 2
    assert result.request_metrics["http_2xx"] == 2


def test_partner_max_concurrency_controls_real_worker_count():
    configured = policy(max_concurrency=2, min_request_interval_seconds=0)
    barrier = threading.Barrier(2)

    class ConcurrencyTransport:
        def __init__(self) -> None:
            from universal_supplier.control_plane.rate_control import RequestMetrics
            self.metrics = RequestMetrics()
            self.active = 0
            self.max_active = 0
            self.lock = threading.Lock()

        def get(self, url: str):
            from universal_supplier.models import FetchRecord
            with self.lock:
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            barrier.wait(timeout=2)
            with self.lock:
                self.active -= 1
            self.metrics.record_status(200, 0)
            return FetchRecord(url, url, 200, (), 1, 0, "ok")

    transport = ConcurrencyTransport()
    result = run_partner_st_live(
        baseline=None,
        rate_policy=configured,
        transport_factory=lambda **kwargs: transport,
        adapter=FakeAdapter(2),
    )
    assert len(result.cards) == 2
    assert transport.max_active == configured.max_concurrency == 2


def test_partner_custom_timeout_reaches_actual_requests_session():
    configured = policy(request_timeout_seconds=7.5, max_attempts=1)
    session = FakeSession([FakeResponse()])
    transport = HttpTransport(session=session, domain_policy=configured, clock=FakeClock())
    transport.get("https://partner-st.ru/product/1")
    assert transport.policy is configured
    assert session.calls[0][1]["timeout"] == 7.5


@pytest.mark.parametrize(
    ("max_attempts", "expected_status", "expected_calls"),
    ((1, 503, 1), (2, 200, 2)),
)
def test_partner_custom_attempts(max_attempts: int, expected_status: int, expected_calls: int):
    configured = policy(max_attempts=max_attempts, backoff_base_seconds=0)
    session = FakeSession([FakeResponse(503), FakeResponse(200)])
    result = HttpTransport(session=session, domain_policy=configured, clock=FakeClock()).get(
        "https://partner-st.ru/product/1"
    )
    assert result.status_code == expected_status
    assert result.attempts == expected_calls
    assert len(session.calls) == expected_calls


def test_partner_custom_backoff_is_bounded_by_policy():
    clock = FakeClock()
    configured = policy(
        max_attempts=3,
        backoff_base_seconds=2.0,
        backoff_max_seconds=3.0,
    )
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(200)])
    result = HttpTransport(session=session, domain_policy=configured, clock=clock).get(
        "https://partner-st.ru/product/1"
    )
    assert result.attempts == 3
    assert clock.sleeps == [2.0, 3.0]


def test_partner_custom_jitter_is_part_of_shared_pacing():
    clock = FakeClock()
    configured = policy(
        min_request_interval_seconds=0.2,
        jitter_min_seconds=0.3,
        jitter_max_seconds=0.3,
        max_attempts=1,
    )
    session = FakeSession([FakeResponse(), FakeResponse()])
    transport = HttpTransport(
        session=session,
        domain_policy=configured,
        clock=clock,
        random_uniform=lambda low, high: 0.3,
    )
    transport.get("https://partner-st.ru/product/1")
    transport.get("https://partner-st.ru/product/2")
    assert clock.sleeps == [0.5]
    assert transport.metrics.rate_limit_waits == 1
    assert transport.metrics.rate_limit_wait_seconds == pytest.approx(0.5)


def test_partner_custom_circuit_breaker_uses_threshold_and_cooldown():
    clock = FakeClock()
    configured = policy(
        max_attempts=3,
        backoff_base_seconds=0,
        consecutive_error_threshold=2,
        cooldown_seconds=17,
    )
    session = FakeSession([FakeResponse(503), FakeResponse(503), FakeResponse(200)])
    transport = HttpTransport(session=session, domain_policy=configured, clock=clock)
    with pytest.raises(CircuitOpen):
        transport.get("https://partner-st.ru/product/1")
    assert len(session.calls) == 2
    assert transport.breaker.open_until == pytest.approx(17)
    assert transport.metrics.circuit_breaker_trips == 1
    assert transport.metrics.circuit_breaker_cooldown_seconds == pytest.approx(17)


def test_partner_deadline_during_limiter_wait_starts_no_new_request():
    clock = FakeClock()
    configured = policy(
        min_request_interval_seconds=10,
        max_attempts=1,
        max_run_duration_seconds=5,
    )
    session = FakeSession([FakeResponse(), FakeResponse()])
    transport = HttpTransport(
        session=session,
        domain_policy=configured,
        clock=clock,
        deadline=5,
        random_uniform=lambda low, high: 0,
    )
    transport.get("https://partner-st.ru/product/1")
    with pytest.raises(MaxRunDurationExceeded, match="MAX_RUN_DURATION_EXCEEDED"):
        transport.get("https://partner-st.ru/product/2")
    assert len(session.calls) == 1
    assert clock.value == pytest.approx(5)
    assert transport.metrics.max_run_duration_exceeded is True
    assert transport.metrics.requests_skipped_deadline == 1


def test_partner_deadline_during_backoff_starts_no_retry_attempt():
    clock = FakeClock()
    configured = policy(
        max_attempts=2,
        backoff_base_seconds=10,
        backoff_max_seconds=10,
        max_run_duration_seconds=5,
    )
    session = FakeSession([FakeResponse(503), FakeResponse(200)])
    transport = HttpTransport(
        session=session,
        domain_policy=configured,
        clock=clock,
        deadline=5,
    )
    with pytest.raises(MaxRunDurationExceeded, match="MAX_RUN_DURATION_EXCEEDED"):
        transport.get("https://partner-st.ru/product/1")
    assert len(session.calls) == 1
    assert clock.value == pytest.approx(5)
    assert transport.metrics.requests_total == 1
    assert transport.metrics.retry_count == 1
    assert transport.metrics.backoff_wait_seconds == pytest.approx(5)
    assert transport.metrics.max_run_duration_exceeded is True


def test_respect_retry_after_true():
    clock = FakeClock()
    configured = policy(
        max_attempts=2,
        respect_retry_after=True,
        backoff_base_seconds=2,
        backoff_max_seconds=3,
    )
    session = FakeSession([
        FakeResponse(429, headers={"Retry-After": "11"}),
        FakeResponse(200),
    ])
    transport = HttpTransport(session=session, domain_policy=configured, clock=clock)
    transport.get("https://partner-st.ru/product/1")
    assert clock.sleeps == [11]
    assert transport.metrics.retry_count == 1
    assert transport.metrics.http_429 == 1


def test_respect_retry_after_false():
    clock = FakeClock()
    configured = policy(
        max_attempts=2,
        respect_retry_after=False,
        backoff_base_seconds=2,
        backoff_max_seconds=3,
    )
    session = FakeSession([
        FakeResponse(429, headers={"Retry-After": "11"}),
        FakeResponse(200),
    ])
    HttpTransport(session=session, domain_policy=configured, clock=clock).get(
        "https://partner-st.ru/product/1"
    )
    assert clock.sleeps == [2]


def test_partner_request_metrics_cover_status_and_network_error_buckets():
    configured = policy(max_attempts=1, consecutive_error_threshold=20)
    clock = FakeClock()
    session = FakeSession([
        FakeResponse(200), FakeResponse(302), FakeResponse(404), FakeResponse(500),
        FakeResponse(429), RuntimeError("offline"),
    ], clock=clock, advance_per_call=0.25)
    transport = HttpTransport(session=session, domain_policy=configured, clock=clock)
    for position in range(5):
        transport.get(f"https://partner-st.ru/product/{position}")
    with pytest.raises(RuntimeError, match="offline"):
        transport.get("https://partner-st.ru/product/network-error")
    metrics = transport.metrics.as_metadata()
    assert metrics["requests_total"] == 6
    assert metrics["http_2xx"] == 1
    assert metrics["http_3xx"] == 1
    assert metrics["http_4xx"] == 2
    assert metrics["http_5xx"] == 1
    assert metrics["http_429"] == 1
    assert metrics["network_errors"] == 1
    assert metrics["response_time_total_seconds"] == pytest.approx(1.5)
    assert metrics["average_response_time_seconds"] == pytest.approx(0.25)


def test_partner_max_run_duration_stops_scheduling_new_product_requests():
    clock = FakeClock()
    configured = policy(
        max_concurrency=1,
        max_attempts=1,
        max_run_duration_seconds=5,
    )
    session = FakeSession(
        [FakeResponse(url="https://partner-st.ru/product/1")],
        clock=clock,
        advance_per_call=6,
    )
    result = run_partner_st_live(
        baseline=None,
        rate_policy=configured,
        clock=clock,
        transport_factory=make_transport(session),
        adapter=FakeAdapter(3),
    )

    assert len(session.calls) == 1
    assert len(result.cards) == 1
    assert result.termination_reason == "MAX_RUN_DURATION_EXCEEDED"
    assert any(error.error_type == "MAX_RUN_DURATION_EXCEEDED" for error in result.errors)
    assert result.discovery_metadata["max_run_duration_exceeded"] is True
    assert result.discovery_metadata["unscheduled_product_count"] == 2
    assert result.requested_rate_policy == result.effective_rate_policy == configured.snapshot()
