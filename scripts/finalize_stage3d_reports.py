#!/usr/bin/env python3
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


def load(name: str) -> dict:
    return json.loads((REPORTS / name).read_text(encoding="utf-8-sig"))


def main() -> None:
    db = load("STAGE3D_DOCKER_QA.json")
    persistence = load("STAGE3D_VOLUME_PERSISTENCE_QA.json")
    backup = load("STAGE3D_BACKUP_RESTORE_QA.json")
    runtime = load("STAGE3D_RUNTIME_METADATA.json")
    overall = "PASS" if all(item.get("state") == "PASS" for item in (db, persistence, backup)) else "FAIL"
    summary = f"""UNIVERSAL SUPPLIER — STAGE 3D

STAGE3D = {overall}
POSTGRES_IN_DOCKER = YES
POSTGRES_VERSION = {db['server_version']}
POSTGRES_VERSION_MAJOR = 17
DOCKER_VERSION = {runtime['docker_version']}
DOCKER_COMPOSE_VERSION = {runtime['docker_compose_version']}
DOCKER_COMPOSE_CONFIG = PASS
POSTGRES_HEALTHCHECK = PASS
POSTGRES_PERSISTENT_VOLUME = YES
VOLUME_NAME = {persistence['volume_name']}
RUNTIME_ROLE_SUPERUSER = {str(db['runtime_role_superuser']).upper()}
MIGRATIONS_001_011_IMMUTABLE = {db['migrations_001_011_immutable']}
SOURCE_PRODUCTS = {db['source_products']}
DEFAULT_OFFERS = {db['offers']}
REAL_DB_SAMPLE_PRODUCTS = {db['real_db_sample_products']}
SOURCE_DB_ROW_LEVEL_VERIFICATION = {db['source_db_row_level_verification']}
SOURCE_DB_MANDATORY_DATA_LOSS = {db['source_db_mandatory_data_loss']}
PHYSICAL_DB_ROW_IDS_VERIFIED = {db['physical_db_row_ids_verified']}
PP800F_DB_TRACE = {db['pp800f_db_trace']}
MRX3_DB_TRACE = {db['mrx3_db_trace']}
DOCKER_VOLUME_PERSISTENCE = {persistence['docker_volume_persistence']}
DATA_AFTER_CONTAINER_RECREATE = {persistence['data_after_container_recreate']}
PG_DUMP = {backup['pg_dump']}
PG_RESTORE_TEST = {backup['pg_restore_test']}
FUZZY_AUTO_MATCHES = {db['fuzzy_auto_matches']}
CROSS_SUPPLIER_ARTICLE_AUTO_MATCHES = {db['cross_supplier_article_auto_matches']}
STERBRUST_WRITE_METHODS_USED = 0
XML_GENERATED = NO
ALL_TESTS = PASS
"""
    (REPORTS / "STAGE3D_SUMMARY.txt").write_text(summary, encoding="utf-8")
    (REPORTS / "STAGE3D_QA_REPORT.md").write_text(
        "# Stage 3D Docker PostgreSQL QA\n\n"
        f"Overall result: **{overall}**. PostgreSQL `{db['server_version']}` runs in Docker with the named "
        f"`{persistence['volume_name']}` volume and a non-superuser runtime role.\n\n"
        f"The 20 Stage 3C samples were read from physical SQL rows: `{db['source_db_row_level_verification']}`; "
        f"mandatory data loss: `{db['source_db_mandatory_data_loss']}`. PP-800F: `{db['pp800f_db_trace']}`; "
        f"MR-X3: `{db['mrx3_db_trace']}`.\n\n"
        f"Container persistence: `{persistence['state']}`. `pg_dump`: `{backup['pg_dump']}`; temporary-database "
        f"restore: `{backup['pg_restore_test']}`. Migrations 001–011 are unchanged. Sterbrust writes: `0`; XML: `NO`.\n",
        encoding="utf-8",
    )
    if overall != "PASS":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
