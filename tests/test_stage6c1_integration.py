from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from universal_supplier.control_plane import adapter_registry as adapter_module
from universal_supplier.control_plane.adapter_registry import AdapterRegistry, default_adapter_registry
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import CrawlJob, InMemoryJobQueue
from universal_supplier.control_plane.rate_control import (
    DomainRatePolicy, OPTIMUM_DEFAULT_RATE_POLICY, PARTNER_ST_DEFAULT_RATE_POLICY,
)
from universal_supplier.state import DiscoveryHealth


@pytest.mark.parametrize("values", [
    {"max_concurrency": 0},
    {"min_request_interval_seconds": -0.1},
    {"jitter_min_seconds": -0.1},
    {"jitter_min_seconds": 2, "jitter_max_seconds": 1},
    {"request_timeout_seconds": 0},
    {"max_attempts": 0},
    {"backoff_base_seconds": 2, "backoff_max_seconds": 1},
    {"cooldown_seconds": -1},
    {"max_run_duration_seconds": 0},
])
def test_rate_policy_rejects_every_invalid_operator_value(values):
    with pytest.raises(ValueError):
        DomainRatePolicy.from_mapping(values)


def test_current_supplier_defaults_are_exact_and_complete():
    assert PARTNER_ST_DEFAULT_RATE_POLICY.snapshot() == {
        "max_concurrency": 4, "min_request_interval_seconds": .2,
        "jitter_min_seconds": 0, "jitter_max_seconds": .1,
        "request_timeout_seconds": 40, "max_attempts": 3,
        "backoff_base_seconds": .5, "backoff_max_seconds": 8,
        "respect_retry_after": True, "consecutive_error_threshold": 5,
        "cooldown_seconds": 30, "max_run_duration_seconds": 7200,
    }
    assert OPTIMUM_DEFAULT_RATE_POLICY.snapshot() == {
        **PARTNER_ST_DEFAULT_RATE_POLICY.snapshot(), "max_concurrency": 3,
        "min_request_interval_seconds": .15, "request_timeout_seconds": 35,
    }


def test_partial_snapshot_overlays_supplier_specific_baseline():
    partner = adapter_module._policy_from_snapshot(
        {"request_timeout_seconds": 7.5}, PARTNER_ST_DEFAULT_RATE_POLICY,
    )
    optimum = adapter_module._policy_from_snapshot(
        {"request_timeout_seconds": 7.5}, OPTIMUM_DEFAULT_RATE_POLICY,
    )
    assert partner.max_concurrency == 4
    assert partner.min_request_interval_seconds == pytest.approx(0.20)
    assert optimum.max_concurrency == 3
    assert optimum.min_request_interval_seconds == pytest.approx(0.15)
    assert partner.request_timeout_seconds == optimum.request_timeout_seconds == 7.5


def test_adapter_capabilities_are_full_only():
    registry = default_adapter_registry()
    assert registry.capabilities("partner_st").supported_crawl_types == frozenset({"full"})
    assert registry.capabilities("optimum").supported_crawl_types == frozenset({"full"})


def test_unsupported_crawl_type_fails_before_lock_or_runner_network_call():
    now = datetime.now(timezone.utc)
    job = CrawlJob(1, 1, "partner", "fake", "https://example.invalid", "manual", "discovery", "running", now, {})
    events = []
    class Queue:
        lease_seconds = 30
        def complete(self, job_id, status, **kwargs): events.append((job_id, status, kwargs.get("error_message")))
        def recover_stale(self): return 0
    registry = AdapterRegistry()
    registry.register("fake", lambda *_: events.append("NETWORK_CALLED"), supported_crawl_types={"full"})
    orchestrator = Orchestrator(None, queue=Queue(), registry=registry,
                                lease_factory=lambda _: events.append("LOCK_CALLED"))
    orchestrator._run_job(job)
    assert events == [(1, "failed", "UNSUPPORTED_CRAWL_TYPE: fake does not support discovery")]


def test_queued_settings_are_immutable_and_future_job_gets_updated_policy():
    queue = InMemoryJobQueue()
    mutable = {"rate_settings": {"request_timeout_seconds": 7.5}}
    first = queue.enqueue(1, trigger_type="manual", crawl_type="full", settings_snapshot=mutable)
    mutable["rate_settings"]["request_timeout_seconds"] = 12
    assert queue.jobs[0]["settings_snapshot"]["rate_settings"]["request_timeout_seconds"] == 7.5
    queue.jobs[0]["status"] = "succeeded"
    second = queue.enqueue(1, trigger_type="schedule", crawl_type="full", settings_snapshot=mutable)
    assert first.enqueued and second.enqueued
    assert queue.jobs[-1]["settings_snapshot"]["rate_settings"]["request_timeout_seconds"] == 12


