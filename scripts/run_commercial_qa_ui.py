"""Run the existing control-plane UI in the fixed local QA mode.

This launcher is intentionally manual. It never starts Docker, PostgreSQL,
migrations, orchestrator or scheduler. Read-only views can connect to the
two fixed local databases. An explicit two-flag mode allows operator-confirmed
writes only to the Stage 4 QA cluster; public GETs begin only after a manual
UI action.
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

import uvicorn

# Direct execution puts ``scripts`` rather than the repository root on
# sys.path.  The launcher intentionally has no installation step.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.commercial_qa import CommercialQaService
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.stage4_qa_store import stage4_qa_store_from_env
from universal_supplier.control_plane.main_snapshot_store import main_snapshot_store_from_env
from universal_supplier.control_plane.combined_readonly_store import CombinedReadOnlyStore
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.stage6e import InMemoryStage6EService


def required(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise RuntimeError(f"Missing required environment variable: {name}")
    return value


def build_app():
    if os.environ.get("COMMERCIAL_QA_UI_CONFIRM", "NO").upper() != "YES":
        raise RuntimeError("Set COMMERCIAL_QA_UI_CONFIRM=YES for this local QA UI only")
    username = required("COMMERCIAL_QA_UI_USERNAME")
    password = required("COMMERCIAL_QA_UI_PASSWORD")
    secret = required("COMMERCIAL_QA_UI_SESSION_SECRET")
    service = CommercialQaService.from_env()
    if not service.enabled:
        raise RuntimeError(service.reason)
    admins = InMemoryAdminStore()
    admins.create_user(username, "Локальный QA-оператор", hash_password(password), "OPERATOR")
    qa_store = stage4_qa_store_from_env()
    main_store = main_snapshot_store_from_env()
    # Do not retain an unavailable source in the combined store: paging a
    # historical catalog must not wait for a failed TCP connection on every page.
    if qa_store and qa_store.health().get("postgresql") != "qa_read_only":
        qa_store = None
    if main_store and main_store.health().get("postgresql") != "historical_read_only":
        main_store = None
    store = CombinedReadOnlyStore(*[item for item in (main_store, qa_store) if item]) if main_store and qa_store else (qa_store or main_store) or UnavailableControlPlaneStore(
        "Локальный Stage 5D QA: PostgreSQL не подключён"
    )
    return create_app(store=store,
                      queue=InMemoryJobQueue(), admin_store=admins,
                      stage6e_service=InMemoryStage6EService(), session_secret=secret,
                      auth_required=True, commercial_qa_service=service)


def main() -> None:
    app = build_app()
    host = os.environ.get("COMMERCIAL_QA_UI_HOST", "127.0.0.1")
    port = int(os.environ.get("COMMERCIAL_QA_UI_PORT", "58097"))
    uvicorn.run(app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    main()
