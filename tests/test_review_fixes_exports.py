"""Regression tests for the 2026-10-18 code-review fixes (exports, schema, migrations)."""
from __future__ import annotations

import csv
import hashlib
import importlib.util
import io
import json
import sys
import types
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest

from universal_supplier.stage6e import (
    csv_bytes, csv_chunks, product_workbook, sanitize_xml_tree, strip_xml_illegal, xlsx_bytes,
)
from universal_supplier import yml_feed
from universal_supplier.xml_export import supplier_xml_bytes
from universal_supplier.supplier_neutral_xml import neutral_snapshot_xml

ROOT = Path(__file__).resolve().parents[1]
CONTROL = "A\x00B\x01C\x08D\x0bE\x0cF\x0eG\x1fH￾I￿J"
CLEAN = "ABCDEFGHIJ"
COLUMNS = (("name", "Название"), ("price", "Цена"), ("quantity", "Количество"))


def _csv_cells(data: bytes) -> list[list[str]]:
    return list(csv.reader(io.StringIO(data.decode("utf-8-sig"), newline="")))


# 1. formula injection --------------------------------------------------------

@pytest.mark.parametrize("value", ["=HYPERLINK(\"http://x\")", "+1+1", "-2+3", "@SUM(A1)", "\tcmd", "\rcmd"])
def test_csv_string_cells_starting_with_formula_prefix_are_neutralised(value):
    cells = _csv_cells(csv_bytes([{"name": value, "price": None, "quantity": None}], COLUMNS))
    assert cells[1][0] == "'" + value


def test_csv_genuine_numbers_and_plain_text_are_unchanged():
    row = {"name": "Станок PP-800F", "price": Decimal("-10.50"), "quantity": -3}
    cells = _csv_cells(csv_bytes([row], COLUMNS))
    assert cells[1] == ["Станок PP-800F", "-10.50", "-3"]
    streamed = b"".join(csv_chunks([row], COLUMNS))
    assert _csv_cells(streamed)[1] == ["Станок PP-800F", "-10.50", "-3"]


def test_yml_feed_trace_csv_neutralises_formula_strings_only():
    data = yml_feed.csv_bytes([{"sterbrust_name": "=cmd|' /C calc'!A0", "price": "-1", "quantity": -1}],
                              ["sterbrust_name", "price", "quantity"])
    rows = list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"), newline="")))
    assert rows[0]["sterbrust_name"] == "'=cmd|' /C calc'!A0"
    assert rows[0]["price"] == "'-1"  # a *string* starting with '-' is neutralised
    assert rows[0]["quantity"] == "-1"  # a real number is not


# 2/3. XML-illegal characters ---------------------------------------------------

def test_strip_xml_illegal_keeps_tab_newline_and_unicode():
    assert strip_xml_illegal(CONTROL) == CLEAN
    assert strip_xml_illegal("a\tb\nc\rд€") == "a\tb\nc\rд€"


def test_xlsx_with_control_characters_and_formulas_does_not_raise():
    from openpyxl import load_workbook
    data = xlsx_bytes({"Товары": (COLUMNS, [{"name": CONTROL, "price": Decimal("-5"), "quantity": 2},
                                             {"name": "=1+1", "price": None, "quantity": None}])})
    sheet = load_workbook(io.BytesIO(data)).active
    assert sheet["A2"].value == CLEAN
    assert sheet["B2"].value == -5
    assert sheet["A3"].value == "'=1+1" and sheet["A3"].data_type == "s"
    assert product_workbook([{"name": CONTROL}])


def test_full_supplier_xml_strips_illegal_characters():
    row = {"supplier_code": "partner_st", "source_product_id": 1, "external_id": "E\x01",
           "name": CONTROL, "category": "Cat\x0b"}
    root = ET.fromstring(supplier_xml_bytes([row], lambda ids: {}))
    product = root.find(".//product")
    assert product.get("external_id") == "E"
    assert CLEAN in ET.tostring(root, encoding="unicode")


def test_supplier_neutral_xml_strips_illegal_characters():
    record = {"supplier_code": "intervesp", "external_id": "id\x02", "name": CONTROL,
              "price_state": "price_on_request", "price": None,
              "raw_data": {"source_content": {"description_text": "d\x1f", "source_category": "c\x0c",
                                               "source_properties": [["p\x01", "v\x02"]]}}}
    root = ET.fromstring(neutral_snapshot_xml([record]))
    assert root.find(".//name").text == CLEAN
    assert root.find(".//source_product").get("external_id") == "id"


def test_sanitize_xml_tree_cleans_text_tail_and_attributes():
    root = ET.Element("r", {"a": "x\x01"}); child = ET.SubElement(root, "c"); child.text = "t\x02"; child.tail = "u\x03"
    sanitize_xml_tree(root)
    assert ET.fromstring(ET.tostring(root)).get("a") == "x"
    assert (child.text, child.tail) == ("t", "u")


def test_yml_feed_strips_illegal_characters_in_names_categories_and_vendor():
    sys.path.insert(0, str(ROOT / "tests"))
    from test_stage5 import POLICY, row, snapshot
    item = row(sterbrust_name="Станок\x01 PP", sterbrust_category_name="Кат\x0bегория", sterbrust_brand="Par\x02tner")
    artifact = yml_feed.build_yml(snapshot(item), POLICY)
    root = ET.fromstring(artifact.data)
    assert root.find("./shop/offers/offer/name").text == "Станок PP"
    assert root.find("./shop/categories/category").text == "Категория"
    assert root.find("./shop/offers/offer/vendor").text == "Partner"


# 5. release export keeps exact decimal text ------------------------------------