class RecordingRepository:
    instances = []

    def __init__(self, config):
        self.config = config
        self.recorded_errors = []
        self.ingest_kwargs = None
        self.metadata = None
        self.failed = None
        self.__class__.instances.append(self)

    def start_crawl_run(self, **kwargs):
        self.started = kwargs
        return 101

    def record_crawl_error(self, run_id, **kwargs):
        self.recorded_errors.append((run_id, kwargs))

    def ingest(self, cards, observed_at, **kwargs):
        self.ingest_kwargs = kwargs
        self.run_status = "partial" if kwargs.get("error_count") or kwargs.get("schema_signals") else "succeeded"
        return SimpleNamespace(new_products=len(cards))

    def final_crawl_run_status(self, run_id):
        return getattr(self, "run_status", "succeeded")

    def fail_crawl_run(self, run_id, **kwargs):
        self.failed = (run_id, kwargs)

    def close(self):
        self.closed = True


def _runner_job(supplier_code: str, adapter_name: str, rate_settings: dict, *, baseline: int = 1):
    return CrawlJob(
        1, 1, supplier_code, adapter_name, f"https://{supplier_code}.invalid",
        "manual", "full", "running", datetime.now(timezone.utc),
        {"rate_settings": rate_settings, "crawl_settings": {"baseline_discovered_count": baseline}},
    )


def _install_recording_repository(monkeypatch):
    RecordingRepository.instances.clear()
    monkeypatch.setattr(adapter_module, "PostgresRepository", RecordingRepository)
    monkeypatch.setattr(
        adapter_module,
        "_finish_metadata",
        lambda repository, run_id, metadata: setattr(repository, "metadata", metadata),
    )


def test_partner_job_persists_actual_policy_and_suppresses_deactivation_on_deadline(monkeypatch):
    _install_recording_repository(monkeypatch)
    captured = {}

    def fake_crawl(*, baseline, rate_policy):
        captured["policy"] = rate_policy
        return SimpleNamespace(
            cards=(), finished_at="2026-09-15T00:00:00+00:00",
            discovered_urls=("https://partner.invalid/1",),
            errors=(SimpleNamespace(
                url="https://partner.invalid/", error_type="MAX_RUN_DURATION_EXCEEDED",
                message="deadline", http_status=None,
            ),),
            health=DiscoveryHealth(1, 1, 1.0, False, True),
            effective_rate_policy=rate_policy.snapshot(), request_metrics={"requests_total": 1},
            discovery_metadata={"max_run_duration_exceeded": True},
            termination_reason="MAX_RUN_DURATION_EXCEEDED",
        )

    monkeypatch.setattr(adapter_module, "run_partner_st_live", fake_crawl)
    outcome = adapter_module.run_partner_st_job(
        _runner_job("partner_st", "partner_st", {"request_timeout_seconds": 7.5}), object(),
    )
    repository = RecordingRepository.instances[0]
    assert captured["policy"].max_concurrency == 4
    assert captured["policy"].request_timeout_seconds == 7.5
    assert repository.ingest_kwargs["discovery_health"].deactivation_allowed is False
    assert repository.metadata["requested_effective_policy_match"] is True
    assert repository.metadata["termination_reason"] == "MAX_RUN_DURATION_EXCEEDED"
    assert outcome["status"] == "partial"


def test_optimum_job_records_deadline_and_suppresses_deactivation(monkeypatch):
    _install_recording_repository(monkeypatch)
    captured = {}

    def fake_crawl(*, rate_policy):
        captured["policy"] = rate_policy
        return SimpleNamespace(
            cards=(object(),), rows=(), finished_at="2026-09-15T00:00:00+00:00",
            metadata={
                "effective_rate_policy": rate_policy.snapshot(),
                "request_metrics": {"requests_total": 1},
                "max_run_duration_exceeded": True,
                "termination_reason": "MAX_RUN_DURATION_EXCEEDED",
            },
        )

    monkeypatch.setattr(adapter_module, "run_optimum_live_sync", fake_crawl)
    outcome = adapter_module.run_optimum_job(
        _runner_job("optimum", "optimum", {"request_timeout_seconds": 7.5}), object(),
    )
    repository = RecordingRepository.instances[0]
    assert captured["policy"].max_concurrency == 3
    assert captured["policy"].request_timeout_seconds == 7.5
    assert repository.ingest_kwargs["discovery_health"].deactivation_allowed is False
    assert repository.ingest_kwargs["error_count"] == 1
    assert repository.recorded_errors[0][1]["error_type"] == "MAX_RUN_DURATION_EXCEEDED"
    assert repository.metadata["requested_effective_policy_match"] is True
    assert outcome["status"] == "partial"
