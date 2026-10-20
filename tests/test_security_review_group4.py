"""Security review 2026-10, group 4: panel, database access, operations."""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
import json
import logging
import os
from pathlib import Path
import stat
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from universal_supplier.change_detection import _quantity, card_hashes
from universal_supplier.control_plane.admin_store import (
    CaseAlreadyResolved, InMemoryAdminStore, PostgresAdminStore, integrity_as_value_error, parse_filter_date,
)
from universal_supplier.control_plane.commercial_qa import (
    MAX_RETAINED_RUNS, CommercialQaRun, CommercialQaService, DisabledCommercialQaService,
)
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.sql_text import escape_like, like_contains
from universal_supplier.control_plane.store import DASHBOARD_SQL, MAX_PAGE, ProductFilters, product_where
from universal_supplier.control_plane.web import create_app, optional_int, public_health
from universal_supplier.stage6e import InMemoryStage6EService, validate_rule_values
from tests.test_session_revocation_heartbeats import PASSWORD, SECRET, csrf, login, make_app, users
from tests.test_stage6d2 import RenderStore, admin_cases

ROOT = Path(__file__).resolve().parents[1]


# --- 10. Bulk actions never override a final decision ------------------------------

def test_bulk_actions_skip_resolved_cases_and_report_them():
    cases = admin_cases()[:3]
    cases[0].update(lifecycle_status="RESOLVED", resolved_decision_class="MANUAL_CONFIRMED")
    admin = InMemoryAdminStore(cases=cases)
    result = admin.bulk_update([1, 2, 3], "mark_reviewed", 0)
    assert result == {"updated": 2, "skipped_resolved": 1, "failed": 0}
    assert admin.review_case(1)["resolved_decision_class"] == "MANUAL_CONFIRMED"
    # An explicit single decision is not affected by the bulk guard.
    admin.decide(1, 0, "POSTPONED", comment="")
    assert admin.review_case(1)["lifecycle_status"] == "POSTPONED"


def test_bulk_route_reports_skipped_resolved_cases():
    cases = admin_cases()[:2]
    cases[0].update(lifecycle_status="RESOLVED", resolved_decision_class="MANUAL_CONFIRMED")
    admin = InMemoryAdminStore(cases=cases)
    client = TestClient(create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=admin, auth_required=False))
    response = client.post("/review/bulk", data={"csrf_token": "test-csrf", "bulk_action": "postpone",
                                                 "case_1": "1", "case_2": "2"}, follow_redirects=False)
    assert response.status_code == 303 and "skipped_resolved=1" in response.headers["location"]
    assert admin.review_case(1)["lifecycle_status"] == "RESOLVED"


class _Rows:
    def __init__(self, answers): self.answers = list(answers); self.sql = []
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def execute(self, sql, params=None): self.sql.append(sql)
    def fetchone(self): return self.answers.pop(0)
    def fetchall(self): return self.answers.pop(0)


class _Conn:
    def __init__(self, cursor): self._cursor = cursor
    def __enter__(self): return self
    def __exit__(self, *args): return False
    @contextmanager
    def transaction(self): yield
    def cursor(self): return self._cursor


def _store(cursor) -> PostgresAdminStore:
    store = PostgresAdminStore.__new__(PostgresAdminStore); store._connect = lambda: _Conn(cursor)
    return store


def test_postgres_bulk_decision_checks_resolved_under_the_row_lock():
    cursor = _Rows([{"id": 1, "lifecycle_status": "RESOLVED", "source_product_id": 9}])
    with pytest.raises(CaseAlreadyResolved):
        _store(cursor).decide(1, 1, "MARKED_REVIEWED", bulk=True)
    assert "FOR UPDATE" in cursor.sql[0] and not any("INSERT" in sql for sql in cursor.sql)


# --- 11. Dashboard -------------------------------------------------------------------

def test_dashboard_counts_without_row_multiplication():
    sql = " ".join(DASHBOARD_SQL.split())
    assert "GROUP BY" not in sql
    assert "LEFT JOIN source_products sp ON" not in sql and "LEFT JOIN crawl_errors ce ON" not in sql
    assert "LEFT JOIN LATERAL (SELECT count(*) AS products" in sql
    assert "LEFT JOIN LATERAL (SELECT count(*) AS errors_30d" in sql


# --- 12. /health ---------------------------------------------------------------------

