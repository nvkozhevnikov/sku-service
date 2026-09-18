from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from package_stage6e1 import forbidden as stage6e1_forbidden
from package_stage6e2 import forbidden as stage6e2_forbidden
from package_xml_admin import forbidden as xml_forbidden
from package_stage6e_final import forbidden as final_forbidden
from test_stage6e import csrf
from fastapi.testclient import TestClient
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import PostgresControlPlaneStore, SnapshotControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.stage6e import AnomalyThresholds
from universal_supplier.yml_feed import PostgresFeedStore


@pytest.mark.parametrize(("values"), [
    {"count_ratio_min": 0}, {"count_ratio_min": 1.01},
    {"fill_rate_drop": -0.01}, {"fill_rate_drop": 1.01},
    {"fill_rate_min": -0.01}, {"fill_rate_min": 1.01},
])
def test_anomaly_threshold_validation(values):
    with pytest.raises(ValueError):
        AnomalyThresholds(**values)


def test_anomaly_threshold_defaults_valid():
    assert AnomalyThresholds() == AnomalyThresholds(.60, .35, .25)


def test_invalid_quality_policy_is_not_saved_and_has_russian_error():
    class Store(SnapshotControlPlaneStore):
        def _connect(self):
            raise AssertionError("invalid policy must be rejected before database access")
        def save_data_quality_policy(self, supplier_id, values):
            return PostgresControlPlaneStore.save_data_quality_policy(self, supplier_id, values)

    store = Store(ROOT / "reports")
    admin = InMemoryAdminStore([{"id": 1, "username": "admin", "display_name": "Admin", "role": "ADMIN", "is_active": True,
                                 "password_hash": hash_password("Safe-password-2026", salt=b"0123456789abcdef")}])
    client = TestClient(create_app(store=store, queue=InMemoryJobQueue(), admin_store=admin,
                                   session_secret="stage6e-test-secret-at-least-32-bytes", auth_required=True))
    assert client.post("/login", data={"username": "admin", "password": "Safe-password-2026"}, follow_redirects=False).status_code == 303
    response = client.post("/suppliers/1/data-quality-policy", data={
        "count_ratio_min": "0", "fill_rate_drop": "0.2", "fill_rate_min": "0.1",
        "csrf_token": csrf(client),
    })
    assert response.status_code < 500
    assert "Настройки качества не сохранены" in response.text


@pytest.mark.parametrize("checker", [stage6e1_forbidden, stage6e2_forbidden, xml_forbidden, final_forbidden])
def test_package_preview_backup_and_database_dumps_excluded(checker):
    assert checker(Path("preview-backup/universal_supplier_preview.sql"))
    assert checker(Path("backups/current.sql"))
    assert checker(Path("tmp/export.dump"))
    assert checker(Path("temp/export.backup"))


@pytest.mark.parametrize("checker", [stage6e1_forbidden, stage6e2_forbidden, xml_forbidden, final_forbidden])
def test_package_legitimate_project_sql_preserved(checker):
    assert not checker(Path("migrations/014_stage6e_operator_data_tools.sql"))
    assert not checker(Path("tests/postgres_integrity_tests.sql"))
    assert not checker(Path("schema.sql"))


def test_docker_runtime_contains_yml_policy():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY config ./config" in dockerfile
    assert "test -f /app/config/yml_feed.json" in dockerfile
    assert (ROOT / "config/yml_feed.json").is_file()


def test_preview_selects_source_only_by_exact_working_volume_mount():
    script = (ROOT / "scripts/start_admin_preview.ps1").read_text(encoding="utf-8")
    assert '$mount.Name -eq $WorkingVolume' in script
    assert '$mount.Destination -eq "/var/lib/postgresql/data"' in script
    assert 'if ($name -match "postgres")' not in script


def test_preview_database_is_detected_by_complete_schema():
    script = (ROOT / "scripts/start_admin_preview.ps1").read_text(encoding="utf-8")
    for table in ("source_products", "offers", "catalog_products", "catalog_offer_selection"):
        assert f"public.{table}" in script
    assert "Найдено несколько неразличимых баз со схемой" in script
    assert "$databaseCandidates -contains $MaintenanceDatabase" in script
    assert "pg_stat_database" in script and "xact_commit" in script
    assert "$ranked[0].Commits -gt $ranked[1].Commits" in script


def test_preview_has_no_silent_packaged_fallback_and_is_ps51_compatible():
    script = (ROOT / "scripts/start_admin_preview.ps1").read_text(encoding="utf-8")
    assert "falling back" not in script.lower()
    assert "Get-Content $dump -AsByteStream" not in script
    assert "docker cp" in script and "pg_dump" in script and "pg_restore" in script
    assert "Assert-LoopbackPortAvailable $PreviewPort" in script
    assert "Stop-PreviewWebQuietly" in script


def test_preview_clean_restore_and_working_volume_never_deleted():
    start = (ROOT / "scripts/start_admin_preview.ps1").read_text(encoding="utf-8")
    stop = (ROOT / "scripts/stop_admin_preview.ps1").read_text(encoding="utf-8")
    assert "DROP DATABASE IF EXISTS $PreviewDatabase" in start
    assert "CREATE DATABASE $PreviewDatabase OWNER $PreviewRole" in start
    assert "docker compose down -v" not in (start + stop)
    assert "docker volume rm $WorkingVolume" not in (start + stop)
    assert "docker volume rm $PreviewVolume" in stop


def test_admin_unicode_uses_utf8_base64_transport_without_persisting_password():
    helper = (ROOT / "scripts/create_preview_admin.ps1").read_text(encoding="utf-8")
    create = (ROOT / "scripts/create_admin.py").read_text(encoding="utf-8")
    assert "ToBase64String" in helper and "FIRST_ADMIN_DISPLAY_NAME_B64" in helper
    assert "base64.b64decode" in create and 'decode("utf-8")' in create
    assert "Remove-Item Env:FIRST_ADMIN_PASSWORD" in helper


class _TimestampCursor:
    def __init__(self, values):
        self.values = iter(values)
        self.queries = []
    def execute(self, query):
        self.queries.append(" ".join(query.split()))
    def fetchone(self):
        return (next(self.values),)


def test_xml_legacy_null_evaluated_at_uses_persisted_selected_state_fallback():
    expected = datetime(2026, 9, 15, 12, 0, tzinfo=timezone.utc)
    cursor = _TimestampCursor([None, None, expected])
    assert PostgresFeedStore._resolve_snapshot_timestamp(cursor) == expected
    assert len(cursor.queries) == 3 and "greatest(o.updated_at" in cursor.queries[-1]


def test_xml_timestamp_prefers_selection_and_then_stage5_feed_run():
    evaluated = datetime(2026, 9, 16, 8, 0, tzinfo=timezone.utc)
    cursor = _TimestampCursor([evaluated])
    assert PostgresFeedStore._resolve_snapshot_timestamp(cursor) == evaluated
    assert len(cursor.queries) == 1
    feed_run = datetime(2026, 9, 16, 7, 0, tzinfo=timezone.utc)
    cursor = _TimestampCursor([None, feed_run])
    assert PostgresFeedStore._resolve_snapshot_timestamp(cursor) == feed_run
    assert len(cursor.queries) == 2


def test_xml_timestamp_fails_closed_when_no_persisted_timestamp_exists():
    cursor = _TimestampCursor([None, None, None])
    assert PostgresFeedStore._resolve_snapshot_timestamp(cursor) is None
