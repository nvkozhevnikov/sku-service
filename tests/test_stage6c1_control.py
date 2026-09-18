from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane import scheduler_main
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.store import SnapshotControlPlaneStore
from universal_supplier.control_plane.web import create_app


ROOT = Path(__file__).resolve().parents[1]


class RecordingStore(SnapshotControlPlaneStore):
    def __init__(self) -> None:
        super().__init__(ROOT / "reports")
        self.saved: tuple[int, dict] | None = None

    def save_schedule(self, supplier_id: int, values: dict) -> None:
        self.saved = supplier_id, values


def valid_schedule_form(**overrides: str) -> dict[str, str]:
    values = {
        "enabled": "false",
        "cron_expression": "",
        "timezone": "UTC",
        "crawl_type": "full",
        "max_concurrency": "4",
        "min_request_interval_seconds": "0.2",
        "jitter_min_seconds": "0",
        "jitter_max_seconds": "0.1",
        "request_timeout_seconds": "40",
        "max_attempts": "3",
        "backoff_base_seconds": "0.5",
        "backoff_max_seconds": "8",
        "respect_retry_after": "true",
        "consecutive_error_threshold": "5",
        "cooldown_seconds": "30",
        "max_run_duration_seconds": "7200",
    }
    values.update(overrides)
    return values


@pytest.mark.parametrize(("configured", "expected"), (("1", 1), ("3", 3)))
def test_scheduler_main_reads_global_parallelism(monkeypatch, configured, expected):
    captured = {}
    config = object()
    queue = object()

    class FakeOrchestrator:
        def __init__(self, received_config, *, queue, max_parallel_supplier_jobs):
            captured.update(
                config=received_config,
                queue=queue,
                maximum=max_parallel_supplier_jobs,
            )

        def run_forever(self):
            captured["ran"] = True

    monkeypatch.setenv("MAX_PARALLEL_SUPPLIER_JOBS", configured)
    monkeypatch.setenv("SCHEDULER_ENABLED", "NO")
    monkeypatch.setattr(scheduler_main.PostgresConfig, "from_env", lambda: config)
    monkeypatch.setattr(scheduler_main, "PostgresJobQueue", lambda received: queue)
    monkeypatch.setattr(scheduler_main, "Scheduler", lambda *args: object())
    monkeypatch.setattr(scheduler_main, "Orchestrator", FakeOrchestrator)

    scheduler_main.main()

    assert captured == {
        "config": config,
        "queue": queue,
        "maximum": expected,
        "ran": True,
    }


@pytest.mark.parametrize("configured", ("0", "33", "abc", "1.5", ""))
def test_scheduler_main_rejects_invalid_global_parallelism_before_database(monkeypatch, configured):
    monkeypatch.setenv("MAX_PARALLEL_SUPPLIER_JOBS", configured)
    monkeypatch.setattr(
        scheduler_main.PostgresConfig,
        "from_env",
        lambda: pytest.fail("invalid global parallelism must fail before database setup"),
    )
    with pytest.raises(RuntimeError, match="MAX_PARALLEL_SUPPLIER_JOBS"):
        scheduler_main.main()


def test_ui_hides_unsupported_crawl_types_and_labels_full_crawl():
    client = TestClient(create_app(store=RecordingStore(), queue=InMemoryJobQueue()))
    response = client.get("/scheduler")

    assert response.status_code == 200
    assert '<option value="full">Полный сбор</option>' in response.text
    for unsupported in ("fast", "discovery", "identity_validation"):
        assert f'<option value="{unsupported}"' not in response.text
        assert f"<option>{unsupported}</option>" not in response.text


def test_unsupported_manual_crawl_type_is_rejected_before_enqueue():
    queue = InMemoryJobQueue()
    client = TestClient(create_app(store=RecordingStore(), queue=queue))

    response = client.post(
        "/suppliers/1/start",
        data={"crawl_type": "discovery"},
        follow_redirects=False,
    )

    assert response.status_code == 400
    assert queue.jobs == []


@pytest.mark.parametrize(
    "invalid",
    (
        {"max_concurrency": "not-a-number"},
        {"request_timeout_seconds": "0"},
        {"jitter_min_seconds": "1", "jitter_max_seconds": "0.5"},
        {"backoff_base_seconds": "4", "backoff_max_seconds": "3"},
        {"max_run_duration_seconds": "0"},
        {"crawl_type": "fast"},
    ),
)
def test_invalid_rate_or_crawl_settings_render_validation_error(invalid):
    store = RecordingStore()
    client = TestClient(create_app(store=store, queue=InMemoryJobQueue()))

    response = client.post("/scheduler/1", data=valid_schedule_form(**invalid))

    assert response.status_code == 400
    assert 'role="alert"' in response.text
    assert "Settings were not saved:" in response.text
    assert store.saved is None


def test_retry_after_is_visible_configurable_and_false_is_retained():
    store = RecordingStore()
    client = TestClient(create_app(store=store, queue=InMemoryJobQueue()))

    page = client.get("/scheduler")
    assert 'select name="respect_retry_after"' in page.text
    assert '<option value="true"' in page.text
    assert '<option value="false"' in page.text

    response = client.post(
        "/scheduler/1",
        data=valid_schedule_form(respect_retry_after="false"),
        follow_redirects=False,
    )
    assert response.status_code == 303
    assert store.saved is not None
    assert store.saved[1]["respect_retry_after"] is False


def service_block(compose: str, service: str, following_service: str | None) -> str:
    start = f"  {service}:\n"
    block = compose.split(start, 1)[1]
    if following_service:
        block = block.split(f"\n  {following_service}:\n", 1)[0]
    return block


def test_host_web_network_has_publish_capability():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    web = service_block(compose, "web", "orchestrator")

    assert '"127.0.0.1:${WEB_HOST_PORT:-8080}:8080"' in web
    assert "- universal_supplier_internal" in web
    assert "- universal_supplier_egress" in web


def test_postgres_remains_internal_with_loopback_only_host_port():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    postgres = service_block(compose, "postgres", "application")

    assert "- universal_supplier_internal" in postgres
    assert "universal_supplier_egress" not in postgres
    assert '"127.0.0.1:${POSTGRES_HOST_PORT:-5432}:5432"' in postgres
    assert "0.0.0.0:${POSTGRES_HOST_PORT" not in postgres


def test_qa_web_port_binding_is_explicit_loopback_58080():
    qa = (ROOT / "docker-compose.qa.yml").read_text(encoding="utf-8")

    assert "ports: !override" in qa
    assert "target: 8080" in qa
    assert 'published: "58080"' in qa
    assert "host_ip: 127.0.0.1" in qa
    assert 'published: "55432"' in qa
    assert "${QA_VOLUME_NAME:-universal_supplier_stage6c1_qa_pgdata}" in qa


def test_web_restart_preserves_host_binding_verification_path():
    script = (ROOT / "scripts" / "verify_stage6c1_host_web.ps1").read_text(encoding="utf-8")

    assert "docker port" in script
    assert "NetworkSettings.Ports" in script
    assert "Invoke-WebRequest" in script
    assert "restart web" in script
    assert "HostConfig.PortBindings" in script
    assert "postgres_not_public = \"PASS\"" in script
