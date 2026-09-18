from __future__ import annotations

import asyncio

import pytest

from universal_supplier.adapters import optimum as optimum_adapter
from universal_supplier.adapters.optimum_source.fetch import HttpFetcher
from universal_supplier.adapters.optimum_source.models import DiscoveryRecord
from universal_supplier.control_plane.rate_control import (
    CircuitOpen,
    DomainRatePolicy,
)


class FakeAsyncClock:
    def __init__(self) -> None:
        self.value = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.value

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.value += seconds

    def advance(self, seconds: float) -> None:
        self.value += seconds


class FakeResponse:
    def __init__(self, status_code: int = 200, *, headers: dict | None = None,
                 url: str = "https://optimum.su/catalog/probe/", text: str = "ok") -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.url = url
        self.text = text


class FakeAsyncClient:
    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[str, dict]] = []
        self.closed = False

    async def get(self, url: str, **kwargs):
        self.calls.append((url, kwargs))
        response = self.responses.pop(0)
        if isinstance(response, BaseException):
            raise response
        return response

    async def aclose(self) -> None:
        self.closed = True


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
        "consecutive_error_threshold": 20,
        "cooldown_seconds": 30.0,
        "max_run_duration_seconds": 60.0,
    }
    values.update(overrides)
    return DomainRatePolicy(**values)


async def fetch_once(fetcher: HttpFetcher, url: str = "https://optimum.su/catalog/probe/"):
    try:
        return await fetcher.get(url)
    finally:
        await fetcher.close()


def test_optimum_full_policy_reaches_real_transport_and_truthful_metadata(monkeypatch):
    clock = FakeAsyncClock()
    configured = policy(
        max_concurrency=2,
        min_request_interval_seconds=0.2,
        jitter_min_seconds=0.3,
        jitter_max_seconds=0.4,
        request_timeout_seconds=7.5,
        max_attempts=2,
        backoff_base_seconds=2.0,
        backoff_max_seconds=3.0,
        respect_retry_after=False,
        consecutive_error_threshold=4,
        cooldown_seconds=17.0,
        max_run_duration_seconds=99.0,
    )
    client = FakeAsyncClient([FakeResponse()])

    async def discover_with_real_transport(self):
        fetched = await self.fetch_product("https://optimum.su/catalog/probe/")
        assert fetched.status_code == 200
        return [], [], []

    monkeypatch.setattr(
        optimum_adapter.SourceAdapter,
        "discover_products",
        discover_with_real_transport,
    )
    result = asyncio.run(optimum_adapter.run_optimum_live(
        rate_policy=configured,
        # Conflicting legacy knobs must not rebuild a supplied full policy.
        concurrency=9,
        delay=9.0,
        clock=clock,
        random_uniform=lambda low, high: low,
        client=client,
    ))

    assert client.calls == [(
        "https://optimum.su/catalog/probe/",
        {"timeout": 7.5},
    )]
    assert client.closed is True
    assert result.metadata["requested_rate_policy"] == configured.snapshot()
    assert result.metadata["effective_rate_policy"] == configured.snapshot()
    assert result.metadata["requested_policy_equals_effective_policy"] is True
    assert result.metadata["request_metrics"]["requests_total"] == 1
    assert result.metadata["request_metrics"]["http_2xx"] == 1


def test_optimum_custom_timeout_reaches_actual_async_client_call():
    configured = policy(request_timeout_seconds=7.5, max_attempts=1)
    client = FakeAsyncClient([FakeResponse()])
    fetcher = HttpFetcher(rate_policy=configured, clock=FakeAsyncClock(), client=client)

    result = asyncio.run(fetch_once(fetcher))

    assert result.status_code == 200
    assert result.attempt_count == 1
    assert client.calls[0][1]["timeout"] == 7.5


@pytest.mark.parametrize(
    ("max_attempts", "expected_status", "expected_calls"),
    ((1, 503, 1), (2, 200, 2)),
)
def test_optimum_custom_attempts_control_real_transport(
        max_attempts: int, expected_status: int, expected_calls: int):
    configured = policy(max_attempts=max_attempts, backoff_base_seconds=0)
    client = FakeAsyncClient([FakeResponse(503), FakeResponse(200)])
    fetcher = HttpFetcher(rate_policy=configured, clock=FakeAsyncClock(), client=client)

    result = asyncio.run(fetch_once(fetcher))

    assert result.status_code == expected_status
    assert result.attempt_count == expected_calls
    assert len(client.calls) == expected_calls


