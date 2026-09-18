from __future__ import annotations

import json
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from universal_supplier.offer_selection import (
    OfferCandidate,
    OfferSelectionPolicy,
    PostgresOfferSelectionStore,
    evaluate_offers,
)


ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "offer_selection.json"
POLICY = OfferSelectionPolicy.load(POLICY_PATH)
NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def candidate(offer_id=1, **overrides):
    value = OfferCandidate(
        catalog_product_id=100,
        offer_id=offer_id,
        source_product_id=offer_id,
        supplier_id=1,
        offer_supplier_id=1,
        supplier_code="partner_st",
        supplier_enabled=True,
        source_catalog_product_id=100,
        source_active=True,
        source_missed_crawls=0,
        source_last_success_at=NOW,
        offer_active=True,
        offer_kind="default",
        offer_missed_crawls=0,
        offer_last_success_at=NOW,
        latest_successful_full_crawl_at=NOW,
        price=Decimal("100000"),
        currency="RUB",
        availability="in_stock",
        availability_raw="В наличии",
        quantity=Decimal("2"),
    )
    return replace(value, **overrides)


def policy(**changes):
    raw = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    raw.update(changes)
    return OfferSelectionPolicy.from_dict(raw)


def test_policy_is_explicit_versioned_and_suppliers_are_equal_by_default():
    assert POLICY.version == "offer-selection-v1"
    assert POLICY.supplier_priority["partner_st"] == POLICY.supplier_priority["optimum"]


def test_single_current_offer_is_selected():
    result = evaluate_offers(100, [candidate()], POLICY)
    assert (result.selection_status, result.selected_offer_id) == ("selected", 1)
    assert result.evidence["selection_reason"] == "single_eligible_current_offer"


def test_single_null_price_is_still_selectable_as_price_on_request():
    result = evaluate_offers(100, [candidate(price=None)], POLICY)
    assert result.selection_status == "selected"
    assert result.evidence["candidate_offers"][0]["price"] is None


def test_zero_price_is_not_treated_as_cheapest():
    zero = candidate(1, price=Decimal("0"))
    valid = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                      price=Decimal("90000"))
    result = evaluate_offers(100, [zero, valid], POLICY)
    assert result.selected_offer_id == 2
    zero_evidence = next(row for row in result.evidence["candidate_offers"] if row["offer_id"] == 1)
    assert zero_evidence["price_valid"] is False


def test_lower_price_wins_at_equal_business_rank():
    expensive = candidate(1)
    cheap = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                      price=Decimal("90000"))
    assert evaluate_offers(100, [expensive, cheap], POLICY).selected_offer_id == 2


def test_availability_beats_lower_price():
    backorder = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                          availability="backorder", price=Decimal("80000"), quantity=None)
    assert evaluate_offers(100, [candidate(), backorder], POLICY).selected_offer_id == 1


def test_configured_supplier_priority_precedes_price():
    preferred = policy(supplier_priority={"partner_st": 10, "optimum": 0})
    cheap = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                      price=Decimal("90000"))
    assert evaluate_offers(100, [candidate(), cheap], preferred).selected_offer_id == 1


def test_stale_offer_is_excluded_and_current_supplier_is_fallback():
    stale = candidate(1, source_missed_crawls=1)
    current = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2)
    result = evaluate_offers(100, [stale, current], POLICY)
    assert result.selected_offer_id == 2
    stale_evidence = next(row for row in result.evidence["candidate_offers"] if row["offer_id"] == 1)
    assert "source_product_missed_crawls" in stale_evidence["exclusion_reasons"]


def test_offer_not_confirmed_by_latest_crawl_is_stale():
    old = datetime(2026, 9, 2, tzinfo=timezone.utc)
    result = evaluate_offers(100, [candidate(offer_last_success_at=old)], POLICY)
    assert result.selection_status == "no_eligible_offer"


@pytest.mark.parametrize("field,value", [
    ("offer_active", False), ("source_active", False), ("supplier_enabled", False),
])
def test_inactive_or_disabled_candidate_is_not_eligible(field, value):
    result = evaluate_offers(100, [candidate(**{field: value})], POLICY)
    assert result.selection_status == "no_eligible_offer"


def test_discontinued_is_not_eligible_by_default():
    result = evaluate_offers(100, [candidate(availability="discontinued")], POLICY)
    assert result.selection_status == "no_eligible_offer"


def test_out_of_stock_single_offer_remains_current_representation():
    result = evaluate_offers(100, [candidate(availability="out_of_stock", quantity=None)], POLICY)
    assert result.selection_status == "selected"


def test_unknown_availability_is_ranked_below_backorder():
    unknown = candidate(1, availability="unknown", quantity=None, price=Decimal("1"))
    backorder = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                          availability="backorder", quantity=None, price=Decimal("999999"))
    assert evaluate_offers(100, [unknown, backorder], POLICY).selected_offer_id == 2


def test_in_stock_quantity_zero_goes_to_review():
    result = evaluate_offers(100, [candidate(quantity=Decimal("0"))], POLICY)
    assert result.selection_status == "review"
    assert "quantity_zero_contradiction" in result.evidence["selection_reason"]


def test_different_top_currencies_go_to_review():
    eur = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                    price=Decimal("900"), currency="EUR")
    result = evaluate_offers(100, [candidate(), eur], POLICY)
    assert result.selection_status == "review"
    assert result.evidence["incomparable_currencies"] == ["EUR", "RUB"]


