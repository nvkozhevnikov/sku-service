from __future__ import annotations

import sys
import xml.etree.ElementTree as ET
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(ROOT / "scripts"))

from test_stage5 import POLICY, row, snapshot
from test_stage6e import csrf, make_client
from universal_supplier.control_plane.store import ProductFilters, SnapshotControlPlaneStore
from universal_supplier.xml_export import (
    generate_canonical_xml, pp800f_in_artifact, supplier_xml_bytes,
)
from universal_supplier.yml_feed import FeedGenerationError, validate_yml_bytes


def _canonical(extra=()):
    return generate_canonical_xml(snapshot(row(), *extra))


def test_canonical_xml_well_formed():
    artifact = _canonical()
    root = ET.fromstring(artifact.data)
    assert root.tag == "yml_catalog"
    assert artifact.validation.valid


def test_canonical_xml_selected_offer_count():
    artifact = _canonical()
    assert artifact.manifest["exported_offer_count"] == 1
    assert len(ET.fromstring(artifact.data).findall(".//offer")) == 1


def test_short_xml_emits_explicit_sterbrust_bitrix_id_equal_to_offer_id():
    offer = ET.fromstring(_canonical().data).find(".//offer")
    assert offer.findtext("stb_1c_bitrix_id") == offer.get("id") == "88585"


def test_short_xml_excludes_bad_selected_rows_and_reports_reasons():
    good = row()
    no_price = row(
        "90001", catalog_product_id=101, selected_offer_id=201, offer_id=201,
        source_product_id=301, source_catalog_product_id=101,
        current_match_catalog_product_id=101, price=None,
    )
    no_url_or_category = row(
        "90002", catalog_product_id=102, selected_offer_id=202, offer_id=202,
        source_product_id=302, source_catalog_product_id=102,
        current_match_catalog_product_id=102, sterbrust_url=None,
        sterbrust_category_id=None, sterbrust_category_name=None,
    )
    artifact = generate_canonical_xml(snapshot(good, no_price, no_url_or_category))
    offers = ET.fromstring(artifact.data).findall(".//offer")
    counts = artifact.manifest["short_xml_exclusion_counts"]
    assert [offer.get("id") for offer in offers] == ["88585"]
    assert counts == {
        "excluded_total": 2, "without_price": 1, "without_url": 1,
        "without_category": 1, "blocked_by_identity": 0,
        "invalid_selected_data": 0,
        "backorder_unknown_quantity": 0,
        "unsupported_price_type": 0,
        "stale_confirmed_identity": 0,
    }


def test_short_xml_keeps_non_positive_price_fail_closed():
    bad = row(price=Decimal("0"))
    artifact = generate_canonical_xml(snapshot(bad))
    assert not ET.fromstring(artifact.data).findall(".//offer")
    assert artifact.manifest["short_xml_exclusion_counts"]["without_price"] == 1


@pytest.mark.parametrize(
    "status", ["CONFLICT", "REVIEW", "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY"]
)
def test_short_xml_identity_states_remain_fail_closed(status):
    artifact = generate_canonical_xml(snapshot(row(current_match_status=status)))
    assert not ET.fromstring(artifact.data).findall(".//offer")
    counts = artifact.manifest["short_xml_exclusion_counts"]
    assert counts["excluded_total"] == 1
    assert counts["blocked_by_identity"] == 1


def test_canonical_xml_duplicate_offer_zero():
    with pytest.raises(FeedGenerationError):
        generate_canonical_xml(snapshot(row(), row()))


def test_canonical_xml_category_refs_valid():
    artifact = _canonical()
    root = ET.fromstring(artifact.data)
    categories = {node.get("id") for node in root.findall(".//categories/category")}
    for offer in root.findall(".//offer"):
        assert offer.findtext("categoryId") in categories


def test_canonical_xml_currency_declared():
    artifact = _canonical()
    root = ET.fromstring(artifact.data)
    currencies = {node.get("id") for node in root.findall(".//currencies/currency")}
    for offer in root.findall(".//offer"):
        assert offer.findtext("currencyId") in currencies


def test_canonical_xml_known_false_match_zero():
    leak = row("61421", supplier_code="partner_st", supplier_external_id="1655", catalog_product_id=101,
               selected_offer_id=201, offer_id=201, source_product_id=301, source_catalog_product_id=101,
               current_match_catalog_product_id=101)
    with pytest.raises(FeedGenerationError, match="KNOWN_FALSE_MATCH"):
        generate_canonical_xml(snapshot(leak))


