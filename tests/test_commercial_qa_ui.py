from __future__ import annotations

import inspect
import hashlib
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
from xml.etree import ElementTree as ET

from fastapi.testclient import TestClient
import pytest

from universal_supplier.commercial_collection import CollectionRow, CommercialCollectionResult, SiteCollectionResult
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.commercial_qa import (
    CommercialQaRun,
    CommercialQaService,
    DisabledCommercialQaService,
    MAX_UI_LIMIT,
    MIN_PAUSE_SECONDS,
)
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.security import SessionSigner, hash_password
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.stage6e import InMemoryStage6EService


class FakeCommercialQaService:
    enabled = True

    def __init__(self):
        self.item = CommercialQaRun(
            id="safe-run", source="intervesp", limit=3, status="succeeded",
            started_at="2026-09-25T10:00:00+00:00", finished_at="2026-09-25T10:01:00+00:00",
            report_path="safe/report.json",
            rows=[
                {"phase": "product", "result": "PERSISTED", "parsed_model": "BMS-230DG",
                 "expected_model": "BMS-230DG", "execution": None, "price": None,
                 "currency": None, "availability": "unknown"},
                {"phase": "review", "result": "REVIEW", "requested_url": "https://example.test/review",
                 "expected_model": "BMSY-320DGH", "diagnostics": ["URL/title conflict"]},
                {"phase": "product", "result": "HTTP_ERROR", "requested_url": "https://example.test/blocked",
                 "http_status": 503, "diagnostics": ["upstream_unavailable"]},
            ],
            site_summary={"fetched_cards": 1, "observations_created": 0, "reviews": 1, "errors": 0},
        )

    def overview(self):
        return {"enabled": True, "reason": "", "runs": [self.item.public()],
                "manifest": {"intervesp": {"candidates": 24, "sha256": "a" * 64},
                             "beka_mak": {"candidates": 44, "sha256": "b" * 64}},
                "xml": {"bytes": 10, "sha256": "c" * 64,
                        "notice": "Диагностический XML. Не является файлом импорта Sterbrust."},
                "write": {"enabled": False, "target": "stage4_commercial_qa", "reason": "not enabled"}}

    def start(self, source, limit, dry_run, *, full_manifest=False, write_confirmed=False):
        assert source in {"intervesp", "beka_mak"}
        assert limit == 3 and dry_run is True
        return self.item

    def run(self, run_id):
        return self.item.public() if run_id == self.item.id else None

    def report_bytes(self, run_id):
        assert run_id == self.item.id
        return b'{"dry_run":true}'

    def diagnostic_xml(self):
        return b'<?xml version="1.0" encoding="UTF-8"?><supplier_export/>', self.overview()["xml"]


class WriteReadyFakeCommercialQaService(FakeCommercialQaService):
    def overview(self):
        data = super().overview()
        data["write"] = {"enabled": True, "target": "stage4_commercial_qa", "reason": ""}
        return data


class ServerReadyFakeCommercialQaService(WriteReadyFakeCommercialQaService):
    def overview(self):
        data = super().overview()
        data["server_mode"] = True
        data["write"]["target"] = "universal_supplier_server"
        data["full_manifest"] = {code: {"candidates": count, "sha256": "d" * 64}
                                 for code, count in (("intervesp", 81), ("beka_mak", 98),
                                                     ("beka_mak_tr", 76), ("all", 255))}
        return data


def test_server_form_defaults_to_full_manifest_and_names_persistent_target():
    client, _ = client_and_csrf(ServerReadyFakeCommercialQaService())
    page = client.get("/commercial-qa")
    assert page.status_code == 200
    assert 'value="full" selected' in page.text
    assert "Сохранить в universal_supplier_server" in page.text
    assert "Обновить все три источника" in page.text


class ReadOnlyOfflineStore(UnavailableControlPlaneStore):
    read_only = True


class ReadOnlyCommercialSupplierStore(ReadOnlyOfflineStore):
    def suppliers(self):
        return [{"id": -4, "code": "beka_mak", "name": "Beka-Mak", "base_url": "https://beka-mak.su/",
                 "enabled": False, "products": 44, "last_crawl": None, "last_status": None,
                 "last_discovered_count": 44, "last_error_count": 0, "next_runs": [],
                 "job_status": None, "rate_settings": {"min_request_interval_seconds": 20,
                 "max_concurrency": 1, "request_timeout_seconds": 30}, "crawl_settings": {}}]

    def dashboard(self):
        return self.suppliers()


