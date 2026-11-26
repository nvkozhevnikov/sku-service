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