def test_canonical_xml_mrx3_zero():
    leak = row("999", supplier_external_id="305", catalog_product_id=102, selected_offer_id=202,
               offer_id=202, source_product_id=302, source_catalog_product_id=102,
               current_match_catalog_product_id=102)
    with pytest.raises(FeedGenerationError, match="MRX3"):
        generate_canonical_xml(snapshot(leak))


def test_canonical_xml_pp800f_pass():
    artifact = _canonical()
    assert pp800f_in_artifact(artifact)
    assert validate_yml_bytes(artifact.data, expected_rows=artifact.trace_rows and None, policy=POLICY).metrics["xml_well_formed"]


def test_supplier_xml_well_formed():
    xml = supplier_xml_bytes([{
        "supplier_code": "partner_st", "external_id": "1", "source_product_id": 1,
        "source_url": "https://partner-st.ru/a", "sku": "A", "article": "A", "brand_raw": "Partner",
        "model": "M", "name": "Item", "category": "Cat", "price": "10", "old_price": "",
        "currency": "RUB", "availability_normalized": "in_stock", "quantity": "1", "description_text": "d",
    }])
    root = ET.fromstring(xml)
    assert root.tag == "supplier_export" and root.find(".//product").get("external_id") == "1"


def test_supplier_xml_filter_parity():
    store = SnapshotControlPlaneStore(ROOT / "reports")
    filters = ProductFilters(supplier="optimum", brand="Optimum", page_size=100)
    xml = supplier_xml_bytes(store.export_rows(filters))
    assert len(ET.fromstring(xml).findall(".//product")) == store.products(filters)["total"]


def test_supplier_xml_special_chars_escaped():
    xml = supplier_xml_bytes([{
        "supplier_code": "optimum", "external_id": "x", "source_product_id": 9,
        "name": "A & B <C>", "description_text": 'quote "ok"',
    }])
    assert b"A &amp; B &lt;C&gt;" in xml
    ET.fromstring(xml)


def test_supplier_xml_missing_optional_fields_safe():
    xml = supplier_xml_bytes([{"supplier_code": "optimum", "external_id": "empty", "source_product_id": 2}])
    ET.fromstring(xml)
    assert b"<product " in xml


def test_xml_page_authenticated_and_anonymous():
    client, _ = make_client()
    assert client.get("/exports/xml").status_code == 200
    assert "XML-выгрузка" in client.get("/exports/xml").text
    assert "НЕ ПОДТВЕРЖДЁН" in client.get("/exports/xml").text
    bare = TestClient(__import__("universal_supplier.control_plane.web", fromlist=["create_app"]).create_app(
        store=SnapshotControlPlaneStore(ROOT / "reports"),
        session_secret="stage6e-test-secret-at-least-32-bytes", auth_required=True,
    ))
    assert bare.get("/exports/xml", follow_redirects=False).status_code in {303, 401}


def test_xml_preview_and_download_headers():
    client, _ = make_client()
    preview = client.get("/exports/xml?mode=canonical&action=preview")
    assert preview.status_code == 200 and "<pre" in preview.text
    download = client.get("/exports/xml/download?mode=canonical")
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("application/xml")
    assert "attachment" in download.headers.get("content-disposition", "")
    assert download.content.startswith(b"<?xml")


def test_viewer_can_download_xml_and_no_apply_button():
    client, _ = make_client(role="VIEWER")
    html = client.get("/exports/xml").text
    assert "Применить к Sterbrust" not in html and 'action="/esol' not in html
    assert "Скачать XML" in client.get("/exports/xml?mode=supplier&action=preview").text
    assert client.get("/exports/xml/download?mode=supplier").status_code == 200


def test_xml_generation_does_not_mutate_store():
    client, admin = make_client()
    before = len(admin.audit_events())
    client.get("/exports/xml?mode=supplier&action=preview")
    client.get("/exports/xml/download?mode=supplier")
    assert len(admin.audit_events()) == before


def test_xml_zero_sterbrust_write_routes():
    client, _ = make_client()
    routes = [getattr(route, "path", "") for route in client.app.routes]
    assert not any("sterbrust/write" in route or "esol" in route.lower() for route in routes)


def test_preview_script_refuses_working_volume():
    from preview_volume_guard import WORKING_VOLUME, assert_preview_volume_safe
    import pytest
    assert WORKING_VOLUME == "universal_supplier_pgdata"
    with pytest.raises(SystemExit):
        assert_preview_volume_safe("universal_supplier_pgdata")
    assert_preview_volume_safe("universal_supplier_admin_preview_pgdata")