def test_public_health_exposes_states_only(caplog):
    raw = {"application": "ok", "postgresql": "ok", "version": "17.11 secret build",
           "components": {"scheduler": {"status": "failing", "last_error": "OperationalError: password=x"},
                          "orchestrator": {"status": "not_running"}}, "degraded": True}
    with caplog.at_level(logging.WARNING):
        body, code = public_health(raw)
    assert code == 200
    assert body == {"application": "ok", "postgresql": "ok",
                    "components": {"scheduler": "failing", "orchestrator": "stale"}, "degraded": True}
    assert "17.11" in caplog.text  # details go to the log only


@pytest.mark.parametrize("raw", [
    {"application": "ok", "postgresql": "error", "error": "FATAL: password authentication failed for user x"},
    {"application": "ok", "postgresql": "qa_read_only", "database": "universal_supplier_qa", "role": "qa",
     "system_identifier": 7693098812512405428},
    {"application": "ok", "postgresql": "unavailable", "error": "config missing DB_PASSWORD"},
])
def test_health_endpoint_never_leaks_database_details(raw):
    class Store(RenderStore):
        def health(self): return raw
    response = TestClient(create_app(store=Store(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                     auth_required=False)).get("/health")
    assert response.status_code == 503
    assert response.json() == {"application": "ok", "postgresql": "error", "degraded": False}


def test_heartbeat_last_error_stays_out_of_public_health():
    raw = {"application": "ok", "postgresql": "ok",
           "components": {"scheduler": {"status": "ok", "last_error": "psycopg: host=10.0.0.5"}}}
    assert "10.0.0.5" not in json.dumps(public_health(raw)[0])


# --- 13. Security headers and cookie ------------------------------------------------------

def test_html_responses_carry_security_headers_and_a_fresh_nonce():
    client = TestClient(create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    first, second = client.get("/"), client.get("/")
    for response in (first, second):
        headers = response.headers
        assert headers["x-frame-options"] == "DENY" and headers["x-content-type-options"] == "nosniff"
        assert headers["referrer-policy"] == "no-referrer" and headers["cache-control"] == "no-store"
        csp = headers["content-security-policy"]
        for directive in ("default-src 'self'", "frame-ancestors 'none'", "base-uri 'none'", "form-action 'self'"):
            assert directive in csp
    assert first.headers["content-security-policy"] != second.headers["content-security-policy"]


def test_templates_have_no_inline_script_without_nonce_or_javascript_urls():
    for path in (ROOT / "universal_supplier/control_plane/templates").glob("*.html"):
        text = path.read_text(encoding="utf-8")
        assert "<script>" not in text, path.name
        assert "javascript:" not in text.lower(), path.name
        assert " onclick=" not in text.lower(), path.name


def test_inline_script_nonce_matches_the_csp_header():
    import re
    class Store(RenderStore):
        def run(self, run_id): return {"id": 1, "supplier_name": "Optimum", "status": "running", "metadata": {}}
    client = TestClient(create_app(store=Store(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    response = client.get("/runs/1")
    nonces = re.findall(r'<script nonce="([^"]+)">', response.text)
    assert nonces and all(f"'nonce-{nonce}'" in response.headers["content-security-policy"] for nonce in nonces)


def test_session_cookie_secure_by_default(monkeypatch):
    monkeypatch.delenv("CONTROL_PLANE_INSECURE_HTTP", raising=False)
    app, _ = make_app()
    response = TestClient(app).post("/login", data={"username": "admin1", "password": PASSWORD}, follow_redirects=False)
    assert "secure" in response.headers["set-cookie"].lower()


def test_deployment_examples_do_not_use_the_removed_flag():
    for name in (".env.example", ".env.server.example", "docker-compose.yml", "docker-compose.server.yml",
                 "docker-compose.admin-preview.yml"):
        text = (ROOT / name).read_text(encoding="utf-8")
        assert "HTTPS_ENABLED" not in text and "CONTROL_PLANE_INSECURE_HTTP" in text, name
    assert "CONTROL_PLANE_INSECURE_HTTP=NO" in (ROOT / ".env.server.example").read_text(encoding="utf-8")


# --- 14. Commercial QA journal -----------------------------------------------------------------

def _qa_service(tmp_path) -> CommercialQaService:
    service = CommercialQaService.__new__(CommercialQaService)
    service.artifacts_dir = tmp_path; service._runs = {}; service._journaled = {}
    from threading import Lock
    service._lock = Lock()
    return service


def test_commercial_qa_journal_writes_deltas_and_replays(tmp_path):
    service = _qa_service(tmp_path)
    run = CommercialQaRun("a" * 32, "intervesp", 3, "queued", "2026-10-20T00:00:00+00:00")
    service._record_event(run)
    run.status = "running"; service._record_event(run)
    run.rows = []
    for index in range(3):
        run.rows.append({"n": index, "payload": "x" * 200}); run.processed = index + 1
        service._record_event(run)
    run.status = "succeeded"; run.rows = [{"n": "final"}]; service._record_event(run)
    lines = [json.loads(line) for line in (tmp_path / run.id / "events.jsonl").read_text().splitlines()]
    assert [line["event"] for line in lines][0] == "snapshot"
    assert all(len(json.dumps(line)) < 600 for line in lines[:-2])  # no full snapshot per row
    # The new rows list is written once ("rows"), then each appended row on its own line.
    kinds = [line["event"] for line in lines]
    assert kinds.count("row") == 2 and kinds.count("rows") == 2
    restored = CommercialQaService._replay(tmp_path / run.id / "events.jsonl", run.id)
    assert restored["status"] == "succeeded" and restored["processed"] == 3 and restored["rows"] == [{"n": "final"}]


def test_commercial_qa_legacy_snapshot_journal_still_loads(tmp_path):
    run = CommercialQaRun("b" * 32, "beka_mak", 1, "running", "2026-10-19T00:00:00+00:00", rows=[{"n": 1}])
    (tmp_path / run.id).mkdir()
    (tmp_path / run.id / "events.jsonl").write_text(json.dumps(run.public()) + "\n", encoding="utf-8")
    service = _qa_service(tmp_path); service._load_journal()
    loaded = service._runs[run.id]
    assert loaded.status == "interrupted" and loaded.rows == [{"n": 1}]


def test_commercial_qa_keeps_only_the_newest_runs_in_memory(tmp_path):
    service = _qa_service(tmp_path)
    for index in range(MAX_RETAINED_RUNS + 5):
        run_id = f"{index:032x}"
        service._runs[run_id] = CommercialQaRun(run_id, "intervesp", 1, "succeeded", f"2026-10-{index % 28 + 1:02d}T{index % 24:02d}:00:00")
    active = CommercialQaRun("f" * 32, "intervesp", 1, "running", "2000-01-01T00:00:00")
    service._runs[active.id] = active
    service._evict_old_runs()
    assert len(service._runs) == MAX_RETAINED_RUNS and active.id in service._runs


def test_disabled_commercial_qa_accepts_full_scope_arguments():
    from universal_supplier.control_plane.commercial_qa import CommercialQaUnavailable
    with pytest.raises(CommercialQaUnavailable):
        DisabledCommercialQaService().start("intervesp", 1, True, full_manifest=True, write_confirmed=False, full_scope=True)


# --- 15. Validation and 500s ---------------------------------------------------------------

@pytest.mark.parametrize("value", ["²", "١٢", "1.5", "-1", " ", "１"])
def test_optional_int_accepts_ascii_digits_only(value):
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        optional_int(value, "x")
    assert optional_int(" 42 ", "x") == 42


def test_review_filters_reject_bad_input_with_400():
    client = TestClient(create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    assert client.get("/review?assignee=²").status_code == 400
    assert client.get("/products?page=100001").status_code == 400
    assert client.get("/changes/export.csv?period=between&run_a=²&run_b=1").status_code == 400
    with pytest.raises(ValueError):
        ProductFilters(page=MAX_PAGE + 1)
    with pytest.raises(ValueError, match="Некорректная дата"):
        parse_filter_date("2026-13-40", "открыто с")
    assert str(parse_filter_date("2026-10-20", "x")) == "2026-10-20"


def test_like_patterns_are_escaped():
    assert escape_like("50%_a\\b") == "50\\%\\_a\\\\b"
    assert like_contains("10%") == "%10\\%%"
    where, params = product_where(ProductFilters(search="_", category="%"))
    assert params == ["%\\%%", *["%\\_%"] * 6]


def test_integrity_errors_become_russian_value_errors():
    from psycopg.errors import UniqueViolation
    with pytest.raises(ValueError, match="уже существует"):
        with integrity_as_value_error("Пользователь с таким именем уже существует"):
            raise UniqueViolation("duplicate key value violates unique constraint")
    with pytest.raises(KeyError):
        with integrity_as_value_error("x"):
            raise KeyError("other errors pass through")


def test_xml_export_error_text_only_for_admin(monkeypatch):
    from universal_supplier.xml_export import XmlExportService
    monkeypatch.setattr(XmlExportService, "canonical_artifact", lambda self, supplier: (_ for _ in ()).throw(
        RuntimeError("connection to server at 10.0.0.5 failed")))
    app, _ = make_app()
    admin = login(app, "admin1").get("/exports/xml?action=preview").text
    viewer = login(app, "viewer1").get("/exports/xml?action=preview").text
    assert "10.0.0.5" in admin
    assert "10.0.0.5" not in viewer and "RuntimeError" in viewer


def test_final_release_file_os_error_is_404(monkeypatch, tmp_path):
    from universal_supplier.control_plane import final_release_store
    class Store:
        def __init__(self, *args): pass
        def file(self, name): raise OSError("disk gone")
    monkeypatch.setattr(final_release_store, "FinalReleaseStore", Store)
    monkeypatch.setenv("FINAL_RC_ARTIFACTS_DIR", str(tmp_path))
    client = TestClient(create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    assert client.get("/rc-final/files/X.csv").status_code == 404


@pytest.mark.parametrize("values,message", [
    ({"supplier_id": "1 OR 1=1", "scope": "product", "rule_type": "FORCE_REVIEW"}, "поставщик"),
    ({"supplier_id": "", "scope": "property", "rule_type": "FORCE_REVIEW"}, "свойство"),
    ({"supplier_id": "", "scope": "supplier", "rule_type": "DO_NOT_IMPORT"}, "поставщика"),
    ({"supplier_id": "", "scope": "galaxy", "rule_type": "FORCE_REVIEW"}, "область"),
])
def test_business_rule_validation(values, message):
    with pytest.raises(ValueError, match=message):
        validate_rule_values(values)
    assert validate_rule_values({"supplier_id": "7", "scope": "property", "rule_type": "DO_NOT_USE_SUPPLIER_FIELD"})["supplier_id"] == 7


def test_rule_route_shows_validation_error_instead_of_500():
    client = TestClient(create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   stage6e_service=InMemoryStage6EService(), auth_required=False))
    response = client.post("/rules", data={"csrf_token": "test-csrf", "reason": "x", "scope": "supplier",
                                           "rule_type": "DO_NOT_IMPORT", "supplier_id": "abc"})
    assert response.status_code == 400 and "Правило не сохранено" in response.text


# --- 16. Reparse persistence claim ------------------------------------------------------------------

def test_reparse_preview_is_claimed_atomically_before_ingest():
    source = (ROOT / "universal_supplier/stage6e.py").read_text(encoding="utf-8")
    body = source[source.index("    def persist_reparse_preview(self, preview_id: int, user_id: int) -> dict[str, Any]:\n        from"):]
    body = body[:body.index("    def preview(")]
    assert "WHERE rp.id=%s FOR UPDATE" not in body
    assert body.index("SET persisted=true") < body.index("repository.ingest(")
    assert "AND NOT rp.persisted" in body and "SET persisted=false" in body


# --- 17. Administration ------------------------------------------------------------------------------

def test_last_active_admin_cannot_be_deactivated_in_memory():
    admin = InMemoryAdminStore(users() + [{"id": 3, "username": "admin2", "display_name": "B", "role": "ADMIN",
                                           "is_active": False, "password_hash": "x"}], [])
    with pytest.raises(ValueError, match="последнего"):
        admin.set_user_active(1, False, 99)
    admin.set_user_active(3, True, 1)
    admin.set_user_active(1, False, 3)
    assert not admin.user_by_id(1).is_active


def test_postgres_last_admin_guard_runs_inside_the_transaction():
    cursor = _Rows([{"username": "a", "display_name": "A", "role": "ADMIN", "is_active": True}, []])
    with pytest.raises(ValueError, match="последнего"):
        _store(cursor).set_user_active(1, False, 2)
    assert "FOR UPDATE" in cursor.sql[1] and not any(sql.startswith("UPDATE") for sql in cursor.sql)


def test_login_success_and_failure_are_audited_without_password():
    app, admin = make_app()
    client = TestClient(app)
    client.post("/login", data={"username": "admin1", "password": "wrong-password"}, follow_redirects=False)
    client.post("/login", data={"username": "ghost", "password": "wrong-password"}, follow_redirects=False)
    client.post("/login", data={"username": "admin1", "password": PASSWORD}, follow_redirects=False)
    events = [event for event in admin.audit_events() if event["action"].startswith("LOGIN_")]
    assert [event["action"] for event in reversed(events)] == ["LOGIN_FAILED", "LOGIN_FAILED", "LOGIN_SUCCEEDED"]
    assert "wrong-password" not in json.dumps(events) and PASSWORD not in json.dumps(events)
    assert {event["after_state"]["reason"] for event in events} == {"bad_password", "unknown_user", "password"}


def test_migration_018_adds_recent_audit_index_and_runner_pins_016_017():
    sql = (ROOT / "migrations/018_audit_events_recent_index.sql").read_text(encoding="utf-8")
    assert "audit_events (created_at DESC, id DESC)" in sql and sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    import importlib.util
    spec = importlib.util.spec_from_file_location("migrate_018", ROOT / "scripts/docker_postgres_migrate.py")
    migrate = importlib.util.module_from_spec(spec); spec.loader.exec_module(migrate)
    expected = migrate.expected_hashes()
    for name in ("016_review_decisions_source_product_index.sql", "017_session_revocation_login_throttle_heartbeats.sql"):
        assert expected[name] == migrate.sha256(ROOT / "migrations" / name)
    assert migrate.unknown_applied({"001_a.sql": "x", "099_future.sql": "y"}, {"001_a.sql"}) == ["099_future.sql"]


# --- 18. Scripts and infrastructure -------------------------------------------------------------------

@pytest.mark.skipif(os.name != "posix", reason="POSIX file modes")
def test_server_backup_files_are_owner_only(tmp_path, monkeypatch):
    from scripts import server_backup
    compose = tmp_path / "docker-compose.server.yml"; env = tmp_path / ".env.server"
    compose.write_text("services: {}", encoding="utf-8"); env.write_text("X=1", encoding="utf-8")
    for name, value in (("ROOT", tmp_path), ("COMPOSE", compose), ("ENV", env), ("BACKUPS", tmp_path / "backups")):
        monkeypatch.setattr(server_backup, name, value)
    def fake_run(command, **kwargs):
        kwargs["stdout"].write(b"PGDMP"); return SimpleNamespace(returncode=0)
    monkeypatch.setattr(server_backup.subprocess, "run", fake_run)
    target = server_backup.backup()
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert stat.S_IMODE(target.with_name(target.name + ".json").stat().st_mode) == 0o600
    assert stat.S_IMODE((tmp_path / "backups").stat().st_mode) == 0o700


def test_final_rc_backup_precreates_owner_only_dump():
    source = (ROOT / "scripts/backup_final_rc.py").read_text(encoding="utf-8")
    assert "os.O_EXCL, 0o600" in source and "os.chmod(target, 0o600)" in source and "mode=0o700" in source


def test_init_role_script_keeps_password_out_of_server_log():
    init = (ROOT / "docker/postgres/init/01-create-application-role.sh").read_text(encoding="utf-8")
    begin, role, commit = init.index("BEGIN;"), init.index("'CREATE ROLE"), init.index("COMMIT;")
    assert begin < init.index("SET LOCAL log_statement = 'none';") < role < commit
    assert init.index("SET LOCAL log_min_error_statement = 'panic';") < role
    assert init.index("'ALTER ROLE") < commit < init.index("CREATE DATABASE")


@pytest.mark.parametrize("name,app_db", [("universal_supplier", "universal_supplier"),
                                         ("stage6c_upgrade_qa", "universal_supplier"),
                                         ("universal_supplier_qa_x", "universal_supplier_qa_x")])
def test_qa_upgrade_refuses_non_qa_database(name, app_db):
    from scripts.qa_stage6c_upgrade import checked_qa_database
    with pytest.raises(SystemExit):
        checked_qa_database(name, app_db)


def test_qa_upgrade_default_target_is_a_qa_database():
    from scripts.qa_stage6c_upgrade import DB, checked_qa_database
    assert checked_qa_database(DB, "universal_supplier") == DB


# --- 19. Commercial change detection ---------------------------------------------------------------------

def test_quantity_hash_ignores_trailing_zeros_but_keeps_existing_hashes():
    assert str(_quantity(Decimal("5.000"))) == str(_quantity(Decimal("5"))) == "5"
    assert str(_quantity(Decimal("100"))) == "100" and str(_quantity(Decimal("2.50"))) == "2.5"
    from tests.test_stage6e1 import old_card_payload
    from universal_supplier.models import ProductCard
    base = old_card_payload()
    one = ProductCard.from_jsonable({**base, "quantity": "5"})
    two = ProductCard.from_jsonable({**base, "quantity": "5.000"})
    assert card_hashes(one)["commercial"] == card_hashes(two)["commercial"]
