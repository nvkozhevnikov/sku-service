from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
from unittest.mock import patch

from universal_supplier.proposal_export import build_proposal_tables, capture_review_rows, new_candidate_id
from scripts.generate_proposed_supplier_files import _all_registry_model_keys, _xml
import xml.etree.ElementTree as ET


def _row(status="EXISTING_CONFIRMED", **updates):
    value = {"source": "beka_mak", "external_id": "19240", "offer_id": 1,
             "source_url": "https://beka-mak.su/product/bms_230_dg/",
             "model": "BMS-230DG", "model_key": "BMS-230DG",
             "name": "BMS-230DG", "classification": status,
             "sterbrust_product_id": "17128" if status == "EXISTING_CONFIRMED" else "",
             "source_category": "Автоматические ленточнопильные станки",
             "price_state": "numeric_public", "price": "772191", "currency": "RUB",
             "availability": "in_stock", "observed_at": "2026-09-29T12:00:00+00:00"}
    value.update(updates)
    return value


def test_capture_review_keeps_unresolved_and_discards_later_resolved_status():
    urls = {("beka_mak_tr", "https://www.bekamak.com/urun/a/en"),
            ("beka_mak_tr", "https://www.bekamak.com/urun/b/en")}
    old = {"rows": [{"source": "beka_mak_tr", "url": "https://www.bekamak.com/urun/a/en",
                     "expected_model": "A", "status": "REVIEW", "diagnostics": ["missing specs"]},
                    {"source": "beka_mak_tr", "url": "https://www.bekamak.com/urun/b/en",
                     "expected_model": "B", "status": "AMBIGUOUS", "diagnostics": ["title conflict"]}]}
    later = {"rows": [{"source": "beka_mak_tr", "url": "https://www.bekamak.com/urun/a/en",
                       "status": "PERSISTED"}]}
    result = capture_review_rows(allowed_urls=urls,
                                 discovery_reviews=[{"source": "beka_mak_tr",
                                                     "url": "https://www.bekamak.com/urun/c_random/en",
                                                     "reason": "unstable_official_model_slug"}],
                                 reports=[old, later])
    assert len(result) == 2
    assert {row["source_url"] for row in result} == {
        "https://www.bekamak.com/urun/b/en", "https://www.bekamak.com/urun/c_random/en"}


def test_existing_update_is_proposed_only_and_uses_fresh_numeric_offer():
    older = _row(source="intervesp", external_id="8992", offer_id=2,
                 price="1", observed_at="2026-09-20T12:00:00+00:00")
    result = build_proposal_tables([_row(), older], {"17128": {"name": "Sterbrust card"}},
                                   freshness_after=datetime(2026, 9, 29, tzinfo=timezone.utc))
    assert len(result["existing"]) == 1
    assert result["existing"][0]["selected_source_proposed"] == "beka_mak"
    assert result["existing"][0]["selection_status"] == "PROPOSED_ONLY_SUPPLIERS_DISABLED"
    assert len(result["existing"][0]["all_source_offers"]) == 2


def test_new_requires_exact_absence_and_active_section_with_real_path():
    row = _row("NEW_CANDIDATE")
    sections = {6769: {"name": "Станки для резки", "active": "Y", "iblockSectionId": None},
                4004: {"name": "Ленточнопильные станки по металлу", "active": "Y", "iblockSectionId": 6769},
                4074: {"name": "Автоматические ленточнопильные станки", "active": "Y", "iblockSectionId": 4004}}
    result = build_proposal_tables([row], {}, registry_model_keys=set(), sections=sections)
    assert result["new"][0]["section_id"] == 4074
    assert result["new"][0]["sterbrust_product_id"] is None
    assert result["new"][0]["new_candidate_id"] == new_candidate_id("beka_mak", "19240", "BMS-230DG")
    assert result["new"][0]["external_id"] == "19240"
    assert result["new"][0]["section_path"].startswith("Станки для резки / ")
    result = build_proposal_tables([row], {}, registry_model_keys={"BMS-230DG"}, sections=sections)
    assert result["new"] == []
    assert result["review"][0]["export_status"] == "REVIEW"


def test_new_candidate_id_is_stable_supplier_scoped_and_not_canonical():
    first = new_candidate_id("beka_mak", "19240", "bms230dg")
    assert first == new_candidate_id("beka_mak", "19240", "bms230dg")
    assert first != new_candidate_id("intervesp", "19240", "bms230dg")
    assert first.startswith("NEWC-") and not first.isdigit()


def test_cross_source_new_model_remains_review_without_identity_proof():
    rows = [_row("NEW_CANDIDATE"), _row("NEW_CANDIDATE", source="intervesp", external_id="8992")]
    result = build_proposal_tables(rows, {}, registry_model_keys=set(), sections={})
    assert result["new"] == []
    assert len(result["review"]) == 2
    assert {row["export_reason"] for row in result["review"]} == {"cross_source_new_identity_unconfirmed"}


def test_conflict_never_enters_update_or_new():
    result = build_proposal_tables([_row("CONFLICT")], {})
    assert not result["existing"] and not result["new"]
    assert result["review"][0]["export_status"] == "CONFLICT"


def test_diagnostic_xml_keeps_null_new_id_and_is_not_esol_payload():
    body = _xml("create_new_candidates", [{"model": "BMS-230DG", "sterbrust_product_id": None,
                                             "new_candidate_id": "NEWC-EXAMPLE",
                                             "section_id": 4074, "source_offers": []}],
                manifest_sha="a" * 64, registry_sha="b" * 64)
    root = ET.fromstring(body)
    assert root.attrib["esolPayload"] == "false"
    assert root.find("Product/sterbrust_product_id") is None
    assert root.findtext("Product/new_candidate_id") == "NEWC-EXAMPLE"
    assert root.findtext("Product/section_id") == "4074"


def test_official_reference_never_supplies_proposed_commercial_price():
    official = _row(source="beka_mak_tr", external_id="BMS-230DG", offer_id=3, price="1")
    result = build_proposal_tables([official], {"17128": {"name": "Sterbrust card"}})
    assert result["existing"] == []
    assert result["review"][0]["export_reason"] == "no_fresh_comparable_numeric_commercial_offer"


def test_two_executions_cannot_share_one_proposed_canonical_update():
    base = _row()
    wp2 = _row(source="intervesp", external_id="9028", offer_id=2,
               model="BMS-230DG WP2", model_key="bms230dgwp2")
    result = build_proposal_tables([base, wp2], {"17128": {"name": "Sterbrust card"}})
    assert result["existing"] == []
    assert all(row["export_status"] == "CONFLICT" for row in result["review"])


def test_absence_check_includes_inactive_canonical_cards():
    csv_text = "sterbrust_product_id,name,brand_raw,model_raw,active\n17,Beka-Mak BMS-230DG,Beka-Mak,BMS-230DG,N\n"
    with patch.object(Path, "open", return_value=StringIO(csv_text)):
        keys = _all_registry_model_keys(Path("registry.csv"))
    assert "bms230dg" in keys