class CountingQueue(InMemoryJobQueue):
    def __init__(self):
        super().__init__()
        self.enqueue_calls = 0

    def enqueue(self, *args, **kwargs):
        self.enqueue_calls += 1
        return super().enqueue(*args, **kwargs)


def client_and_csrf(service=None):
    secret = "commercial-qa-ui-test-secret-at-least-32-bytes"
    admin = InMemoryAdminStore([{
        "id": 1, "username": "operator", "display_name": "Operator", "role": "OPERATOR",
        "is_active": True, "password_hash": hash_password("safe-password", salt=b"0123456789abcdef"),
    }])
    app = create_app(store=UnavailableControlPlaneStore("offline test"), queue=InMemoryJobQueue(),
                     admin_store=admin, stage6e_service=InMemoryStage6EService(),
                     session_secret=secret, auth_required=True,
                     commercial_qa_service=service or FakeCommercialQaService())
    client = TestClient(app)
    assert client.post("/login", data={"username": "operator", "password": "safe-password"},
                       follow_redirects=False).status_code == 303
    csrf = SessionSigner(secret).read(client.cookies.get(SessionSigner.cookie_name))["csrf"]
    return client, csrf


def test_safe_commercial_qa_page_and_run_are_available_without_database_write():
    client, csrf = client_and_csrf()
    page = client.get("/commercial-qa")
    assert page.status_code == 200
    assert "Проверка без записи" in page.text
    assert "Ручной сбор" in page.text
    assert "Кандидатов в manifest" in page.text
    response = client.post("/commercial-qa/runs", data={"csrf_token": csrf, "source": "intervesp",
                                                          "limit": "3", "dry_run": "true"},
                           follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/commercial-qa/runs/safe-run"
    detail = client.get(response.headers["location"])
    assert "BMS-230DG" in detail.text and "REVIEW" in detail.text and "URL/title conflict" in detail.text
    assert "HTTP и другие ошибки" in detail.text and "upstream_unavailable" in detail.text
    assert client.get("/commercial-qa/runs/safe-run/report").content == b'{"dry_run":true}'


def test_diagnostic_xml_is_explicitly_not_an_import_payload():
    client, _ = client_and_csrf()
    page = client.get("/commercial-qa/xml")
    assert page.status_code == 200
    assert "Не является файлом импорта Sterbrust" in page.text
    download = client.get("/commercial-qa/xml/download")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/xml")
    assert b"supplier_export" in download.content


def test_write_ready_form_still_defaults_to_dry_run_and_names_fixed_qa_target():
    client, _ = client_and_csrf(WriteReadyFakeCommercialQaService())
    page = client.get("/commercial-qa")
    assert page.status_code == 200
    assert '<option value="true">Проверка без записи</option>' in page.text
    assert '<option value="false">Сохранить в QA PostgreSQL</option>' in page.text
    assert 'name="full_manifest"' in page.text
    assert 'name="write_confirmed"' in page.text
    assert "stage4_commercial_qa" in page.text


def test_commercial_qa_is_disabled_without_explicit_operator_configuration(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_QA_UI_ENABLED", raising=False)
    service = CommercialQaService.from_env()
    assert isinstance(service, DisabledCommercialQaService)
    assert service.overview()["enabled"] is False


def test_commercial_qa_accepts_the_documented_explicit_ui_environment(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("COMMERCIAL_QA_UI_ENABLED", "YES")
    monkeypatch.setenv("COMMERCIAL_QA_UI_MANIFEST_PATH", str(
        root / "reports/STAGE5D_2026-09-24/candidate_manifest_http_dry_run.json"))
    monkeypatch.setenv("COMMERCIAL_QA_UI_ARTIFACTS_DIR", str(root / "reports/STAGE5D_2026-09-25/ui_runs"))
    monkeypatch.setenv("COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH", str(
        root / "reports/STAGE5D_2026-09-25/final_qa/STAGE5D_COMMERCIAL_QA_POST_RECOVERY.xml"))
    service = CommercialQaService.from_env()
    assert isinstance(service, CommercialQaService)


def test_write_option_requires_successful_preflight_for_both_sources(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    service = CommercialQaService(
        manifest_path=root / "reports/STAGE5D_2026-09-24/candidate_manifest_http_dry_run.json",
        artifacts_dir=root / "reports/STAGE5D_2026-09-25/ui_runs",
        diagnostic_xml_path=root / "reports/STAGE5D_2026-09-25/final_qa/STAGE5D_COMMERCIAL_QA_POST_RECOVERY.xml",
        write_repository_factory=lambda: object(),
    )
    checked = []
    monkeypatch.setattr(service, "_preflight_write_target", lambda source: checked.append(source))
    assert service.overview()["write"]["enabled"] is True
    assert checked == ["beka_mak", "intervesp"]

    def unavailable(_source):
        raise RuntimeError("connection details must not reach the page")

    monkeypatch.setattr(service, "_preflight_write_target", unavailable)
    write = service.overview()["write"]
    assert write["enabled"] is False
    assert "connection details" not in write["reason"]


def test_real_bridge_hardcodes_bounded_no_persistence_collection():
    source = inspect.getsource(CommercialQaService._execute)
    assert MAX_UI_LIMIT == 3
    assert MIN_PAUSE_SECONDS == 20.0
    assert "run.mode == \"dry_run\"" in source
    assert "repository = None if run.mode == \"dry_run\"" in source


def test_write_mode_has_server_and_operator_confirmation_guards():
    source = inspect.getsource(CommercialQaService.start)
    assert "write_repository_factory is None" in source
    assert "not write_confirmed" in source
    assert "_preflight_write_target(selected_source)" in source
    preflight = inspect.getsource(CommercialQaService._check_write_target)
    assert "pg_is_in_recovery()" in preflight
    assert "inet_server_addr()" in preflight and "inet_server_port()" in preflight
    assert "system_identifier FROM pg_control_system()" in preflight
    assert "rolsuper, rolcreatedb, rolcreaterole" in preflight
    assert "catalog_product_id IS NOT NULL" in preflight
    assert "SELECT count(*) FROM offers WHERE active" in preflight


def test_write_preflight_checks_exact_endpoint_role_and_passive_state():
    class Cursor:
        def __init__(self, identity):
            self.answers = iter((identity, (False, False, False), (0,), (0,), (0,)))
            self.statements = []

        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, statement, *_):
            self.statements.append(statement)
            return self
        def fetchone(self): return next(self.answers)
        def fetchall(self): return [("beka_mak", False)]

    class Repository:
        def __init__(self, identity):
            self.cursor = Cursor(identity)
            self.connection = SimpleNamespace(cursor=lambda: self.cursor)
            self.closed = False

        def gate(self): return {"select_1": True}
        def close(self): self.closed = True

    root = Path(__file__).resolve().parents[1]
    made = []

    def factory():
        repository = Repository(("stage4_commercial_qa", "stage4_runtime", False,
                                 "17.11", "127.0.0.1", 55447, 7689025282387590508))
        made.append(repository)
        return repository

    service = CommercialQaService(
        manifest_path=root / "reports/STAGE5D_2026-09-24/candidate_manifest_http_dry_run.json",
        artifacts_dir=root / "reports/STAGE5D_2026-09-25/ui_runs",
        diagnostic_xml_path=root / "reports/STAGE5D_2026-09-25/final_qa/STAGE5D_COMMERCIAL_QA_POST_RECOVERY.xml",
        write_repository_factory=factory,
    )
    service._preflight_write_target("beka_mak")
    assert made[-1].closed
    assert all(statement.lstrip().startswith("SELECT") for statement in made[-1].cursor.statements)

    def wrong_factory():
        repository = Repository(("wrong_database", "stage4_runtime", False,
                                 "17.11", "127.0.0.1", 55447, 7689025282387590508))
        made.append(repository)
        return repository

    service.write_repository_factory = wrong_factory
    with pytest.raises(ValueError, match="разрешённую Stage 4"):
        service._preflight_write_target("beka_mak")
    assert made[-1].closed

    service.write_repository_factory = lambda: Repository((
        "stage4_commercial_qa", "stage4_runtime", False,
        "17.11", "127.0.0.1", 55447, 1,
    ))
    with pytest.raises(ValueError, match="разрешённую Stage 4"):
        service._preflight_write_target("beka_mak")


def test_execute_closes_write_repository_even_when_collection_fails():
    source = inspect.getsource(CommercialQaService._execute)
    assert "finally:" in source
    assert "repository.close()" in source


def _offline_manual_service(directory: Path, collector, *, write_repository_factory=None):
    root = Path(__file__).resolve().parents[1]
    xml_path = directory / "diagnostic.xml"
    xml_path.write_bytes(b'<supplier_export/>')
    return CommercialQaService(
        manifest_path=root / "reports/STAGE5D_2026-09-24/candidate_manifest_http_dry_run.json",
        artifacts_dir=directory / "runs", diagnostic_xml_path=xml_path,
        collector=collector, write_repository_factory=write_repository_factory,
    )


def _one_row_result(row: CollectionRow, *, dry_run: bool):
    summary = SiteCollectionResult(row.site, 1, 2, 0, 1, 1, 1, 1, 0, 0, 0, False,
                                   0 if dry_run else 1, 0)
    return CommercialCollectionResult("2026-09-29T00:00:00+00:00", "2026-09-29T00:01:00+00:00",
                                      dry_run, (summary,), (row,))


def _wait_for_manual_run(service, run_id):
    for _ in range(100):
        run = service.run(run_id)
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(.01)
    raise AssertionError("manual run did not finish")


def test_manual_job_journal_survives_restart_and_keeps_progress():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root / "reports") as temporary:
        location = Path(temporary)

        def collector(**kwargs):
            row = CollectionRow("beka_mak", "https://beka-mak.su/product/test/", "BMS-230DG",
                                None, "product", "DRY_RUN", 200, "19240", "BMS-230DG")
            kwargs["on_progress"](row, 1, 1)
            return _one_row_result(row, dry_run=True)

        service = _offline_manual_service(location, collector)
        run = service.start("beka_mak", 1, True)
        finished = _wait_for_manual_run(service, run.id)
        assert finished["status"] == "succeeded"
        assert finished["processed"] == 1 and finished["total"] == 1
        assert (location / "runs" / run.id / "events.jsonl").is_file()
        restarted = _offline_manual_service(location, collector)
        assert restarted.run(run.id)["status"] == "succeeded"
        assert len(restarted.run(run.id)["rows"]) == 1
        assert b'DRY_RUN' in restarted.report_bytes(run.id)


def test_interrupted_job_is_not_restarted_and_errors_do_not_leak_secrets():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root / "reports") as temporary:
        location = Path(temporary)
        calls = []

        def collector(**kwargs):
            calls.append(1)
            raise RuntimeError("secret-value-must-never-be-logged")

        service = _offline_manual_service(location, collector)
        run = service.start("intervesp", 1, True)
        failed = _wait_for_manual_run(service, run.id)
        assert failed["status"] == "failed"
        assert "secret-value" not in failed["error"]
        assert "secret-value" not in (location / "runs" / run.id / "events.jsonl").read_text(encoding="utf-8")
        restarted = _offline_manual_service(location, collector)
        assert restarted.run(run.id)["status"] == "failed"
        assert calls == [1]
        stale = CommercialQaRun("a" * 32, "beka_mak", 1, "running", "2026-09-29T00:00:00+00:00")
        restarted._record_event(stale)
        recovered = _offline_manual_service(location, collector)
        assert recovered.run(stale.id)["status"] == "interrupted"
        assert calls == [1]


def test_qa_write_updates_diagnostic_xml_and_records_before_after_without_real_db(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root / "reports") as temporary:
        location = Path(temporary)

        class Repository:
            closed = False

            def close(self):
                self.closed = True

        repository = Repository()

        def collector(**kwargs):
            assert kwargs["repository"] is repository and not kwargs["dry_run"]
            row = CollectionRow("beka_mak", "https://beka-mak.su/product/test/", "BMS-230DG",
                                None, "product", "PERSISTED", 200, "19240", "BMS-230DG")
            kwargs["on_progress"](row, 1, 1)
            return _one_row_result(row, dry_run=False)

        service = _offline_manual_service(location, collector, write_repository_factory=lambda: repository)
        monkeypatch.setattr(service, "_preflight_write_target", lambda _source: None)
        counts = iter(({"source_products": 66}, {"source_products": 66}))
        monkeypatch.setattr(service, "_database_counts", lambda _repository: next(counts))

        def refresh(_repository, _run_id):
            service.diagnostic_xml_path.write_bytes(b'<supplier_export count="66"/>')
            return "a" * 64, 66

        monkeypatch.setattr(service, "_refresh_diagnostic_xml", refresh)
        run = service.start("beka_mak", 1, False, write_confirmed=True)
        finished = _wait_for_manual_run(service, run.id)
        assert finished["status"] == "succeeded"
        assert finished["counts_before"] == {"source_products": 66}
        assert finished["counts_after"] == {"source_products": 66}
        assert finished["xml_products"] == 66 and finished["xml_sha256"] == "a" * 64
        assert service.diagnostic_xml()[0] == b'<supplier_export count="66"/>'
        assert repository.closed


def test_diagnostic_xml_refresh_uses_current_qa_rows_and_keeps_identity_blank():
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(dir=root / "reports") as temporary:
        service = _offline_manual_service(Path(temporary), lambda **_kwargs: None)

        class Cursor:
            description = [SimpleNamespace(name=key) for key in (
                "source_product_id", "external_id", "supplier_code", "source_url", "name", "sku",
                "price_source", "price", "currency", "availability_normalized")]

            def __enter__(self): return self
            def __exit__(self, *_args): return None
            def execute(self, sql, params):
                assert "offer_commercial_observations" in sql
                assert params == ("intervesp", "beka_mak", "beka_mak_tr")
            def fetchall(self):
                return [(1, "8992", "intervesp", "https://intervesp.ru/test/", "BMS-230DG", "BMS-230DG",
                         "price_on_request", None, None, "unknown")]

        repository = SimpleNamespace(connection=SimpleNamespace(cursor=Cursor))
        digest, products = service._refresh_diagnostic_xml(repository, "a" * 32)
        body = service.diagnostic_xml()[0]
        assert products == 1 and digest == hashlib.sha256(body).hexdigest()
        product = ET.fromstring(body).find(".//product")
        assert product.get("external_id") == "8992"
        assert product.findtext(".//price") == ""
        assert product.findtext(".//sterbrust_product_id") is None


def test_read_only_qa_blocks_regular_supplier_posts_before_queue_or_store_access():
    secret = "readonly-qa-ui-test-secret-at-least-32-bytes"
    admin = InMemoryAdminStore([{
        "id": 1, "username": "operator", "display_name": "Operator", "role": "OPERATOR",
        "is_active": True, "password_hash": hash_password("safe-password", salt=b"0123456789abcdef"),
    }])
    queue = CountingQueue()
    app = create_app(store=ReadOnlyOfflineStore("no database in test"), queue=queue, admin_store=admin,
                     stage6e_service=InMemoryStage6EService(), session_secret=secret, auth_required=True,
                     commercial_qa_service=FakeCommercialQaService())
    client = TestClient(app)
    assert client.post("/login", data={"username": "operator", "password": "safe-password"},
                       follow_redirects=False).status_code == 303
    for path in ("/suppliers/-2/start", "/suppliers/-2/enabled", "/suppliers/-2/schedule"):
        response = client.post(path, data={"csrf_token": "unused"}, follow_redirects=False)
        assert response.status_code == 403
        assert "только для чтения" in response.text
    assert queue.enqueue_calls == 0


def test_read_only_supplier_pages_point_to_bounded_manual_run_not_blocked_start():
    secret = "readonly-manual-navigation-test-secret-32bytes"
    admin = InMemoryAdminStore([{
        "id": 1, "username": "operator", "display_name": "Operator", "role": "OPERATOR",
        "is_active": True, "password_hash": hash_password("safe-password", salt=b"0123456789abcdef"),
    }])
    queue = CountingQueue()
    app = create_app(store=ReadOnlyCommercialSupplierStore("offline test"), queue=queue,
                     admin_store=admin, stage6e_service=InMemoryStage6EService(),
                     session_secret=secret, auth_required=True,
                     commercial_qa_service=FakeCommercialQaService())
    client = TestClient(app)
    assert client.post("/login", data={"username": "operator", "password": "safe-password"},
                       follow_redirects=False).status_code == 303
    for path in ("/", "/suppliers", "/suppliers/-4"):
        page = client.get(path)
        assert page.status_code == 200
        assert 'href="/commercial-qa"' in page.text
        assert 'action="/suppliers/-4/start"' not in page.text
    blocked = client.post("/suppliers/-4/start", data={}, follow_redirects=False)
    assert blocked.status_code == 403
    assert 'href="/commercial-qa"' in blocked.text
    assert queue.enqueue_calls == 0
    manual = client.get("/commercial-qa")
    assert manual.status_code == 200
    assert 'name="dry_run"' in manual.text
    assert 'value="false"' not in manual.text
    assert 'name="full_manifest"' in manual.text
    assert 'id="commercial-full" disabled' in manual.text
