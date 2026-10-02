from __future__ import annotations

from universal_supplier.control_plane.combined_readonly_store import CombinedReadOnlyStore
from universal_supplier.control_plane.main_snapshot_store import (
    MAIN_SNAPSHOT_DATABASE,
    MAIN_SNAPSHOT_HOST,
    MAIN_SNAPSHOT_PORT,
    MAIN_SNAPSHOT_ROLE,
    ReadOnlyMainSnapshotStore,
    main_snapshot_store_from_env,
)
from scripts import run_commercial_qa_ui
from types import SimpleNamespace


class FakeStore:
    read_only = True

    def __init__(self, row):
        self.row = row

    def suppliers(self):
        return [self.row]

    def dashboard(self):
        return [self.row]

    def filter_options(self):
        return {"suppliers": [{"code": self.row["code"], "name": self.row["name"]}], "brands": [], "decisions": (), "kinds": []}

    def product(self, code, external_id):
        return {"source": self.row["name"], "external_id": external_id}

    def product_history(self, code, external_id):
        return []

    def products(self, filters):
        return {"items": [], "total": 0, "page": filters.page, "page_size": filters.page_size, "pages": 1}

    def health(self):
        return {"postgresql": "historical_read_only"}

    def runs(self, limit=100):
        return []


def test_main_snapshot_source_requires_explicit_opt_in(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_QA_MAIN_SNAPSHOT_SOURCE", raising=False)
    assert main_snapshot_store_from_env() is None
    monkeypatch.setenv("COMMERCIAL_QA_MAIN_SNAPSHOT_SOURCE", "restored_snapshot")
    store = main_snapshot_store_from_env()
    assert isinstance(store, ReadOnlyMainSnapshotStore)
    assert store.read_only is True
    assert (store.config.host, store.config.port, store.config.dbname, store.config.user) == (
        MAIN_SNAPSHOT_HOST, MAIN_SNAPSHOT_PORT, MAIN_SNAPSHOT_DATABASE, MAIN_SNAPSHOT_ROLE)


def test_combined_store_prefers_real_owner_over_unavailable_placeholder():
    placeholder = FakeStore({"code": "beka_mak", "name": "Beka-Mak", "products": None})
    owner = FakeStore({"code": "beka_mak", "name": "Beka-Mak", "products": 44})
    combined = CombinedReadOnlyStore(placeholder, owner)
    assert combined.product("beka_mak", "19240")["source"] == "Beka-Mak"
    assert len(combined.filter_options()["suppliers"]) == 1


def test_ui_never_attaches_a_database_with_wrong_system_identity(monkeypatch):
    monkeypatch.setenv("COMMERCIAL_QA_UI_CONFIRM", "YES")
    monkeypatch.setenv("COMMERCIAL_QA_UI_USERNAME", "test")
    monkeypatch.setenv("COMMERCIAL_QA_UI_PASSWORD", "local-test-only")
    monkeypatch.setenv("COMMERCIAL_QA_UI_SESSION_SECRET", "a" * 64)
    qa = SimpleNamespace(health=lambda: {"postgresql": "error"})
    historical = SimpleNamespace(health=lambda: {"postgresql": "historical_read_only"})
    monkeypatch.setattr(run_commercial_qa_ui.CommercialQaService, "from_env",
                        lambda: SimpleNamespace(enabled=True))
    monkeypatch.setattr(run_commercial_qa_ui, "stage4_qa_store_from_env", lambda: qa)
    monkeypatch.setattr(run_commercial_qa_ui, "main_snapshot_store_from_env", lambda: historical)
    monkeypatch.setattr(run_commercial_qa_ui, "create_app", lambda **values: values["store"])
    assert run_commercial_qa_ui.build_app() is historical