def test_stronger_availability_resolves_before_currency():
    eur = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                    price=Decimal("900"), currency="EUR", availability="backorder", quantity=None)
    result = evaluate_offers(100, [candidate(), eur], POLICY)
    assert result.selected_offer_id == 1


def test_equal_inputs_use_stable_supplier_then_offer_tie_break():
    optimum = candidate(8, supplier_code="optimum", supplier_id=2, offer_supplier_id=2)
    partner = candidate(2)
    first = evaluate_offers(100, [partner, optimum], POLICY)
    second = evaluate_offers(100, [optimum, partner], POLICY)
    assert first.selected_offer_id == second.selected_offer_id == 8


def test_multiple_top_null_prices_are_review_not_hidden_by_tie_break():
    a = candidate(1, price=None)
    b = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2, price=None)
    assert evaluate_offers(100, [a, b], POLICY).selection_status == "review"


def test_wrong_catalog_product_relationship_is_integrity_failure():
    with pytest.raises(ValueError, match="catalog_product_mismatch"):
        evaluate_offers(100, [candidate(source_catalog_product_id=999)], POLICY)


def test_offer_supplier_mismatch_is_integrity_failure():
    with pytest.raises(ValueError, match="offer_supplier_mismatch"):
        evaluate_offers(100, [candidate(offer_supplier_id=999)], POLICY)


def test_duplicate_default_offers_for_one_source_go_to_review():
    duplicate = candidate(2, source_product_id=1)
    result = evaluate_offers(100, [candidate(1), duplicate], POLICY)
    assert result.selection_status == "review"


def test_same_supplier_multiple_source_products_require_identity_review():
    result = evaluate_offers(100, [candidate(1), candidate(2)], POLICY)
    assert result.selection_status == "review"
    assert result.selected_offer_id is None
    assert result.evidence["selection_reason"] == "same_supplier_multiple_source_products_identity_ambiguity"


def test_different_suppliers_remain_normal_alternatives():
    optimum = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                        price=Decimal("90000"))
    result = evaluate_offers(100, [candidate(1), optimum], POLICY)
    assert result.selection_status == "selected"
    assert result.selected_offer_id == 2


def test_variant_offer_is_excluded_by_default_policy():
    result = evaluate_offers(100, [candidate(offer_kind="variant")], POLICY)
    assert result.selection_status == "no_eligible_offer"


def test_same_raw_sku_never_enters_ranking_or_identity_logic():
    source = (ROOT / "universal_supplier" / "offer_selection.py").read_text(encoding="utf-8")
    assert ".sku" not in source
    assert "fuzzy" not in source.lower()


def test_policy_version_change_is_reflected_without_code_change():
    changed = policy(version="offer-selection-v2", supplier_priority={"partner_st": 5, "optimum": 0})
    cheap = candidate(2, supplier_code="optimum", supplier_id=2, offer_supplier_id=2,
                      price=Decimal("90000"))
    v1 = evaluate_offers(100, [candidate(), cheap], POLICY)
    v2 = evaluate_offers(100, [candidate(), cheap], changed)
    assert (v1.selected_offer_id, v2.selected_offer_id) == (2, 1)
    assert (v1.rule_version, v2.rule_version) == ("offer-selection-v1", "offer-selection-v2")


def test_evidence_contains_required_candidate_and_rule_inputs():
    evidence = evaluate_offers(100, [candidate()], POLICY).evidence
    for key in ("policy_version", "catalog_product_id", "evaluated_candidate_count",
                "eligible_candidate_count", "selected_offer_id", "selected_supplier_code",
                "selection_reason", "availability_rank", "supplier_priority", "price_rule",
                "freshness_rule", "candidate_offers"):
        assert key in evidence
    assert evidence["candidate_offers"][0]["availability_raw"] == "В наличии"


def test_apply_sql_preserves_evaluated_at_when_semantics_are_unchanged():
    source = (ROOT / "universal_supplier" / "offer_selection.py").read_text(encoding="utf-8")
    assert "evidence IS DISTINCT FROM EXCLUDED.evidence" in source
    assert "evaluated_at=now()" in source


def test_cli_has_dry_run_and_explicit_apply_confirmation():
    source = (ROOT / "scripts" / "run_offer_selection.py").read_text(encoding="utf-8")
    assert 'mode.add_argument("--dry-run"' in source
    assert 'mode.add_argument("--apply"' in source
    assert 'os.environ.get("STAGE4_DB_CONFIRM") != "YES"' in source


def test_migrations_001_011_remain_at_baseline():
    import hashlib
    baseline = json.loads((ROOT / "stage3d_migration_baseline_sha256.json").read_text(encoding="utf-8"))
    paths = [path for path in sorted((ROOT / "migrations").glob("[0-9][0-9][0-9]_*.sql")) if int(path.name[:3]) <= 11]
    actual = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in paths}
    assert actual == baseline["files"]
    package_source = (ROOT / "scripts" / "package_stage4.py").read_text(encoding="utf-8")
    assert 'ARCHIVE_ROOT = "UNIVERSAL_SUPPLIER_STAGE4_OFFER_SELECTION_ENGINE_CORRECTED2"' in package_source
    assert 'EXCLUDED_NAMES = {".env"}' in package_source
