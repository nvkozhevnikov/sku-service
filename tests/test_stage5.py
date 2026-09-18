from __future__ import annotations

import hashlib
import json
import xml.etree.ElementTree as ET
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from universal_supplier.yml_feed import (
    FeedGenerationError,
    FeedPolicy,
    FeedRow,
    FeedSnapshot,
    atomic_replace_validated,
    build_yml,
    validate_yml_bytes,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY = FeedPolicy.load(ROOT / "config" / "yml_feed.json")
SNAPSHOT_TIME = datetime(2026, 9, 8, 9, 23, 32, tzinfo=timezone.utc)


def row(feed_id: str = "88585", **changes) -> FeedRow:
    base = FeedRow(
        catalog_product_id=100,
        selection_status="selected",
        selected_offer_id=200,
        selection_rule_version="offer-selection-v1",
        selection_reason="single_eligible_current_offer",
        selection_evaluated_at=SNAPSHOT_TIME,
        offer_id=200,
        offer_supplier_id=1,
        source_product_id=300,
        source_supplier_id=1,
        source_catalog_product_id=100,
        supplier_code="partner_st",
        supplier_enabled=True,
        supplier_external_id="297",
        supplier_sku="508002",
        price=Decimal("339940.0000"),
        old_price=None,
        currency="RUB",
        availability_normalized="in_stock",
        availability_raw="https://schema.org/InStock",
        quantity=Decimal("3.0000"),
        offer_active=True,
        source_active=True,
        sterbrust_product_id=feed_id,
        sterbrust_name="Универсальный станок Partner PP-800F",
        sterbrust_url=f"https://sterbrust.com/catalog/{feed_id}/",
        sterbrust_category_id="3901",
        sterbrust_category_name="Заточные станки",
        sterbrust_brand="Partner",
        sterbrust_active=True,
        current_match_status="EXACT_MATCH",
        current_match_catalog_product_id=100,
    )
    return replace(base, **changes)


def snapshot(*rows: FeedRow, not_managed=()) -> FeedSnapshot:
    return FeedSnapshot(
        selected_rows=tuple(rows),
        not_managed_rows=tuple(not_managed),
        selection_counts={"selected": len(rows), "no_eligible_offer": len(not_managed)},
        snapshot_timestamp=SNAPSHOT_TIME,
    )


def parsed(artifact):
    return ET.fromstring(artifact.data)


def only_offer(artifact):
    return parsed(artifact).find("./shop/offers/offer")


def test_policy_is_versioned_and_shop_metadata_is_configured_without_legal_suffix():
    assert POLICY.version == "yml-feed-v1"
    assert POLICY.shop_name == "Стербруст"
    assert POLICY.shop_company == "Стербруст"
    assert "ООО" not in POLICY.shop_company and "ИП" not in POLICY.shop_company


def test_policy_allows_rub_at_rate_one_only():
    assert POLICY.currencies == {"RUB": "1"}


def test_policy_rejects_missing_availability_mapping():
    raw = json.loads((ROOT / "config" / "yml_feed.json").read_text(encoding="utf-8"))
    del raw["availability_mapping"]["unknown"]
    with pytest.raises(ValueError, match="Missing availability mappings"):
        FeedPolicy.from_dict(raw)


def test_selected_only_export_excludes_no_eligible_rows():
    artifact = build_yml(snapshot(row(), not_managed=[{"catalog_product_id": 999}]), POLICY)
    assert artifact.manifest["exported_offer_count"] == 1
    assert len(parsed(artifact).findall("./shop/offers/offer")) == 1


@pytest.mark.parametrize("status", ["review", "no_eligible_offer", "not_evaluated"])
def test_non_selected_row_fails_closed_if_passed_to_export(status):
    item = row(selection_status=status)
    bad = FeedSnapshot((item,), (), {"selected": 1}, SNAPSHOT_TIME)
    with pytest.raises(FeedGenerationError, match="NOT_SELECTED"):
        build_yml(bad, POLICY)


def test_unlinked_source_fails_closed():
    with pytest.raises(FeedGenerationError, match="SOURCE_CATALOG_PRODUCT_MISMATCH"):
        build_yml(snapshot(row(source_catalog_product_id=None)), POLICY)


def test_nonaccepted_current_match_fails_closed():
    with pytest.raises(FeedGenerationError, match="NO_CURRENT_ACCEPTED_MATCH"):
        build_yml(snapshot(row(current_match_status="CONFLICT")), POLICY)


@pytest.mark.parametrize("availability,expected", [
    ("in_stock", "true"),
    ("preorder", "true"),
    ("backorder", "true"),
    ("out_of_stock", "false"),
    ("incoming", "false"),
    ("unknown", "false"),
])
def test_explicit_availability_mapping(availability, expected):
    artifact = build_yml(snapshot(row(availability_normalized=availability)), POLICY)
    assert only_offer(artifact).get("available") == expected


def test_discontinued_selected_offer_fails_closed():
    with pytest.raises(FeedGenerationError, match="DISCONTINUED_SELECTED_OFFER"):
        build_yml(snapshot(row(availability_normalized="discontinued")), POLICY)


@pytest.mark.parametrize("price,marker", [
    (None, "NULL_SELECTED_PRICE"),
    (Decimal("0"), "NON_POSITIVE_SELECTED_PRICE"),
    (Decimal("-1"), "NON_POSITIVE_SELECTED_PRICE"),
])
def test_invalid_selected_price_fails_closed(price, marker):
    with pytest.raises(FeedGenerationError, match=marker):
        build_yml(snapshot(row(price=price)), POLICY)


def test_price_has_decimal_point_not_comma_or_currency_symbol():
    artifact = build_yml(snapshot(row(price=Decimal("1234.50"))), POLICY)
    assert only_offer(artifact).findtext("price") == "1234.5"


def test_unsupported_currency_fails_closed():
    with pytest.raises(FeedGenerationError, match="UNSUPPORTED_CURRENCY"):
        build_yml(snapshot(row(currency="EUR")), POLICY)


def test_sterbrust_identity_is_used_for_offer_name_url_category_and_id():
    artifact = build_yml(snapshot(row()), POLICY)
    offer = only_offer(artifact)
    assert offer.get("id") == "88585"
    assert offer.findtext("name") == "Универсальный станок Partner PP-800F"
    assert offer.findtext("url") == "https://sterbrust.com/catalog/88585/"
    assert offer.findtext("categoryId") == "3901"


def test_canonical_vendor_is_exported_and_supplier_code_is_not():
    artifact = build_yml(snapshot(row(sterbrust_brand="Partner", supplier_code="supplier_internal")), POLICY)
    offer = only_offer(artifact)
    assert offer.findtext("vendor") == "Partner"
    assert "supplier_internal" not in artifact.data.decode("utf-8")


def test_vendor_code_uses_selected_sku_not_external_id():
    artifact = build_yml(snapshot(row(supplier_external_id="373", supplier_sku="578415")), POLICY)
    offer = only_offer(artifact)
    assert offer.get("id") == "88585"
    assert offer.findtext("vendorCode") == "578415"
    assert offer.findtext("vendorCode") != "373"
    assert "Артикул поставщика" not in artifact.data.decode("utf-8")


def test_vendor_code_omitted_when_sku_missing_or_equals_external_id():
    missing = only_offer(build_yml(snapshot(row(supplier_sku=None, supplier_external_id="373")), POLICY))
    same = only_offer(build_yml(snapshot(row(supplier_sku="373", supplier_external_id="373")), POLICY))
    assert missing.find("vendorCode") is None
    assert same.find("vendorCode") is None


def test_sterbrust_own_article_param_is_separate_from_vendor_code():
    artifact = build_yml(snapshot(row(supplier_sku="578415", sterbrust_article="ST-OWN")), POLICY)
    offer = only_offer(artifact)
    assert offer.findtext("vendorCode") == "578415"
    params = {node.get("name"): node.text for node in offer.findall("param")}
    assert params.get("Артикул Sterbrust") == "ST-OWN"
    assert params.get("Артикул поставщика") is None


def test_sterbrust_article_omitted_when_absent_or_same_as_supplier_sku():
    absent = only_offer(build_yml(snapshot(row(supplier_sku="578415", sterbrust_article=None)), POLICY))
    same = only_offer(build_yml(snapshot(row(supplier_sku="578415", sterbrust_article="578415")), POLICY))
    assert all(node.get("name") != "Артикул Sterbrust" for node in absent.findall("param"))
    assert all(node.get("name") != "Артикул Sterbrust" for node in same.findall("param"))


def test_missing_vendor_is_omitted():
    artifact = build_yml(snapshot(row(sterbrust_brand=None)), POLICY)
    assert only_offer(artifact).find("vendor") is None


@pytest.mark.parametrize("old_price,exported,warning_count", [
    (Decimal("400000"), "400000", 0),
    (Decimal("339940"), None, 1),
    (Decimal("300000"), None, 1),
    (None, None, 0),
])
def test_oldprice_rules(old_price, exported, warning_count):
    artifact = build_yml(snapshot(row(old_price=old_price)), POLICY)
    assert only_offer(artifact).findtext("oldprice") == exported
    assert len(artifact.warnings) == warning_count


def test_known_quantity_is_preserved_as_documented_param():
    artifact = build_yml(snapshot(row(quantity=Decimal("13.0000"))), POLICY)
    param = only_offer(artifact).find("param")
    assert param is not None and param.get("name") == "quantity" and param.text == "13"


def test_null_quantity_is_not_converted_to_zero():
    artifact = build_yml(snapshot(row(quantity=None, availability_normalized="preorder")), POLICY)
    assert only_offer(artifact).find("param") is None
    assert ">0<" not in artifact.data.decode("utf-8")


def test_categories_and_offers_are_sorted_deterministically_by_sterbrust_id():
    high = row("100", catalog_product_id=2, selected_offer_id=2, offer_id=2,
               source_product_id=2, source_catalog_product_id=2,
               current_match_catalog_product_id=2, sterbrust_category_id="20",
               sterbrust_category_name="Twenty")
    low = row("9", catalog_product_id=1, selected_offer_id=1, offer_id=1,
              source_product_id=1, source_catalog_product_id=1,
              current_match_catalog_product_id=1, sterbrust_category_id="3",
              sterbrust_category_name="Three")
    artifact = build_yml(snapshot(high, low), POLICY)
    root = parsed(artifact)
    assert [node.get("id") for node in root.findall("./shop/categories/category")] == ["3", "20"]
    assert [node.get("id") for node in root.findall("./shop/offers/offer")] == ["9", "100"]


def test_same_state_produces_identical_bytes_and_hash():
    first = build_yml(snapshot(row()), POLICY)
    second = build_yml(snapshot(row()), POLICY)
    assert first.data == second.data
    assert first.sha256 == second.sha256 == hashlib.sha256(first.data).hexdigest()


def test_snapshot_date_is_database_derived_and_stable():
    artifact = build_yml(snapshot(row()), POLICY)
    assert parsed(artifact).get("date") == "2026-09-08 09:23"


def test_missing_snapshot_timestamp_fails_closed():
    with pytest.raises(FeedGenerationError, match="MISSING_DETERMINISTIC"):
        build_yml(FeedSnapshot((row(),), (), {"selected": 1}, None), POLICY)


def test_xml_escaping_and_unicode_round_trip():
    name = 'Фреза & резец <точный> "Юникод" Ω'
    artifact = build_yml(snapshot(row(sterbrust_name=name)), POLICY)
    assert only_offer(artifact).findtext("name") == name
    assert b"&amp;" in artifact.data and b"&lt;" in artifact.data


def test_document_order_is_metadata_currencies_categories_offers():
    tags = [node.tag for node in list(parsed(build_yml(snapshot(row()), POLICY)).find("shop"))]
    assert tags == ["name", "company", "url", "currencies", "categories", "offers"]


def test_category_name_conflict_fails_closed():
    a = row("1")
    b = row("2", catalog_product_id=2, selected_offer_id=2, offer_id=2,
            source_product_id=2, source_catalog_product_id=2,
            current_match_catalog_product_id=2, sterbrust_category_name="Other")
    with pytest.raises(FeedGenerationError, match="CATEGORY_NAME_CONFLICT"):
        build_yml(snapshot(a, b), POLICY)


def test_duplicate_feed_offer_id_fails_closed():
    a = row("88585")
    b = row("88585", catalog_product_id=2, selected_offer_id=2, offer_id=2,
            source_product_id=2, source_catalog_product_id=2,
            current_match_catalog_product_id=2)
    with pytest.raises(FeedGenerationError, match="DUPLICATE_FEED_OFFER_ID"):
        build_yml(snapshot(a, b), POLICY)


def test_join_cardinality_difference_fails_closed():
    bad = FeedSnapshot((row(),), (), {"selected": 2}, SNAPSHOT_TIME)
    with pytest.raises(FeedGenerationError, match="SELECTED_JOIN_CARDINALITY"):
        build_yml(bad, POLICY)


def test_validator_accepts_generated_document_and_current_expected_row():
    artifact = build_yml(snapshot(row()), POLICY)
    result = validate_yml_bytes(artifact.data, expected_rows=[row()], policy=POLICY)
    assert result.valid and result.metrics["offer_count"] == 1


def test_validator_rejects_malformed_xml():
    result = validate_yml_bytes(b"<?xml version='1.0' encoding='utf-8'?><broken>")
    assert not result.valid and any("XML_PARSE_ERROR" in item for item in result.violations)


def test_validator_rejects_undeclared_currency():
    artifact = build_yml(snapshot(row()), POLICY)
    tampered = artifact.data.replace(b"<currencyId>RUB</currencyId>", b"<currencyId>USD</currencyId>")
    result = validate_yml_bytes(tampered, policy=POLICY)
    assert not result.valid and result.metrics["undeclared_currencies"] == 1


def test_validator_rejects_dangling_category_reference():
    artifact = build_yml(snapshot(row()), POLICY)
    tampered = artifact.data.replace(b"<categoryId>3901</categoryId>", b"<categoryId>999</categoryId>")
    result = validate_yml_bytes(tampered, policy=POLICY)
    assert not result.valid and result.metrics["dangling_category_references"] == 1


def test_validator_rejects_nonpositive_price():
    artifact = build_yml(snapshot(row()), POLICY)
    tampered = artifact.data.replace(b"<price>339940</price>", b"<price>0</price>")
    result = validate_yml_bytes(tampered, policy=POLICY)
    assert not result.valid and result.metrics["invalid_prices"] == 1


def test_validator_rejects_invalid_available_value():
    artifact = build_yml(snapshot(row()), POLICY)
    tampered = artifact.data.replace(b'available="true"', b'available="yes"')
    result = validate_yml_bytes(tampered, policy=POLICY)
    assert not result.valid and any("INVALID_AVAILABLE" in item for item in result.violations)


def test_validator_detects_db_commercial_field_mismatch():
    artifact = build_yml(snapshot(row()), POLICY)
    tampered = artifact.data.replace(b"<price>339940</price>", b"<price>339941</price>")
    result = validate_yml_bytes(tampered, expected_rows=[row()], policy=POLICY)
    assert not result.valid and any("DB_FIELD_MISMATCH:88585:price" in item for item in result.violations)


def test_atomic_replace_writes_valid_feed(tmp_path):
    artifact = build_yml(snapshot(row()), POLICY)
    target = tmp_path / "feed.xml"
    atomic_replace_validated(target, artifact.data, lambda data: validate_yml_bytes(data, policy=POLICY))
    assert target.read_bytes() == artifact.data


def test_invalid_generation_does_not_replace_previous_valid_feed(tmp_path):
    artifact = build_yml(snapshot(row()), POLICY)
    target = tmp_path / "feed.xml"
    target.write_bytes(artifact.data)
    before = target.read_bytes()
    with pytest.raises(FeedGenerationError):
        atomic_replace_validated(
            target, b"<not-xml", lambda data: validate_yml_bytes(data, policy=POLICY)
        )
    assert target.read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))


