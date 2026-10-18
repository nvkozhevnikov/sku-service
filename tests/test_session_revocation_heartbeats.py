from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import logging

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane import heartbeat as heartbeat_module
from universal_supplier.control_plane.admin_store import InMemoryAdminStore, PostgresAdminStore
from universal_supplier.control_plane.heartbeat import HeartbeatRecorder, component_status, stale_after_seconds
from universal_supplier.control_plane.orchestrator import Orchestrator
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.scheduler import Scheduler, SchedulerPolicy
from universal_supplier.control_plane.security import AuthUser, LoginThrottle, PostgresLoginThrottle, SessionSigner, hash_password, throttle_digest
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app

ROOT = Path(__file__).resolve().parents[1]
SECRET = "session-revocation-secret-at-least-32-bytes"
PASSWORD = "Correct-Horse-2026"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")
COOKIE = SessionSigner.cookie_name


def users():
    return [{"id": 1, "username": "admin1", "display_name": "A", "role": "ADMIN", "is_active": True, "password_hash": PASSWORD_HASH},
            {"id": 2, "username": "viewer1", "display_name": "V", "role": "VIEWER", "is_active": True, "password_hash": PASSWORD_HASH}]


def make_app(admin_store=None):
    admin_store = admin_store or InMemoryAdminStore(users(), [])
    app = create_app(store=UnavailableControlPlaneStore("offline"), queue=InMemoryJobQueue(), admin_store=admin_store,
                     session_secret=SECRET, auth_required=True)
    return app, admin_store


def login(app, username):
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD}, follow_redirects=False).status_code == 303
    return client


def csrf(client):
    return SessionSigner(SECRET).read(client.cookies.get(COOKIE))["csrf"]


def authorized(app, token):
    client = TestClient(app); client.cookies.set(COOKIE, token)
    return client.get("/settings", follow_redirects=False).status_code == 200


# Session revocation -------------------------------------------------------------

def test_token_carries_session_version():
    token, _ = SessionSigner(SECRET).issue(AuthUser(1, "admin1", "A", "ADMIN", True, 7))
    assert SessionSigner(SECRET).read(token)["sv"] == 7


def test_logout_revokes_copied_session_cookie():
    app, store = make_app(); client = login(app, "admin1"); stolen = client.cookies.get(COOKIE)
    assert authorized(app, stolen)
    assert client.post("/logout", data={"csrf_token": csrf(client)}, follow_redirects=False).status_code == 303
    assert not authorized(app, stolen)
    assert store.audit_events()[0]["action"] == "USER_SESSIONS_REVOKED"
    assert authorized(app, login(app, "admin1").cookies.get(COOKIE))  # a fresh login works again


def test_logout_revokes_every_session_of_that_user_only():
    app, _ = make_app(); first = login(app, "admin1"); second = login(app, "admin1"); other = login(app, "viewer1")
    first.post("/logout", data={"csrf_token": csrf(first)}, follow_redirects=False)
    assert not authorized(app, second.cookies.get(COOKIE))
    other_client = TestClient(app); other_client.cookies.set(COOKIE, other.cookies.get(COOKIE))
    assert other_client.get("/", follow_redirects=False).status_code != 303  # viewer1 stays signed in


def test_token_without_session_version_is_rejected():
    app, _ = make_app()
    signer = SessionSigner(SECRET); token, _ = signer.issue(AuthUser(1, "admin1", "A", "ADMIN"))
    payload = signer.read(token); payload.pop("sv")
    import base64, hashlib, hmac, json
    raw = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=")
    legacy = (raw + b"." + base64.urlsafe_b64encode(hmac.new(SECRET.encode(), raw, hashlib.sha256).digest()).rstrip(b"=")).decode()
    assert authorized(app, token) and not authorized(app, legacy)


def test_deactivation_revokes_sessions_even_after_reactivation():
    app, _ = make_app(); admin = login(app, "admin1"); viewer_token = login(app, "viewer1").cookies.get(COOKIE)
    for active in ("false", "true"):
        assert admin.post("/settings/users/2/active", data={"csrf_token": csrf(admin), "active": active}, follow_redirects=False).status_code == 303
    client = TestClient(app); client.cookies.set(COOKIE, viewer_token)
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")


