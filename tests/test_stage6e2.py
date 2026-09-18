from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_supplier.control_plane import adapter_registry as adapter_module
from universal_supplier.control_plane.adapter_registry import AdapterRegistry
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.stage6e import MissingPolicy, MissingState, deactivation_was_suppressed, observe_missing
from universal_supplier.state import DiscoveryHealth

import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))
from test_stage6c1_integration import RecordingRepository, _install_recording_repository, _runner_job


def test_custom_missing_policy_validation():
    with pytest.raises(ValueError):
        MissingPolicy(warning_at=0, candidate_at=3)
    with pytest.raises(ValueError):
        MissingPolicy(warning_at=3, candidate_at=3)
    assert MissingPolicy.from_mapping(None) == MissingPolicy()


def test_custom_missing_policy_states():
    policy = MissingPolicy(warning_at=3, candidate_at=5)
    states = []
    current = MissingState()
    for _ in range(5):
        current = observe_missing(current, "NOT_DISCOVERED", policy=policy)
        states.append((current.consecutive_confirmed, current.state))
    assert states == [
        (1, "WATCH"), (2, "WATCH"), (3, "WARNING"), (4, "WARNING"), (5, "MISSING_CANDIDATE"),
    ]


def test_default_missing_policy_still_uses_watch_warning_candidate():
    one = observe_missing(MissingState(), "HTTP_404")
    two = observe_missing(one, "NOT_IN_SITEMAP")
    three = observe_missing(two, "HTTP_410")
    assert (one.state, two.state, three.state) == ("WATCH", "WARNING", "MISSING_CANDIDATE")


@pytest.mark.parametrize(("deadline", "critical", "expected"), [
    (False, True, True),
    (True, False, True),
    (True, True, True),
    (False, False, False),
])
def test_deactivation_suppressed_metadata_truth(deadline, critical, expected):
    assert deactivation_was_suppressed(deactivation_allowed=not deadline, anomaly_critical=critical) is expected


def test_finish_metadata_does_not_write_deactivation_suppressed():
    source = (ROOT / "universal_supplier/control_plane/adapter_registry.py").read_text(encoding="utf-8")
    partner = source[source.index("def run_partner_st_job"):source.index("def run_optimum_job")]
    optimum = source[source.index("def run_optimum_job"):]
    assert '"deactivation_suppressed"' not in partner
    assert '"deactivation_suppressed": deadline_exceeded' not in optimum
    assert "if key != \"deactivation_suppressed\"" in source
    ingest = (ROOT / "universal_supplier/postgres.py").read_text(encoding="utf-8")
    assert ingest.count('"deactivation_suppressed"') == 1


def test_anomaly_adapter_result_partial(monkeypatch):
    _install_recording_repository(monkeypatch)
    monkeypatch.setattr(adapter_module, "_post_ingest_identity", lambda *args, **kwargs: {"status": "succeeded"})

    def fake_crawl(*, baseline, rate_policy):
        card = SimpleNamespace(raw_data={"schema_signals": ["gallery_missing"]})
        return SimpleNamespace(
            cards=(card,), finished_at="2026-09-16T00:00:00+00:00",
            discovered_urls=("https://partner-st.ru/a",), errors=(),
            health=DiscoveryHealth(101, 101, 1.0, False, True),
            effective_rate_policy=rate_policy.snapshot(), request_metrics={},
            discovery_metadata={}, termination_reason=None,
        )

    monkeypatch.setattr(adapter_module, "run_partner_st_live", fake_crawl)
    outcome = adapter_module.run_partner_st_job(_runner_job("partner_st", "partner_st", {}, baseline=101), object())
    assert outcome["status"] == "partial"
    assert RecordingRepository.instances[0].ingest_kwargs["schema_signals"] == ["gallery_missing"]


def test_anomaly_crawl_job_partial(monkeypatch):
    _install_recording_repository(monkeypatch)
    monkeypatch.setattr(adapter_module, "_post_ingest_identity", lambda *args, **kwargs: {"status": "succeeded"})
    monkeypatch.setattr(adapter_module, "run_partner_st_live", lambda **kwargs: SimpleNamespace(
        cards=(SimpleNamespace(raw_data={"schema_signals": ["json_ld_changed"]}),),
        finished_at="2026-09-16T00:00:00+00:00", discovered_urls=("https://partner-st.ru/a",), errors=(),
        health=DiscoveryHealth(101, 101, 1.0, False, True),
        effective_rate_policy=kwargs["rate_policy"].snapshot(), request_metrics={},
        discovery_metadata={}, termination_reason=None,
    ))
    completed = []

    class Queue:
        lease_seconds = 30
        def complete(self, job_id, status, **kwargs):
            completed.append((job_id, status, kwargs.get("crawl_run_id")))
        def heartbeat(self, *args):
            return True
        def recover_stale(self):
            return 0

    class Lease:
        acquired = True
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False

    job = CrawlJob(9, 1, "partner_st", "partner_st", "https://partner-st.ru/", "manual", "full", "running",
                   datetime.now(timezone.utc), {"rate_settings": {}, "crawl_settings": {"baseline_discovered_count": 101}})
    registry = AdapterRegistry()
    registry.register("partner_st", adapter_module.run_partner_st_job)
    Orchestrator(object(), queue=Queue(), registry=registry, lease_factory=lambda domain: Lease())._run_job(job)
    assert completed == [(9, "partial", 101)]