def test_release_offers_numeric_price_is_exact_text_not_float():
    from universal_supplier.release_export import tables
    row = {"source": "intervesp", "external_id": "x", "classification": "EXISTING_CONFIRMED",
           "price_state": "numeric_public", "price": "1234567.8900", "currency": "RUB"}
    offers = tables([row], [])["Offers"]
    assert offers[0]["numeric_price"] == "1234567.8900"
    assert isinstance(offers[0]["numeric_price"], str)
    json.dumps(offers)  # consumers serialise these tables to JSON


# 4. migration runner atomicity ------------------------------------------------

def _load_migrate():
    spec = importlib.util.spec_from_file_location("docker_postgres_migrate_under_test",
                                                  ROOT / "scripts/docker_postgres_migrate.py")
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def test_migration_body_strips_only_outer_wrapper_for_every_migration():
    migrate = _load_migrate()
    for path in migrate.MIGRATIONS:
        original = path.read_text(encoding="utf-8")
        body = migrate.migration_body(original)
        assert "BEGIN;" not in body.splitlines()[:3] and not body.rstrip().endswith("COMMIT;"), path.name
        assert body.strip() in original
    assert migrate.migration_body("CREATE TABLE t();\n") == "CREATE TABLE t();\n"
    with pytest.raises(RuntimeError):
        migrate.migration_body("BEGIN;\nCREATE TABLE a();\nCOMMIT;\nCREATE TABLE b();\nCOMMIT;\n")


class _FakeCursor:
    def __init__(self, log, fail_on):
        self.log, self.fail_on = log, fail_on

    def __enter__(self): return self
    def __exit__(self, *exc): return False

    def execute(self, sql, params=None):
        self.log.append(("execute", sql.strip()[:40], params))
        if self.fail_on and self.fail_on in sql:
            raise RuntimeError("migration failed")

    def fetchall(self): return []


class _FakeConnection:
    def __init__(self, log, fail_on):
        self.log, self.fail_on = log, fail_on

    def __enter__(self): return self
    def __exit__(self, *exc): return False
    def cursor(self): return _FakeCursor(self.log, self.fail_on)

    @contextmanager
    def transaction(self):
        self.log.append(("begin",))
        try:
            yield
        except BaseException:
            self.log.append(("rollback",)); raise
        self.log.append(("commit",))


def _run_migrate(monkeypatch, fail_on=None):
    migrate = _load_migrate()
    log: list = []
    fake = types.SimpleNamespace(connect=lambda **kwargs: _FakeConnection(log, fail_on))
    monkeypatch.setitem(sys.modules, "psycopg", fake)
    monkeypatch.setattr(migrate.PostgresConfig, "from_env", classmethod(lambda cls: types.SimpleNamespace(kwargs=lambda: {})))
    return migrate, log


def test_migration_file_and_its_record_commit_together(monkeypatch):
    migrate, log = _run_migrate(monkeypatch)
    migrate.main()
    inserts = [index for index, entry in enumerate(log) if entry[0] == "execute" and entry[1].startswith("INSERT INTO schema_migrations")]
    assert len(inserts) == len(migrate.MIGRATIONS)
    for index in inserts:
        assert log[index - 2] == ("begin",) and log[index + 1] == ("commit",)
    recorded = [entry[2] for entry in log if entry[0] == "execute" and entry[1].startswith("INSERT")]
    assert recorded == [(path.name, hashlib.sha256(path.read_bytes()).hexdigest()) for path in migrate.MIGRATIONS]


def test_failed_migration_is_not_recorded(monkeypatch):
    migrate, log = _run_migrate(monkeypatch, fail_on="CREATE TABLE suppliers")
    with pytest.raises(RuntimeError, match="migration failed"):
        migrate.main()
    assert ("rollback",) in log
    assert not any(entry[0] == "execute" and entry[1].startswith("INSERT") for entry in log)


# 7/8. schema.sql and migration baseline ----------------------------------------

def test_schema_sql_is_concatenation_of_all_migrations_including_016():
    schema = (ROOT / "schema.sql").read_text(encoding="utf-8")
    paths = sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql"))
    assert paths[-1].name.startswith("016_")
    for path in paths:
        assert f"-- ===== {path.name} =====\n" + path.read_text(encoding="utf-8") in schema
    assert "CREATE INDEX IF NOT EXISTS review_decisions_source_product_idx ON review_decisions (source_product_id);" in schema


def test_stage6e_baseline_pins_001_015_with_runner_checksums():
    baseline = json.loads((ROOT / "stage6e_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    assert sorted(baseline["files"]) == [p.name for p in sorted((ROOT / "migrations").glob("0[01][0-9]_*.sql")) if int(p.name[:3]) <= 15]
    migrate = _load_migrate()
    for name, digest in baseline["files"].items():
        assert migrate.sha256(ROOT / "migrations" / name) == digest


# 6/9. operational scripts --------------------------------------------------------

def test_backup_scripts_write_partial_then_rename():
    for name in ("docker_postgres_backup.sh", "docker_postgres_backup.ps1"):
        text = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert '-f "/backups/$1.partial"' in text and 'mv "/backups/$1.partial" "/backups/$1"' in text
    shell = (ROOT / "scripts/docker_postgres_backup.sh").read_text(encoding="utf-8")
    assert 'BACKUP_DIR="$ROOT/backups"' in shell and "${1:-" not in shell


def test_application_role_password_is_not_passed_on_psql_argv():
    init = (ROOT / "docker/postgres/init/01-create-application-role.sh").read_text(encoding="utf-8")
    assert "--set=app_db_password" not in init
    assert "\\getenv app_db_password APP_DB_PASSWORD" in init