def test_admin_can_revoke_other_user_sessions_and_viewer_cannot():
    app, store = make_app(); admin = login(app, "admin1"); viewer = login(app, "viewer1"); viewer_token = viewer.cookies.get(COOKIE)
    assert viewer.post("/settings/users/1/revoke-sessions", data={"csrf_token": csrf(viewer)}, follow_redirects=False).status_code == 403
    assert authorized(app, admin.cookies.get(COOKIE))
    response = admin.post("/settings/users/2/revoke-sessions", data={"csrf_token": csrf(admin)}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/settings"
    client = TestClient(app); client.cookies.set(COOKIE, viewer_token)
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    assert authorized(app, admin.cookies.get(COOKIE))
    assert "Завершить сессии" in admin.get("/settings").text


def test_revoke_requires_csrf():
    app, _ = make_app(); admin = login(app, "admin1")
    assert admin.post("/settings/users/2/revoke-sessions", data={"csrf_token": "forged"}, follow_redirects=False).status_code == 403


def test_postgres_admin_store_reads_and_bumps_session_version():
    source = (ROOT / "universal_supplier/control_plane/admin_store.py").read_text(encoding="utf-8")
    assert "is_active,session_version FROM app_users WHERE id=%s" in source
    assert "is_active,session_version,password_hash FROM app_users WHERE username=%s" in source
    assert "session_version=session_version+1" in source and "session_version=session_version+(%s AND is_active)::int" in source


def test_migration_017_adds_session_version_throttle_and_heartbeats():
    sql = (ROOT / "migrations/017_session_revocation_login_throttle_heartbeats.sql").read_text(encoding="utf-8")
    assert "ADD COLUMN session_version integer NOT NULL DEFAULT 1" in sql
    assert "CREATE TABLE login_failures" in sql and "CREATE TABLE component_heartbeats" in sql
    assert sql.lstrip().startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")


# Shared login throttle ----------------------------------------------------------

class FakeCursor:
    def __init__(self, log, result=(0,)): self.log, self.result = log, result
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def execute(self, sql, params=()): self.log.append((" ".join(sql.split()), params))
    def fetchone(self): return self.result


class FakeConnection:
    def __init__(self, log, result=(0,)): self.log, self.result = log, result
    def __enter__(self): return self
    def __exit__(self, *args): pass
    def cursor(self): return FakeCursor(self.log, self.result)
    def transaction(self): return self


def test_throttle_digest_hides_username_and_address():
    digest = throttle_digest(("admin1", "10.0.0.5"))
    assert len(digest) == 64 and "admin1" not in digest and digest != throttle_digest(("admin1", "10.0.0.6"))


def test_postgres_login_throttle_queries(monkeypatch):
    log = []; throttle = PostgresLoginThrottle(object(), max_failures=5, window_seconds=900)
    monkeypatch.setattr(throttle, "_connect", lambda: FakeConnection(log, (5,)))
    key = ("admin1", "10.0.0.5")
    assert throttle.locked(key) is True
    throttle.failure(key); throttle.reset(key)
    sql = [entry[0] for entry in log]
    assert sql[0].startswith("SELECT count(*) FROM login_failures WHERE throttle_key=%s") and log[0][1] == (throttle_digest(key), 900)
    assert sql[1].startswith("DELETE FROM login_failures WHERE failed_at <=") and sql[2].startswith("INSERT INTO login_failures")
    assert sql[3] == "DELETE FROM login_failures WHERE throttle_key=%s"
    assert all("admin1" not in str(params) for _, params in log)


def test_app_uses_shared_throttle_with_postgres_admin_store():
    store = PostgresAdminStore(object())
    app = create_app(store=UnavailableControlPlaneStore("offline"), queue=InMemoryJobQueue(), admin_store=store, session_secret=SECRET, auth_required=True)
    assert isinstance(app.state.login_throttle, PostgresLoginThrottle)
    assert isinstance(make_app()[0].state.login_throttle, LoginThrottle)


# Heartbeats ---------------------------------------------------------------------

def test_heartbeat_success_writes_are_rate_limited_but_errors_always_written(monkeypatch):
    log = []; now = [0.0]
    recorder = HeartbeatRecorder(object(), "scheduler", 15, min_write_interval_seconds=30, clock=lambda: now[0])
    monkeypatch.setattr(recorder, "_connect", lambda: FakeConnection(log))
    recorder.record(); now[0] = 10; recorder.record()
    assert len(log) == 1 and "consecutive_failures=0" in log[0][0]
    recorder.record(RuntimeError("db down")); recorder.record(RuntimeError("db down"))
    assert len(log) == 3 and "consecutive_failures=component_heartbeats.consecutive_failures+1" in log[2][0]
    assert log[2][1] == ("scheduler", "RuntimeError: db down", 15.0)
    now[0] = 12; recorder.record()  # first success after an error is written immediately
    assert len(log) == 4


def test_heartbeat_never_raises_when_database_is_down(monkeypatch, caplog):
    recorder = HeartbeatRecorder(object(), "orchestrator", 2)
    def broken(): raise OSError("connection refused")
    monkeypatch.setattr(recorder, "_connect", broken)
    with caplog.at_level(logging.WARNING):
        recorder.record(); recorder.record(RuntimeError("x")); recorder.clear()
    assert "could not record orchestrator heartbeat" in caplog.text


def test_heartbeat_rejects_unknown_component():
    with pytest.raises(ValueError): HeartbeatRecorder(object(), "web", 1)


def row(age_seconds, failures=0, poll=15.0, now=None):
    now = now or datetime(2026, 10, 18, 12, tzinfo=timezone.utc)
    return {"last_tick_at": now - timedelta(seconds=age_seconds), "last_success_at": now - timedelta(seconds=age_seconds),
            "last_error": "RuntimeError: x" if failures else None, "consecutive_failures": failures, "poll_seconds": poll}, now


@pytest.mark.parametrize(("age", "failures", "expected"), ((5, 0, "ok"), (5, 2, "ok"), (5, 3, "failing"), (500, 0, "stale"), (500, 9, "stale")))
def test_component_status(age, failures, expected):
    item, now = row(age, failures)
    assert component_status(item, now)["status"] == expected


def test_component_status_missing_row_and_stale_threshold():
    assert component_status(None, datetime.now(timezone.utc)) == {"status": "not_running"}
    assert stale_after_seconds(2) == 120 and stale_after_seconds(60) == 30 + 240


class Recorder:
    def __init__(self): self.calls = []
    def record(self, error=None): self.calls.append(None if error is None else str(error))


def test_scheduler_loop_records_success_and_failure(monkeypatch):
    recorder = Recorder(); scheduler = Scheduler(None, None, SchedulerPolicy(poll_seconds=0), heartbeat=recorder); results = iter([None, RuntimeError("db down")])
    def tick():
        result = next(results)
        if result: raise result
    def sleep(_):
        if len(recorder.calls) >= 2: raise KeyboardInterrupt
    monkeypatch.setattr(scheduler, "tick", tick); monkeypatch.setattr("universal_supplier.control_plane.scheduler.time.sleep", sleep)
    with pytest.raises(KeyboardInterrupt): scheduler.run_forever()
    assert recorder.calls == [None, "db down"]


def test_orchestrator_loop_records_heartbeat(monkeypatch):
    recorder = Recorder(); orchestrator = Orchestrator(object(), queue=object(), registry=object(), poll_seconds=0, heartbeat=recorder)
    monkeypatch.setattr(orchestrator, "tick", lambda: 0)
    def sleep(_): raise KeyboardInterrupt
    monkeypatch.setattr("universal_supplier.control_plane.orchestrator.time.sleep", sleep)
    with pytest.raises(KeyboardInterrupt): orchestrator.run_forever()
    assert recorder.calls == [None]


def test_heartbeat_cli_usage_and_unavailable(monkeypatch, capsys):
    assert heartbeat_module.main([]) == 2 and heartbeat_module.main(["web"]) == 2
    monkeypatch.setattr(heartbeat_module.PostgresConfig, "from_env", lambda: (_ for _ in ()).throw(RuntimeError("no env")))
    assert heartbeat_module.main(["orchestrator"]) == 1
    assert "heartbeat unavailable" in capsys.readouterr().err


def test_health_reports_components_without_changing_status_code(monkeypatch):
    class Store(UnavailableControlPlaneStore):
        def health(self): return {"application": "ok", "postgresql": "ok", "components": {"scheduler": {"status": "stale"}}, "degraded": True}
    app = create_app(store=Store("x"), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(users(), []), session_secret=SECRET, auth_required=True)
    response = TestClient(app).get("/health")
    assert response.status_code == 200 and response.json()["degraded"] is True


def test_compose_orchestrators_have_heartbeat_healthcheck():
    for name, expected in (("docker-compose.yml", 1), ("docker-compose.server.yml", 2)):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert text.count('"universal_supplier.control_plane.heartbeat", "orchestrator"') == expected, name
