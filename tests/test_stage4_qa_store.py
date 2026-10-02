from __future__ import annotations

from universal_supplier.control_plane.stage4_qa_store import (
    ReadOnlyStage4QaStore,
    STAGE4_DATABASE,
    STAGE4_HOST,
    STAGE4_PORT,
    STAGE4_ROLE,
    stage4_qa_store_from_env,
)


def test_stage4_store_is_explicit_and_does_not_enable_itself(monkeypatch):
    monkeypatch.delenv("COMMERCIAL_QA_DATA_SOURCE", raising=False)
    assert stage4_qa_store_from_env() is None
    monkeypatch.setenv("COMMERCIAL_QA_DATA_SOURCE", "stage4_readonly")
    store = stage4_qa_store_from_env()
    assert isinstance(store, ReadOnlyStage4QaStore)
    assert store.read_only is True
    assert (store.config.host, store.config.port, store.config.dbname, store.config.user) == (
        STAGE4_HOST, STAGE4_PORT, STAGE4_DATABASE, STAGE4_ROLE)


def test_unavailable_sources_are_explicit_not_zero_products():
    rows = ReadOnlyStage4QaStore._supplement_sources(
        object.__new__(ReadOnlyStage4QaStore),
        [ReadOnlyStage4QaStore._unavailable_row("intervesp", "Intervesp", "offline"),
         ReadOnlyStage4QaStore._unavailable_row("beka_mak", "Beka-Mak", "offline")],
    )
    by_code = {row["code"]: row for row in rows}
    assert by_code["partner_st"]["products"] is None
    assert by_code["optimum"]["last_status"] == "SOURCE_NOT_CONNECTED"


def test_stage4_store_blocks_all_mutators_without_connecting():
    store = object.__new__(ReadOnlyStage4QaStore)
    try:
        store.set_supplier_enabled(1, True)
    except RuntimeError as error:
        assert "read-only" in str(error)
    else:
        raise AssertionError("read-only store accepted a write")