def test_optimum_custom_backoff_is_bounded_by_policy():
    clock = FakeAsyncClock()
    configured = policy(
        max_attempts=4,
        backoff_base_seconds=2.0,
        backoff_max_seconds=3.0,
    )
    client = FakeAsyncClient([
        FakeResponse(503), FakeResponse(503), FakeResponse(503), FakeResponse(200),
    ])
    fetcher = HttpFetcher(rate_policy=configured, clock=clock, client=client)

    result = asyncio.run(fetch_once(fetcher))

    assert result.attempt_count == 4
    assert clock.sleeps == [2.0, 3.0, 3.0]
    assert fetcher.metrics.backoff_waits == 3
    assert fetcher.metrics.backoff_wait_seconds == pytest.approx(8.0)


def test_optimum_custom_jitter_deterministically_controls_shared_pacing():
    clock = FakeAsyncClock()
    configured = policy(
        min_request_interval_seconds=0.2,
        jitter_min_seconds=0.3,
        jitter_max_seconds=0.3,
        max_attempts=1,
    )
    client = FakeAsyncClient([FakeResponse(), FakeResponse()])
    random_calls: list[tuple[float, float]] = []

    def deterministic_uniform(low: float, high: float) -> float:
        random_calls.append((low, high))
        return 0.3

    async def exercise():
        fetcher = HttpFetcher(
            rate_policy=configured,
            clock=clock,
            random_uniform=deterministic_uniform,
            client=client,
        )
        try:
            await fetcher.get("https://optimum.su/catalog/one/")
            await fetcher.get("https://optimum.su/catalog/two/")
            return fetcher.metrics
        finally:
            await fetcher.close()

    metrics = asyncio.run(exercise())

    assert clock.sleeps == [pytest.approx(0.5)]
    assert random_calls == [(0.3, 0.3), (0.3, 0.3)]
    assert metrics.rate_limit_waits == 1
    assert metrics.rate_limit_wait_seconds == pytest.approx(0.5)


def test_optimum_custom_circuit_threshold_and_cooldown_apply_to_real_transport():
    clock = FakeAsyncClock()
    configured = policy(
        max_attempts=3,
        backoff_base_seconds=0,
        consecutive_error_threshold=2,
        cooldown_seconds=17,
    )
    client = FakeAsyncClient([FakeResponse(503), FakeResponse(503), FakeResponse(200)])

    async def exercise():
        fetcher = HttpFetcher(rate_policy=configured, clock=clock, client=client)
        with pytest.raises(CircuitOpen):
            await fetcher.get("https://optimum.su/catalog/probe/")
        calls_while_open = len(client.calls)
        open_until = fetcher.breaker.open_until
        clock.advance(17)
        recovered = await fetcher.get("https://optimum.su/catalog/probe/")
        await fetcher.close()
        return fetcher, recovered, calls_while_open, open_until

    fetcher, recovered, calls_while_open, open_until = asyncio.run(exercise())

    assert calls_while_open == 2
    assert open_until == pytest.approx(17)
    assert recovered.status_code == 200
    assert fetcher.breaker.consecutive_failures == 0
    assert fetcher.metrics.circuit_breaker_trips == 1
    assert fetcher.metrics.circuit_breaker_cooldown_seconds == pytest.approx(17)


def test_optimum_respect_retry_after_true_uses_header_delay():
    clock = FakeAsyncClock()
    configured = policy(
        max_attempts=2,
        respect_retry_after=True,
        backoff_base_seconds=2,
        backoff_max_seconds=3,
    )
    client = FakeAsyncClient([
        FakeResponse(429, headers={"Retry-After": "11"}),
        FakeResponse(200),
    ])
    fetcher = HttpFetcher(rate_policy=configured, clock=clock, client=client)

    result = asyncio.run(fetch_once(fetcher))

    assert result.attempt_count == 2
    assert clock.sleeps == [11]
    assert fetcher.metrics.retry_after_waits == 1
    assert fetcher.metrics.retry_after_wait_seconds == pytest.approx(11)
    assert fetcher.metrics.backoff_waits == 0


def test_optimum_respect_retry_after_false_uses_bounded_backoff():
    clock = FakeAsyncClock()
    configured = policy(
        max_attempts=2,
        respect_retry_after=False,
        backoff_base_seconds=2,
        backoff_max_seconds=3,
    )
    client = FakeAsyncClient([
        FakeResponse(429, headers={"Retry-After": "11"}),
        FakeResponse(200),
    ])
    fetcher = HttpFetcher(rate_policy=configured, clock=clock, client=client)

    result = asyncio.run(fetch_once(fetcher))

    assert result.attempt_count == 2
    assert clock.sleeps == [2]
    assert fetcher.metrics.retry_after_waits == 0
    assert fetcher.metrics.backoff_waits == 1
    assert fetcher.metrics.backoff_wait_seconds == pytest.approx(2)