def test_trace_contains_internal_ids_but_production_xml_does_not():
    artifact = build_yml(snapshot(row()), POLICY)
    assert artifact.trace_rows[0]["selected_offer_id"] == 200
    text = artifact.data.decode("utf-8")
    assert "selected_offer_id" not in text and "source_product_id" not in text


def test_generator_source_never_invokes_selector_or_sterbrust_api():
    source = (ROOT / "universal_supplier" / "yml_feed.py").read_text(encoding="utf-8")
    assert "evaluate_offers(" not in source
    assert "run_offer_selection" not in source
    assert "sterbrust api" not in source.lower()


def test_cli_supports_dry_run_and_separate_db_backed_validator():
    generator = (ROOT / "scripts" / "generate_yml_feed.py").read_text(encoding="utf-8")
    validator = (ROOT / "scripts" / "validate_yml_feed.py").read_text(encoding="utf-8")
    assert '"--dry-run"' in generator
    assert "STAGE5_DB_CONFIRM" in generator
    assert '"--structural-only"' in validator
    assert "PostgresFeedStore" in validator


def test_feed_runs_are_combined_and_never_supplier_scoped():
    source = (ROOT / "universal_supplier" / "yml_feed.py").read_text(encoding="utf-8")
    assert "VALUES ('combined',NULL" in source
    assert '"sterbrust_write_methods_used": 0' in source


def test_migrations_001_011_remain_immutable():
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    paths = [path for path in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")) if int(path.name[:3]) <= 11]
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert actual == baseline["files"]


def test_stage5_packager_has_sanitized_root_and_required_artifacts():
    source = (ROOT / "scripts" / "package_stage5.py").read_text(encoding="utf-8")
    assert 'ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE5_YML_FEED_GENERATOR"' in source
    assert 'EXCLUDED_NAMES = {".env"' in source
    assert '"exports/sterbrust_selected_offers.xml"' in source
    assert '"reports/STAGE5_FEED_MANIFEST.json"' in source
