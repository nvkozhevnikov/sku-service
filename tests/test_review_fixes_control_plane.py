from __future__ import annotations

from pathlib import Path
import logging

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane import web as web_module
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import CrawlJob, InMemoryJobQueue
from universal_supplier.control_plane.rate_control import RETRY_AFTER_MAX_SECONDS, retry_after_seconds
from universal_supplier.control_plane.scheduler import Scheduler, SchedulerPolicy
from universal_supplier.control_plane.security import LoginThrottle, SessionSigner, hash_password
from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters, UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app, create_unavailable_app, safe_next

ROOT = Path(__file__).resolve().parents[1]
SECRET = "review-fixes-secret-value-at-least-32-bytes"
PASSWORD = "Correct-Horse-2026"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")


class OfflineStore(UnavailableControlPlaneStore):
    def __init__(self, rows=(), fail_after=None):
        super().__init__("offline"); self.rows = list(rows); self.fail_after = fail_after
    def iter_export_rows(self, filters, chunk_size=500):
        for index, row in enumerate(self.rows):
            if self.fail_after is not None and index >= self.fail_after: raise RuntimeError("db connection lost")
            yield row
    def export_details(self, ids): return {}


def make_client(store=None, login=True):
    admin = InMemoryAdminStore([{"id": 1, "username": "user1", "display_name": "U", "role": "ADMIN", "is_active": True, "password_hash": PASSWORD_HASH}], [])
    client = TestClient(create_app(store=store or OfflineStore(), queue=InMemoryJobQueue(), admin_store=admin, session_secret=SECRET, auth_required=True))
    csrf = ""
    if login:
        assert client.post("/login", data={"username": "user1", "password": PASSWORD}, follow_redirects=False).status_code == 303
        csrf = SessionSigner(SECRET).read(client.cookies.get(SessionSigner.cookie_name))["csrf"]
    return client, csrf


def test_import_fallback_fails_closed_except_probes():
    client = TestClient(create_unavailable_app("SESSION_SIGNING_SECRET is required"))
    assert client.get("/health").status_code == 503 and client.get("/ready").json() == {"ready": False}
    for method, path in (("get", "/"), ("get", "/settings"), ("post", "/settings/users"), ("post", "/login"), ("get", "/exports/xml/download")):
        assert getattr(client, method)(path).status_code == 503


@pytest.mark.parametrize("value,expected", [("/review?x=1", "/review?x=1"), ("//evil.com", "/"), ("/\\evil.com", "/"),
    ("https://evil.com", "/"), ("evil.com", "/"), ("", "/"), (None, "/"), ("/\t/evil.com", "/"), ("/a\\b", "/")])
def test_safe_next(value, expected):
    assert safe_next(value) == expected


