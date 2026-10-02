import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from universal_supplier.commercial_collection import CandidateManifest
from universal_supplier.control_plane.commercial_qa import CommercialQaService, CommercialQaUnavailable
from universal_supplier.full_supplier_collection import FullManifest, _candidate_sha, run_full_collection


def _server_env(monkeypatch, tmp_path):
    manifest = tmp_path / "bounded.json"
    manifest.write_text(json.dumps(CandidateManifest({"intervesp": (), "beka_mak": ()}).as_jsonable()),
                        encoding="utf-8")
    values = {
        "COMMERCIAL_QA_UI_ENABLED": "YES", "COMMERCIAL_MANUAL_MODE": "SERVER",
        "COMMERCIAL_QA_UI_MANIFEST_PATH": str(manifest),
        "COMMERCIAL_QA_UI_ARTIFACTS_DIR": str(tmp_path / "runs"),
        "COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH": str(tmp_path / "not-yet-created.xml"),
        "DB_HOST": "postgres", "DB_PORT": "5432", "DB_NAME": "universal_supplier_server",
        "DB_USER": "supplier_runtime", "DB_PASSWORD": "offline-test-only", "DB_SSLMODE": "disable",
        "DB_SYSTEM_IDENTIFIER": "123456789", "COMMERCIAL_SERVER_WRITE_ENABLE": "YES",
        "COMMERCIAL_SERVER_WRITE_CONFIRM": "universal_supplier_server",
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("COMMERCIAL_QA_WRITE_ENABLE", raising=False)
    return values


def test_server_manual_target_is_pinned_and_missing_xml_is_not_fabricated(monkeypatch, tmp_path):
    _server_env(monkeypatch, tmp_path)
    service = CommercialQaService.from_env()
    assert isinstance(service, CommercialQaService)
    assert service.server_target.dbname == "universal_supplier_server"
    assert service.server_system_identifier == 123456789
    assert service.write_repository_factory is not None
    assert service._xml_info()["sha256"] == ""
    with pytest.raises(CommercialQaUnavailable, match="ещё не сформирован"):
        service.diagnostic_xml()


def test_server_manual_target_refuses_mismatched_confirmation_and_qa_flags(monkeypatch, tmp_path):
    _server_env(monkeypatch, tmp_path)
    monkeypatch.setenv("COMMERCIAL_SERVER_WRITE_CONFIRM", "other_database")
    service = CommercialQaService.from_env()
    assert isinstance(service, CommercialQaService)
    assert service.write_repository_factory is None
    monkeypatch.setenv("COMMERCIAL_QA_WRITE_ENABLE", "YES")
    assert not CommercialQaService.from_env().enabled


def test_server_preflight_is_supplier_scoped_and_allows_historical_matches(monkeypatch, tmp_path):
    _server_env(monkeypatch, tmp_path)
    service = CommercialQaService.from_env()
    statements = []

    class Cursor:
        last = ""

        def __enter__(self): return self
        def __exit__(self, *_): return False
        def execute(self, query, *_):
            self.last = query
            statements.append(query)
            return self
        def fetchone(self):
            if "pg_control_system" in self.last:
                return ("universal_supplier_server", "supplier_runtime", False,
                        "17.11", "127.0.0.1", 5432, 123456789)
            if "rolsuper" in self.last:
                return (False, False, False)
            return (0,)
        def fetchall(self):
            return [("intervesp", False), ("beka_mak", False), ("beka_mak_tr", False)]

    repository = SimpleNamespace(gate=lambda: None, connection=SimpleNamespace(cursor=Cursor))
    result = service._check_write_target(repository, "all")
    assert result["system_identifier"] == 123456789
    assert any("JOIN suppliers" in sql and "o.active" in sql for sql in statements)
    assert not any("FROM product_matches" in sql for sql in statements)
    assert not any("catalog_product_id IS NOT NULL" in sql for sql in statements)


def test_full_collection_uses_explicit_server_validator_before_any_get(tmp_path):
    candidates = ()
    manifest = FullManifest(candidates, {}, (), _candidate_sha(candidates))
    calls = []
    repository = object()
    with patch("universal_supplier.full_supplier_collection.verify_qa_identity",
               side_effect=AssertionError("Stage 4 validator must not run")):
        result = run_full_collection(
            manifest=manifest, repository=repository, evidence_dir=tmp_path / "evidence",
            report_path=tmp_path / "report.json", dry_run=False,
            write_identity_validator=lambda target: calls.append(target) or {"database": "server"},
        )
    assert calls == [repository]
    assert result["rows"] == []
