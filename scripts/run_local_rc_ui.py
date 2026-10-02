"""Manual localhost panel for the consolidated RC PostgreSQL.

The launcher never starts PostgreSQL, Docker, jobs, a scheduler or a crawler.
Ordinary control-plane writes remain blocked; only explicit commercial manual
jobs can use the separately gated RC write mode.
"""

from __future__ import annotations

import os
from pathlib import Path
import sys

import psycopg
import uvicorn

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.commercial_qa import CommercialQaService
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import PostgresControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.postgres import PostgresConfig
from universal_supplier.stage6e import InMemoryStage6EService


RC_ID = 7691270601420084116
RC_DB = "universal_supplier_server"
RC_PORT = 55449


class LocalRcReadOnlyStore(PostgresControlPlaneStore):
    """Five-namespace read model; all ordinary panel POST routes are blocked."""

    read_only = True

    @staticmethod
    def _label_source(row):
        row["data_source"] = "Локальная RC PostgreSQL: universal_supplier_server / 127.0.0.1:55449"
        if row.get("code") in {"partner_st", "optimum"}:
            row["source_reason"] = "Восстановленный исторический снимок; не текущий live refresh."
        elif row.get("code") == "beka_mak_tr":
            row["source_reason"] = "Официальный справочный источник; не коммерческая цена/наличие."
        return row

    def dashboard(self):
        rows = super().dashboard()
        return [self._label_source(row) for row in rows]

def _required(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing local RC UI setting: {name}")
    return value


def _runtime_password() -> str:
    path = Path(os.environ["LOCALAPPDATA"]) / "UniversalSupplier" / "rc-local" / "pgpass.conf"
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split(":", 4)
        if len(fields) == 5 and fields[:4] == ["127.0.0.1", "55449", RC_DB, "rc_runtime"]:
            return fields[4]
    raise RuntimeError("local RC runtime credential is unavailable")


def build_app():
    if os.environ.get("LOCAL_RC_UI_CONFIRM") != "YES":
        raise RuntimeError("LOCAL_RC_UI_CONFIRM=YES is required")
    username = _required("LOCAL_RC_UI_USERNAME")
    password = _required("LOCAL_RC_UI_PASSWORD")
    session_secret = _required("LOCAL_RC_UI_SESSION_SECRET")
    passfile = Path(os.environ["LOCALAPPDATA"]) / "UniversalSupplier" / "rc-local" / "pgpass.conf"
    os.environ["PGPASSFILE"] = str(passfile)
    config = PostgresConfig("127.0.0.1", RC_PORT, RC_DB, "rc_readonly", None, "disable")
    with psycopg.connect(**config.kwargs(), options="-c default_transaction_read_only=on") as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                           "pg_is_in_recovery(),system_identifier FROM pg_control_system()")
            if tuple(cursor.fetchone()) != (RC_DB, "rc_readonly", "17.11", False, RC_ID):
                raise RuntimeError("local RC PostgreSQL identity changed")
    os.environ.update({
        "COMMERCIAL_QA_UI_ENABLED": "YES",
        "COMMERCIAL_QA_UI_MANIFEST_PATH": str(ROOT / "reports" / "STAGE5D_2026-09-24" / "candidate_manifest_http_dry_run.json"),
        "COMMERCIAL_QA_FULL_MANIFEST_PATH": str(ROOT / "reports" / "WORKING_V1_2026-09-29" / "full_candidate_manifest.json"),
        "COMMERCIAL_QA_UI_ARTIFACTS_DIR": str(ROOT / "reports" / "RC_LOCAL" / "ui_runs"),
        "COMMERCIAL_QA_UI_DIAGNOSTIC_XML_PATH": str(ROOT / "reports" / "RC_LOCAL" / "DIAGNOSTIC_QA.xml"),
        "COMMERCIAL_QA_PROPOSALS_PATH": str(ROOT / "reports" / "RC_LOCAL" / "proposals" / "PROPOSAL_TABLES.json"),
        "COMMERCIAL_MANUAL_MODE": "SERVER",
        "COMMERCIAL_SERVER_REGISTRY_PATH": str(ROOT / "reports" / "STERBRUST_REGISTRY.csv"),
        "COMMERCIAL_SERVER_SECTIONS_PATH": str(ROOT / "reports" / "rest" / "STERBRUST_SECTIONS_RAW.json"),
        "COMMERCIAL_SERVER_RUN1_REPORT_PATH": str(ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1" / "report.json"),
        "COMMERCIAL_SERVER_PRIOR_REVIEW_REPORTS": os.pathsep.join(str(path) for path in (
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1" / "report.json",
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1_BEKAMAK_RESUME" / "report.json",
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1_OFFICIAL_RESUME" / "report.json",
            ROOT / "reports" / "RC_LOCAL" / "RUN1_INTERVESP_RESUME" / "report.json",
        )),
        "COMMERCIAL_SERVER_CAPTURE_REPORTS": os.pathsep.join(str(path) for path in (
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1" / "report.json",
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1_BEKAMAK_RESUME" / "report.json",
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1_OFFICIAL_RESUME" / "report.json",
            ROOT / "reports" / "WORKING_V1_2026-09-29" / "RUN1_INTERVESP" / "report.json",
            ROOT / "reports" / "RC_LOCAL" / "RUN1_INTERVESP_RESUME" / "report.json",
            ROOT / "reports" / "RC_LOCAL" / "RUN2_ALL" / "report.json",
        )),
        "DB_HOST": "127.0.0.1", "DB_PORT": str(RC_PORT), "DB_NAME": RC_DB,
        "DB_USER": "rc_runtime", "DB_SSLMODE": "disable", "DB_SYSTEM_IDENTIFIER": str(RC_ID),
    })
    if os.environ.get("COMMERCIAL_SERVER_WRITE_ENABLE") == "YES":
        os.environ["DB_PASSWORD"] = _runtime_password()
    else:
        os.environ.pop("DB_PASSWORD", None)
    service = CommercialQaService.from_env()
    if not service.enabled:
        raise RuntimeError(service.reason)
    admins = InMemoryAdminStore()
    admins.create_user(username, "Локальный RC-оператор", hash_password(password), "OPERATOR")
    return create_app(store=LocalRcReadOnlyStore(config), queue=InMemoryJobQueue(),
                      admin_store=admins, stage6e_service=InMemoryStage6EService(),
                      session_secret=session_secret, auth_required=True,
                      commercial_qa_service=service)


def main() -> None:
    uvicorn.run(build_app(), host="127.0.0.1", port=58097, log_level="info")


if __name__ == "__main__":
    main()