def test_login_rejects_open_redirect():
    client, _ = make_client(login=False)
    response = client.post("/login", data={"username": "user1", "password": PASSWORD, "next": "//evil.com/x"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/"


def test_login_unknown_user_runs_scrypt_and_throttle_locks(monkeypatch):
    calls = []; real = web_module.verify_password
    monkeypatch.setattr(web_module, "verify_password", lambda password, encoded: calls.append(encoded) or real(password, encoded))
    client, _ = make_client(login=False)
    assert client.post("/login", data={"username": "ghost", "password": "x" * 12}).status_code == 401
    assert calls and calls[0].startswith("scrypt$")
    for _ in range(5): assert client.post("/login", data={"username": "user1", "password": "wrong-password!"}).status_code == 401
    assert client.post("/login", data={"username": "user1", "password": PASSWORD}, follow_redirects=False).status_code == 429


def test_login_throttle_window_and_reset():
    now = [0.0]; throttle = LoginThrottle(max_failures=2, window_seconds=10, clock=lambda: now[0]); key = ("u", "ip")
    throttle.failure(key); throttle.failure(key); assert throttle.locked(key)
    now[0] = 11; assert not throttle.locked(key)
    throttle.failure(key); throttle.reset(key); assert not throttle.locked(key)


@pytest.mark.parametrize("secret", ["CHANGE_ME_TO_AT_LEAST_32_RANDOM_BYTES", "replace_with_at_least_32_random_bytes"])
def test_placeholder_session_secret_rejected(secret):
    with pytest.raises(ValueError): SessionSigner(secret)


def test_preview_and_test_secrets_still_accepted():
    for secret in ("preview-session-secret-at-least-32-bytes", "stage6d-test-only-session-secret-32bytes", SECRET):
        SessionSigner(secret)


def test_retry_after_is_capped_and_nan_ignored():
    assert retry_after_seconds({"Retry-After": "11"}) == 11
    assert retry_after_seconds({"Retry-After": "999999"}) == RETRY_AFTER_MAX_SECONDS
    assert retry_after_seconds({"Retry-After": "inf"}) == RETRY_AFTER_MAX_SECONDS
    assert retry_after_seconds({"Retry-After": "nan"}) is None


def test_scheduler_run_forever_survives_tick_errors(monkeypatch, caplog):
    scheduler = Scheduler(None, None, SchedulerPolicy(poll_seconds=0)); ticks = []
    def tick(): ticks.append(1); raise RuntimeError("db down")
    def sleep(_):
        if len(ticks) >= 3: raise KeyboardInterrupt
    monkeypatch.setattr(scheduler, "tick", tick); monkeypatch.setattr("universal_supplier.control_plane.scheduler.time.sleep", sleep)
    with caplog.at_level(logging.ERROR), pytest.raises(KeyboardInterrupt): scheduler.run_forever()
    assert len(ticks) == 3 and "scheduler tick failed" in caplog.text


class FakeQueue:
    lease_seconds = 30
    def __init__(self, complete_error=None): self.recovered = 0; self.completed = []; self.complete_error = complete_error
    def recover_stale(self): self.recovered += 1; return 0
    def claim_next(self): return None
    def heartbeat(self, job_id): return True
    def complete(self, job_id, status, **kwargs):
        self.completed.append((job_id, status))
        if self.complete_error: raise self.complete_error


class Lease:
    acquired = True
    def __enter__(self): return self
    def __exit__(self, *args): pass


def test_orchestrator_recovers_stale_leases_periodically(monkeypatch):
    queue = FakeQueue(); orchestrator = Orchestrator(None, queue=queue, registry=object(), lease_factory=lambda d: Lease())
    now = [1000.0]; monkeypatch.setattr("universal_supplier.control_plane.orchestrator.time.monotonic", lambda: now[0])
    orchestrator.tick(); orchestrator.tick(); assert queue.recovered == 1
    now[0] += orchestrator.recover_interval_seconds; orchestrator.tick(); assert queue.recovered == 2


def test_orchestrator_logs_completion_of_already_stale_job(caplog):
    from universal_supplier.control_plane.adapter_registry import AdapterRegistry
    from datetime import datetime, timezone
    queue = FakeQueue(RuntimeError("job 1 is not running")); registry = AdapterRegistry(); registry.register("fake", lambda job, config: {"status": "succeeded"})
    job = CrawlJob(1, 1, "s", "fake", "https://d.invalid", "manual", "full", "running", datetime.now(timezone.utc), {})
    with caplog.at_level(logging.ERROR): Orchestrator(None, queue=queue, registry=registry, lease_factory=lambda d: Lease())._run_job(job)
    assert queue.completed == [(1, "succeeded")] and "already stale" in caplog.text


def test_unguarded_ints_return_400():
    client, csrf = make_client()
    assert client.get("/review?assignee=abc").status_code == 400
    assert client.get("/exports/review.csv?assignee=abc").status_code == 400
    assert client.post("/review/bulk/assign", data={"csrf_token": csrf, "case_ids": "1", "assigned_to_user_id": "x"}).status_code == 400
    assert client.get("/sterbrust-preview?page=abc").status_code == 400


def test_admin_store_rejects_non_numeric_assignee():
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    store = PostgresAdminStore.__new__(PostgresAdminStore)
    with pytest.raises(ValueError): store.review_cases(assignee="1 OR 1=1")


def test_full_xml_download_error_is_not_truncated_200():
    rows = [{"supplier_code": "optimum", "external_id": str(i), "source_product_id": i, "name": f"P{i}"} for i in range(3)]
    client, _ = make_client(OfflineStore(rows, fail_after=2))
    response = client.get("/exports/xml/download?mode=full")
    assert response.status_code == 500 and b"<supplier_export" not in response.content
    client, _ = make_client(OfflineStore(rows))
    response = client.get("/exports/xml/download?mode=full")
    assert response.status_code == 200 and response.content.rstrip().endswith(b"</supplier_export>")


def test_iter_export_rows_uses_server_side_cursor_in_transaction():
    events = []
    class Cursor:
        def __init__(self): self.batches = [[{"source_product_id": 1}], [{"source_product_id": 2}], []]
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def execute(self, sql, params): events.append("execute")
        def fetchmany(self, size): events.append(("fetchmany", size)); return self.batches.pop(0)
    class Tx:
        def __enter__(self): events.append("begin")
        def __exit__(self, *a): events.append("end")
    class Connection:
        def __enter__(self): return self
        def __exit__(self, *a): pass
        def transaction(self): return Tx()
        def cursor(self, name=None): events.append(("cursor", name)); return Cursor()
    store = PostgresControlPlaneStore.__new__(PostgresControlPlaneStore); store._connect = lambda: Connection()
    assert [r["source_product_id"] for r in store.iter_export_rows(ProductFilters(), chunk_size=7)] == [1, 2]
    assert events[0] == "begin" and events[1] == ("cursor", "export_rows") and ("fetchmany", 7) in events and events[-1] == "end"


def test_compose_and_dockerfiles_hardened():
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8"); server = (ROOT / "docker-compose.server.yml").read_text(encoding="utf-8")
    assert "${SESSION_SIGNING_SECRET:?set in .env}" in compose and "CONTROL_PLANE_INSECURE_HTTP: ${CONTROL_PLANE_INSECURE_HTTP:-NO}" in server
    assert "\nUSER app" in (ROOT / "Dockerfile.server").read_text(encoding="utf-8") and "\nUSER app" in (ROOT / "Dockerfile").read_text(encoding="utf-8")
