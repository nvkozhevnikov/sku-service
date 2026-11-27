"""Fixes from the code and security review of 2026-11-26 (docs/REVIEWS/2026-11-26.md)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _migrate():
    spec = importlib.util.spec_from_file_location("migrate_review_20261126", ROOT / "scripts/docker_postgres_migrate.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- K1: develop's 018 audit index, renamed to 022 at the merge -----------------------------

def test_old_018_audit_index_name_is_rewritten_to_022():
    migrate = _migrate()
    digest = migrate.sha256(ROOT / "migrations/022_audit_events_recent_index.sql")
    applied = {"017_session_revocation_login_throttle_heartbeats.sql": "x",
               "018_audit_events_recent_index.sql": digest}
    actual = {path.name: migrate.sha256(path) for path in migrate.MIGRATIONS}
    assert migrate.pending_renames(applied, actual) == {
        "018_audit_events_recent_index.sql": "022_audit_events_recent_index.sql"}


def test_rename_refuses_different_bytes_or_both_names():
    migrate = _migrate()
    actual = {path.name: migrate.sha256(path) for path in migrate.MIGRATIONS}
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        migrate.pending_renames({"018_audit_events_recent_index.sql": "0" * 64}, actual)
    digest = actual["022_audit_events_recent_index.sql"]
    with pytest.raises(RuntimeError, match="both names"):
        migrate.pending_renames({"018_audit_events_recent_index.sql": digest,
                                 "022_audit_events_recent_index.sql": digest}, actual)


def test_no_rename_for_databases_from_the_sterbrust_line():
    migrate = _migrate()
    actual = {path.name: migrate.sha256(path) for path in migrate.MIGRATIONS}
    applied = {"018_login_throttle_non_destructive_reset.sql": actual["018_login_throttle_non_destructive_reset.sql"]}
    assert migrate.pending_renames(applied, actual) == {}


# --- K3: 020 grants only when rc_runtime exists ------------------------------------------------

def test_020_grants_are_conditional_and_old_bytes_stay_accepted():
    sql = (ROOT / "migrations/020_content_studio.sql").read_text(encoding="utf-8")
    body = sql[sql.index("DO $grants$"):]
    assert "IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'rc_runtime')" in body
    before_block = sql[:sql.index("DO $grants$")]
    assert "TO rc_runtime" not in before_block
    migrate = _migrate()
    migrate.migration_body(sql)  # no transaction control outside the wrapper
    current = migrate.sha256(ROOT / "migrations/020_content_studio.sql")
    released = "1fbbcc94c77c2f487ea8cec57080070947f745934d8bf45288c889e3e1e6f24e"
    assert migrate.checksum_matches("020_content_studio.sql", released, current)
    assert migrate.checksum_matches("020_content_studio.sql", current, current)
    assert not migrate.checksum_matches("019_application_accounts.sql", released, current)


# --- K4: CONTENT_MANAGER permissions on PostgreSQL ----------------------------------------------

def test_023_content_manager_matches_builtin_and_only_touches_the_019_seed():
    from universal_supplier.control_plane.accounts import BUILTINS
    sql = (ROOT / "migrations/023_content_manager_profile_permissions.sql").read_text(encoding="utf-8")
    builtin = next(perms for code, _, _, perms in BUILTINS if code == "CONTENT_MANAGER")
    new_value = json.loads(sql.split("SET permissions = '", 1)[1].split("'::jsonb", 1)[0])
    assert sorted(new_value) == sorted(builtin)
    assert "AND permissions = '[\"competitor.read\",\"applications.read\"]'::jsonb" in sql
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")


# --- K2: every panel route has an explicit permission ------------------------------------------

PUBLIC_OR_SELF = {"/login", "/logout", "/health", "/ready", "/account/password", "/{path:path}"}
ADMIN_BY_DESIGN = ("/users", "/roles", "/settings/users")


def _panel_routes():
    import re
    for source in sorted((ROOT / "universal_supplier/control_plane").glob("*.py")):
        text = source.read_text(encoding="utf-8")
        for match in re.finditer(r"@(?:app|router)\.(get|post|put|patch|delete|api_route)\(\s*['\"]([^'\"]+)['\"]", text):
            method = "GET" if match.group(1) == "api_route" else match.group(1).upper()
            yield method, match.group(2)


def test_no_panel_route_falls_through_to_the_admin_default():
    import re
    from universal_supplier.control_plane.access_policy import request_permission
    unmapped = sorted({(method, path) for method, path in _panel_routes()
                       if path not in PUBLIC_OR_SELF and not path.startswith(ADMIN_BY_DESIGN)
                       and request_permission(re.sub(r"\{[^}]+\}", "1", path), method) == "accounts.admin"})
    assert unmapped == []


@pytest.mark.parametrize("path,method,expected", [
    ("/api/commercial-qa/runs/7", "GET", "catalog.read"),
    ("/api/live11/status", "GET", "catalog.read"),
    ("/cabinet/sources/ltt_msk", "GET", "catalog.read"),
    ("/api/cabinet/sources/ltt_msk", "GET", "catalog.read"),
    ("/cabinet/sources/ltt_msk/enqueue", "POST", "supplier.manage"),
    ("/cabinet/quality", "GET", "catalog.read"),
    ("/cabinet/competitor-runs", "GET", "competitor.read"),
    ("/api/competitor-monitoring/run", "GET", "competitor.read"),
    ("/sterbrust-preview", "GET", "xml.read"),
    ("/changes/export.csv", "GET", "reports.read"),
    # B4: section exports follow their section, not xml.read
    ("/exports/review.csv", "GET", "matching.read"),
    ("/exports/suppliers.xlsx", "GET", "catalog.read"),
    ("/exports/runs.csv", "GET", "catalog.read"),
    ("/exports/quality.csv", "GET", "catalog.read"),
    ("/exports/properties.xlsx", "GET", "catalog.read"),
    ("/exports/xml", "GET", "xml.read"),
    ("/exports/xml/download", "GET", "xml.read"),
])
def test_permission_map(path, method, expected):
    from universal_supplier.control_plane.access_policy import request_permission
    assert request_permission(path, method) == expected


def test_xml_only_profile_cannot_export_other_sections():
    from types import SimpleNamespace
    from universal_supplier.control_plane.access_policy import allowed, request_permission
    xml_only = SimpleNamespace(role="VIEWER", permissions={"xml.read"})
    assert allowed(xml_only, request_permission("/exports/xml", "GET"))
    for entity in ("review", "suppliers", "runs", "quality", "properties"):
        assert not allowed(xml_only, request_permission(f"/exports/{entity}.csv", "GET")), entity


# --- B3: legacy user creation needs the current password ---------------------------------------

def test_legacy_user_creation_requires_current_password():
    from fastapi.testclient import TestClient
    from test_security_review_group5 import _app, _csrf, _login, PASSWORD
    app, admin = _app()
    client = _login(app, "admin1")
    form = {"csrf_token": _csrf(client), "username": "second_admin", "display_name": "X",
            "password": "Another-Strong-Pass-1", "role": "ADMIN"}
    assert client.post("/settings/users", data=form, follow_redirects=False).status_code == 403
    assert client.post("/settings/users", data={**form, "current_password": "wrong"}, follow_redirects=False).status_code == 403
    assert not admin.user_by_username("second_admin")
    response = client.post("/settings/users", data={**form, "current_password": PASSWORD}, follow_redirects=False)
    assert response.status_code == 303 and admin.user_by_username("second_admin")


# --- B6/B7/B8: Content Studio -------------------------------------------------------------------

class _Req:
    def __init__(self, method, query=None):
        self.method = method; self.query_params = query or {}; self.headers = {"x-csrf-token": "t"}


def _auth(permissions):
    from types import SimpleNamespace
    from content_studio.shared_auth import SharedAuth
    from universal_supplier.control_plane.access_policy import allowed
    user = SimpleNamespace(role="OPERATOR", permissions=set(permissions), must_change_password=False, id=5)
    store = SimpleNamespace(access=lambda *args: True)
    return SharedAuth(lambda request: user, lambda request: {"csrf": "t"}, allowed, store)


PID = "a" * 32


def test_final_site_export_needs_site_export_permission():
    from fastapi import HTTPException
    auth = _auth({"content.read", "content.export"})
    auth.authorize(_Req("GET", {"kind": "transfer"}), f"/api/projects/{PID}/export")
    with pytest.raises(HTTPException) as error:
        auth.authorize(_Req("GET", {"kind": "final"}), f"/api/projects/{PID}/export")
    assert error.value.status_code == 403
    _auth({"content.read", "content.export", "content.site.export"}).authorize(
        _Req("GET", {"kind": "final"}), f"/api/projects/{PID}/export")


def test_import_needs_media_permission():
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:
        _auth({"content.read", "content.edit"}).authorize(_Req("POST"), "/api/import")
    assert error.value.status_code == 403
    _auth({"content.read", "content.edit", "content.media"}).authorize(_Req("POST"), "/api/import")


def test_browser_module_info_has_no_absolute_path():
    source = (ROOT / "content_studio/app.py").read_text(encoding="utf-8")
    assert "{'folder':'browser_import'," in source and "browser_import').resolve()" not in source


# --- B1/B5: competitor_intel transport ------------------------------------------------------------

@pytest.mark.parametrize("url,answer,expected", [
    ("http://127.0.0.1:8000/admin", None, False),
    ("http://169.254.169.254/latest/meta-data/", None, False),
    ("https://10.0.0.5/", None, False),
    ("http://[::1]/", None, False),
    ("https://cdn.example/app.js", "93.184.216.34", True),
    ("https://cdn.example:8443/app.js", "93.184.216.34", False),
    ("https://rebind.example/x", "10.0.0.1", False),
    ("https://user:pw@cdn.example/x", "93.184.216.34", False),
    ("ftp://cdn.example/x", None, False),
    ("data:image/png;base64,AAAA", None, True),
    ("blob:https://shop.example/1", None, True),
])
def test_browser_sub_requests_only_reach_public_hosts(monkeypatch, url, answer, expected):
    import socket
    from competitor_intel.transport import public_browser_request
    monkeypatch.setattr(socket, "getaddrinfo", lambda host, port, *args, **kwargs:
                        [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (answer or "10.255.0.1", port))])
    assert public_browser_request(url, {}) is expected


def test_competitor_http_body_is_capped(monkeypatch, tmp_path):
    import io
    import requests
    from competitor_intel import transport
    from competitor_intel.transport import HttpTransport, SourceBlocked, read_body
    monkeypatch.setattr(transport, "MAX_BODY_BYTES", 1000)
    response = requests.Response(); response.status_code = 200; response.raw = io.BytesIO(b"x" * 5000)
    with pytest.raises(SourceBlocked, match="RESPONSE_TOO_LARGE"):
        read_body(response)
    small = requests.Response(); small.status_code = 200; small.raw = io.BytesIO(b"<title>ok</title>")
    assert read_body(small) == b"<title>ok</title>" and small.text == "<title>ok</title>"
    session = HttpTransport(tmp_path, sleeper=lambda seconds: None).session
    from universal_supplier.public_connection import public_requests_adapter
    assert type(session.get_adapter("https://metalmaster.ru/")).__name__ == type(public_requests_adapter()).__name__


# --- K5: the queue's global running cap follows MAX_PARALLEL_SUPPLIER_JOBS ------------------------

class _ClaimCursor:
    def __init__(self, running): self.running = running; self.sql = []; self._last = None
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None): self.sql.append(sql); self._last = sql
    def fetchone(self):
        return (self.running,) if "count(*)" in self._last else None


class _ClaimConnection:
    def __init__(self, cursor): self._cursor = cursor
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def transaction(self):
        from contextlib import nullcontext
        return nullcontext()
    def cursor(self): return self._cursor


@pytest.mark.parametrize("setting,running,claims", [("2", 2, False), ("2", 1, True), ("4", 3, True), ("4", 4, False)])
def test_claim_cap_uses_parallel_setting(monkeypatch, setting, running, claims):
    from universal_supplier.control_plane.queue import PostgresJobQueue
    from universal_supplier.control_plane import source_recovery
    monkeypatch.setenv("MAX_PARALLEL_SUPPLIER_JOBS", setting)
    monkeypatch.delenv(source_recovery.ENV + "_FILE", raising=False)
    cursor = _ClaimCursor(running)
    queue = PostgresJobQueue(None, connection_factory=lambda config, autocommit=True: _ClaimConnection(cursor))
    assert queue.claim_next() is None
    assert any("FOR UPDATE OF j SKIP LOCKED" in sql for sql in cursor.sql) is claims


# --- N1-N3 -----------------------------------------------------------------------------------------

def test_monitoring_registered_once_and_imports_not_repeated():
    source = (ROOT / "universal_supplier/control_plane/web.py").read_text(encoding="utf-8")
    assert source.count("register_monitoring(app") == 1
    imports = [line for line in source.splitlines() if line.startswith(("import ", "from "))]
    assert len(imports) == len(set(imports))


def test_filesystem_permission_error_is_not_reported_as_sql_guard():
    import asyncio
    import errno
    from universal_supplier.control_plane.admin_store import InMemoryAdminStore
    from universal_supplier.control_plane.queue import InMemoryJobQueue
    from universal_supplier.control_plane.store import UnavailableControlPlaneStore
    from universal_supplier.control_plane.web import create_app
    app = create_app(store=UnavailableControlPlaneStore("x"), queue=InMemoryJobQueue(),
                     admin_store=InMemoryAdminStore(), auth_required=False)
    handler = app.exception_handlers[PermissionError]
    request = type("R", (), {"url": type("U", (), {"path": "/x"})()})()
    fs = asyncio.run(handler(request, PermissionError(errno.EACCES, "Permission denied", "/srv/reports")))
    assert fs.status_code == 500 and b"SQL safety guard" not in fs.body
    guard = asyncio.run(handler(request, PermissionError("AUTOMATION_SCOPE_GROWTH")))
    assert guard.status_code == 503 and guard.body == b"AUTOMATION_SCOPE_GROWTH"
    other = asyncio.run(handler(request, PermissionError("RUNTIME_DML_FORBIDDEN")))
    assert other.status_code == 503 and other.body == b"SQL safety guard blocked this action"


def test_stop_pilot_without_stop_file_setting_is_a_clear_503():
    source = (ROOT / "universal_supplier/control_plane/web.py").read_text(encoding="utf-8")
    assert "os.environ['PARTNER_PILOT_STOP_FILE']" not in source
    assert "Не задан PARTNER_PILOT_STOP_FILE" in source


# --- DB run finding: semantic export holds follow the supplier external ID ------------------------

def test_semantic_hold_matches_external_id_not_another_databases_surrogate():
    from universal_supplier.export_eligibility import semantic_export_hold
    # Same surrogate id as a held production row, different offer: not held.
    assert semantic_export_hold({"supplier_code": "partner_st", "source_product_id": 15, "external_id": "999001"}) is None
    # The held offer under another surrogate id (another database): still held.
    held = semantic_export_hold({"supplier_code": "partner_st", "source_product_id": 7, "external_id": "309"})
    assert held and held["reason"] == "voltage_380_vs_220"
    # A trace that carries only the surrogate id keeps the production mapping (fail closed).
    assert semantic_export_hold({"supplier_code": "partner_st", "source_product_id": 15})


# --- CI: known pending owner decision ------------------------------------------------------------

@pytest.mark.xfail(strict=True, reason=(
    "config/live11_approval_matrix.csv changed on 2026-11-05 (765ac15) but source_batch.MATRIX_SHA "
    "still pins the 2026-10-22 approval; LIVE11 batch and cabinet source pages refuse until the "
    "owner re-approves the matrix. Remove this marker when MATRIX_SHA is updated."))
def test_live11_matrix_is_approved():
    from universal_supplier.control_plane import source_batch
    assert len(source_batch.matrix_rows()) == 11