def test_healthy_adapter_result_is_succeeded(monkeypatch):
    _install_recording_repository(monkeypatch)
    monkeypatch.setattr(adapter_module, "_post_ingest_identity", lambda *args, **kwargs: {"status": "succeeded"})
    monkeypatch.setattr(adapter_module, "run_partner_st_live", lambda **kwargs: SimpleNamespace(
        cards=(SimpleNamespace(raw_data={}),), finished_at="2026-09-16T00:00:00+00:00",
        discovered_urls=("https://partner-st.ru/a",), errors=(),
        health=DiscoveryHealth(101, 101, 1.0, False, True),
        effective_rate_policy=kwargs["rate_policy"].snapshot(), request_metrics={},
        discovery_metadata={}, termination_reason=None,
    ))
    outcome = adapter_module.run_partner_st_job(_runner_job("partner_st", "partner_st", {}, baseline=101), object())
    assert outcome["status"] == "succeeded"


def test_packaged_env_example_present():
    from package_stage6e2 import forbidden
    assert forbidden(Path(".env"))
    assert not forbidden(Path(".env.example"))
    root = Path(__import__("os").environ.get("PACKAGED_ROOT") or ROOT)
    assert (root / ".env.example").is_file()
    if __import__("os").environ.get("PACKAGED_ROOT"):
        assert not (root / ".env").exists()


def test_packaged_pytest_collection():
    root = Path(__import__("os").environ.get("PACKAGED_ROOT") or ROOT)
    assert (root / ".env.example").is_file()
    assert (root / "docker-compose.yml").is_file()
    assert (root / "tests" / "test_stage3d.py").is_file()
    text = (root / "tests" / "test_stage3d.py").read_text(encoding="utf-8")
    assert 'ROOT / ".env.example"' in text


def test_missing_policy_advanced_supplier_settings():
    template = (ROOT / "universal_supplier/control_plane/templates/supplier_detail.html").read_text(encoding="utf-8")
    assert "/missing-policy" in template and "warning_at" in template and "candidate_at" in template
    web = (ROOT / "universal_supplier/control_plane/web.py").read_text(encoding="utf-8")
    assert "save_missing_policy" in web


def test_supplier_detail_empty_crawl_settings_renders_defaults():
    from fastapi.testclient import TestClient
    from universal_supplier.control_plane.admin_store import InMemoryAdminStore
    from universal_supplier.control_plane.queue import InMemoryJobQueue
    from universal_supplier.control_plane.web import create_app
    from test_stage6d2 import RenderStore
    store = RenderStore()
    store.supplier["crawl_settings"] = {}
    client = TestClient(create_app(store=store, queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(), auth_required=False))
    response = client.get("/suppliers/1")
    assert response.status_code == 200
    assert 'name="count_ratio_min" value="0.6"' in response.text or 'value="0.60"' in response.text
    assert 'name="warning_at" value="2"' in response.text
    assert 'name="candidate_at" value="3"' in response.text


def test_queued_job_freezes_missing_and_quality_policies(monkeypatch):
    _install_recording_repository(monkeypatch)
    monkeypatch.setattr(adapter_module, "_post_ingest_identity", lambda *args, **kwargs: {"status": "succeeded"})
    monkeypatch.setattr(adapter_module, "run_partner_st_live", lambda **kwargs: SimpleNamespace(
        cards=(SimpleNamespace(raw_data={}),), finished_at="2026-09-16T00:00:00+00:00",
        discovered_urls=("https://partner-st.ru/a",), errors=(),
        health=DiscoveryHealth(1, 1, 1.0, False, True),
        effective_rate_policy=kwargs["rate_policy"].snapshot(), request_metrics={},
        discovery_metadata={}, termination_reason=None,
    ))
    frozen = {"missing_policy": {"warning_at": 3, "candidate_at": 5},
              "data_quality_policy": {"count_ratio_min": 0.9, "fill_rate_drop": 0.1, "fill_rate_min": 0.05}}
    first = _runner_job("partner_st", "partner_st", {}, baseline=1)
    first = CrawlJob(first.id, first.supplier_id, first.supplier_code, first.adapter_name, first.base_url,
                     first.trigger_type, first.crawl_type, first.status, first.scheduled_for,
                     {"rate_settings": {}, "crawl_settings": dict(frozen)})
    adapter_module.run_partner_st_job(first, object())
    assert RecordingRepository.instances[0].ingest_kwargs["crawl_settings"]["missing_policy"]["warning_at"] == 3
    updated = dict(frozen); updated["missing_policy"] = {"warning_at": 4, "candidate_at": 6}
    second = CrawlJob(2, 1, "partner_st", "partner_st", "https://partner_st.invalid", "manual", "full", "running",
                      datetime.now(timezone.utc), {"rate_settings": {}, "crawl_settings": updated})
    adapter_module.run_partner_st_job(second, object())
    assert RecordingRepository.instances[0].ingest_kwargs["crawl_settings"]["missing_policy"]["warning_at"] == 3
    assert RecordingRepository.instances[-1].ingest_kwargs["crawl_settings"]["missing_policy"]["warning_at"] == 4
