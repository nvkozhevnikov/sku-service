from __future__ import annotations

import hashlib
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def test_compose_has_postgres_service_and_pinned_postgres_17_patch():
    assert "  postgres:" in COMPOSE
    assert "image: postgres:17.11" in COMPOSE
    assert "postgres:latest" not in COMPOSE


def test_compose_has_persistent_named_volume_on_pgdata():
    assert "universal_supplier_pgdata:/var/lib/postgresql/data" in COMPOSE
    assert "name: universal_supplier_pgdata" in COMPOSE


def test_compose_has_healthcheck_and_restart_policy():
    assert "healthcheck:" in COMPOSE
    assert "pg_isready" in COMPOSE
    assert "restart: unless-stopped" in COMPOSE


def test_default_port_is_loopback_only():
    assert '127.0.0.1:${POSTGRES_HOST_PORT:-5432}:5432' in COMPOSE
    assert "0.0.0.0:5432" not in COMPOSE


def test_runtime_role_is_distinct_and_restricted():
    values = dict(
        line.split("=", 1) for line in ENV_EXAMPLE.splitlines()
        if line and not line.startswith("#") and "=" in line
    )
    assert values["POSTGRES_ADMIN_USER"] != values["DB_USER"]
    init = (ROOT / "docker/postgres/init/01-create-application-role.sh").read_text(encoding="utf-8")
    for restriction in ("NOSUPERUSER", "NOCREATEDB", "NOCREATEROLE"):
        assert restriction in init


def test_scram_and_utf8_are_explicit():
    assert "--encoding=UTF8 --auth-host=scram-sha-256" in COMPOSE
    assert "password_encryption=scram-sha-256" in COMPOSE


def test_env_is_excluded_and_example_is_placeholders_only():
    assert ".env" in (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".env" in (ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
    assert "CHANGE_ME_ADMIN_PASSWORD" in ENV_EXAMPLE
    assert "CHANGE_ME_APPLICATION_PASSWORD" in ENV_EXAMPLE


def test_local_sslmode_matches_plain_local_docker():
    assert "DB_SSLMODE=disable" in ENV_EXAMPLE
    assert "DB_SSLMODE: disable" in COMPOSE


def test_migrations_001_011_are_immutable():
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    paths = sorted((ROOT / "migrations").glob("0[0-1][0-9]_*.sql"))
    paths = [path for path in paths if int(path.name[:3]) <= 11]
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert actual == baseline["files"]
    assert len(actual) == 11


def test_migration_runner_is_idempotent_and_checksum_guarded():
    source = (ROOT / "scripts/docker_postgres_migrate.py").read_text(encoding="utf-8")
    assert "schema_migrations" in source
    assert "if path.name in applied" in source
    assert "Applied migration checksum mismatch" in source


def test_trace_generator_reads_physical_sql_rows():
    source = (ROOT / "scripts/run_stage3d_docker_verification.py").read_text(encoding="utf-8")
    assert "FROM source_products sp" in source
    assert "JOIN offers o" in source
    assert "JOIN product_matches pm" in source
    assert "PHYSICAL_DB" not in source.upper() or "physical_db_row_ids_verified" in source
    assert "STAGE3C_SOURCE_DB_CONSISTENCY.csv" not in source


def test_no_destructive_volume_removal_in_helpers():
    helper_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (ROOT / "scripts").glob("docker_postgres_*")
    )
    assert "down -v" not in helper_sources
    assert "volume rm" not in helper_sources


def test_application_container_supports_cli_and_host_workflow_remains():
    assert (ROOT / "Dockerfile").is_file()
    assert 'entrypoint: ["python"]' in COMPOSE
    assert (ROOT / "scripts/run_partner_st_live_crawl.py").is_file()