def test_optimum_request_metrics_cover_every_status_network_and_wait_bucket():
    configured = policy(max_attempts=1, consecutive_error_threshold=20)
    client = FakeAsyncClient([
        FakeResponse(200),
        FakeResponse(302),
        FakeResponse(404),
        FakeResponse(500),
        FakeResponse(429),
        RuntimeError("offline"),
    ])

    async def exercise():
        fetcher = HttpFetcher(
            rate_policy=configured,
            clock=FakeAsyncClock(),
            client=client,
        )
        for position in range(5):
            await fetcher.get(f"https://optimum.su/catalog/{position}/")
        with pytest.raises(RuntimeError, match="offline"):
            await fetcher.get("https://optimum.su/catalog/network-error/")
        await fetcher.close()
        return fetcher.metrics.as_metadata()

    metrics = asyncio.run(exercise())

    assert metrics == {
        "requests_total": 6,
        "http_2xx": 1,
        "http_3xx": 1,
        "http_4xx": 2,
        "http_5xx": 1,
        "http_429": 1,
        "network_errors": 1,
        "retry_count": 0,
        "response_time_total_seconds": 0.0,
        "rate_limit_waits": 0,
        "rate_limit_wait_seconds": 0.0,
        "circuit_breaker_trips": 0,
        "circuit_breaker_cooldown_seconds": 0.0,
        "retry_after_waits": 0,
        "retry_after_wait_seconds": 0.0,
        "backoff_waits": 0,
        "backoff_wait_seconds": 0.0,
        "max_run_duration_exceeded": False,
        "requests_skipped_deadline": 0,
        "last_failure_reason": "RuntimeError",
        "average_response_time_seconds": 0.0,
    }


def test_optimum_monotonic_max_run_cancels_pending_product_request(monkeypatch):
    clock = FakeAsyncClock()
    configured = policy(
        max_concurrency=2,
        max_attempts=1,
        max_run_duration_seconds=5,
    )
    records = [
        DiscoveryRecord(
            source_url=f"https://optimum.su/catalog/{position}/",
            canonical_url=f"https://optimum.su/catalog/{position}/",
            discovery_source="test",
            discovered_at="2026-09-15T00:00:00+00:00",
            collections=[],
        )
        for position in range(1, 4)
    ]

    class DeadlineClient:
        def __init__(self) -> None:
            self.calls: list[str] = []
            self.cancelled: list[str] = []
            self.closed = False

        async def get(self, url: str, **kwargs):
            self.calls.append(url)
            if url.endswith("/1/"):
                # Yield so request 2 is genuinely in flight before the
                # monotonic budget is consumed by request 1.
                await asyncio.sleep(0)
                clock.advance(6)
                return FakeResponse(404, url=url)
            if url.endswith("/2/"):
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    self.cancelled.append(url)
                    raise
            pytest.fail("request 3 must be rejected before the client call")

        async def aclose(self) -> None:
            self.closed = True

    client = DeadlineClient()

    async def fixed_discovery(self):
        return records, [], []

    monkeypatch.setattr(
        optimum_adapter.SourceAdapter,
        "discover_products",
        fixed_discovery,
    )
    result = asyncio.run(optimum_adapter.run_optimum_live(
        rate_policy=configured,
        clock=clock,
        random_uniform=lambda low, high: low,
        client=client,
    ))

    assert client.calls == [
        "https://optimum.su/catalog/1/",
        "https://optimum.su/catalog/2/",
    ]
    assert client.cancelled == ["https://optimum.su/catalog/2/"]
    assert client.closed is True
    assert len(result.rows) == 1
    assert result.rows[0]["classification"] == "STALE_404"
    assert result.metadata["deadline_exceeded"] is True
    assert result.metadata["max_run_duration_exceeded"] is True
    assert result.metadata["termination_reason"] == "MAX_RUN_DURATION_EXCEEDED"
    assert result.metadata["recommended_run_status"] == "partial"
    assert result.metadata["requested_policy_equals_effective_policy"] is True
    assert result.metadata["request_metrics"]["requests_total"] == 1
    assert result.metadata["request_metrics"]["requests_skipped_deadline"] == 1
    assert result.metadata["request_metrics"]["max_run_duration_exceeded"] is True
    assert result.metadata["request_metrics"]["last_failure_reason"] == "MAX_RUN_DURATION_EXCEEDED"
