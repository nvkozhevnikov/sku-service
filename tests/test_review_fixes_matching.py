"""Regressions for the 2026-10-18 code-review fixes in product matching."""
import json
from pathlib import Path

import pytest

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    normalize_article, normalize_brand, normalize_model, normalize_model_strict,
)
from sterbrust_matching.product_identity import (
    canonical_properties, canonical_property_ambiguities, compare_category_identity,
    primary_millimeters,
)
from sterbrust_matching.property_normalization import normalize_property
from tests.test_matching_policy_v2 import row
from universal_supplier.kami_overlap import equivalence
from universal_supplier.matching_policy_v2 import NON_MACHINE, assess_pair, normalize_identity_model
from universal_supplier.matching_policy_v22 import assess_pair_v22
from universal_supplier.matching_service import RegistryContext, _candidates
from universal_supplier.offer_selection import OfferSelectionPolicy
from universal_supplier.postgres import operator_decision_guard

ROOT = Path(__file__).resolve().parents[1]


# 1. Operator decisions other than MANUAL_CONFIRMED.
def _accepted():
    return {"source_external_id": "S1", "status": "EXACT_MATCH", "match_method": "BRAND_MODEL",
            "sterbrust_product_id": "77", "auto_accepted": "TRUE", "confidence_score": 1.0}


def test_rejected_candidate_is_never_auto_linked():
    decision, evidence, warnings, conflicts, cls = operator_decision_guard(
        _accepted(), {"x": 1}, {}, {}, "", rejected=True, final_decision_class=None)
    assert decision["status"] == "REVIEW" and decision["auto_accepted"] is False
    assert decision["sterbrust_product_id"] == "77"
    assert evidence["proposed_input_evidence"] == {"x": 1}
    assert cls == "OPERATOR_DECISION_BLOCKED" and conflicts["operator_decision"] == "OPERATOR_REJECTED_CANDIDATE"


@pytest.mark.parametrize("final", ["MANUAL_CONFIRMED_NEW", "MANUAL_ACCESSORY"])
def test_final_new_or_accessory_blocks_auto_link(final):
    decision = operator_decision_guard(_accepted(), {}, {}, {}, "", rejected=False,
                                       final_decision_class=final)[0]
    assert decision["status"] == "REVIEW" and decision["match_method"] == "REVIEW_OPERATOR_FINAL_DECISION"


@pytest.mark.parametrize("final", [None, "MANUAL_CONFIRMED", "MARKED_REVIEWED"])
def test_other_final_decisions_do_not_block(final):
    assert operator_decision_guard(_accepted(), {}, {}, {}, "", rejected=False,
                                   final_decision_class=final) is None


def test_guard_ignores_non_automatic_decisions():
    assert operator_decision_guard({**_accepted(), "auto_accepted": False}, {}, {}, {}, "",
                                   rejected=True, final_decision_class="MANUAL_ACCESSORY") is None


def test_persist_match_queries_rejections_and_final_decisions():
    source = (ROOT / "universal_supplier/postgres.py").read_text(encoding="utf-8")
    body = source[source.index("def persist_match"):source.index("def load_sterbrust_snapshot")]
    assert "candidate_rejections" in body and "decision_class<>'POSTPONED'" in body
    assert "operator_decision_guard(" in body


# 2. Model and article keys.
def test_strict_model_keeps_decimal_and_plus_legacy_key_unchanged():
    assert normalize_model_strict("BF-1.5") != normalize_model_strict("BF 15")
    assert normalize_model_strict("BF-1.5") == normalize_model_strict("BF 1,5")
    assert normalize_model_strict("JWL-1015+") != normalize_model_strict("JWL-1015")
    assert normalize_model_strict("JWL-1015") == normalize_model_strict("JWL 1015")
    # The persisted/indexed legacy key does not change.
    assert normalize_model("BF-1.5") == "bf15" and normalize_model("JWL-1015+") == "jwl1015"


def test_article_key_keeps_digit_group_boundaries():
    assert normalize_article("10-100") != normalize_article("101-00")
    assert normalize_article("10-100") == normalize_article("10 100")
    assert normalize_article("JWL-1015") == normalize_article("JWL1015")
    assert normalize_article("3034252") == "3034252"


def test_v2_identity_model_keeps_decimal_and_plus():
    assert normalize_identity_model("BF-1.5") != normalize_identity_model("BF 15")
    assert normalize_identity_model("45/500S+") != normalize_identity_model("45/500S")
    assert normalize_identity_model("PowerBend 2100-40") == "powerbend210040"


def _product(key, model, name=None, **extra):
    return Product(key=key, name=name or f"Станок токарный JET {model}", brand="JET", model=model,
                   category="Токарные станки", **extra)


def test_decimal_model_is_not_auto_matched_to_integer_model():
    result = match_product(_product("S", "BF-1.5"), [_product("1", "BF 15")])
    assert not result.auto_accepted


def test_plus_model_is_not_auto_matched_to_base_model():
    result = match_product(_product("S", "JWL-1015+"), [_product("1", "JWL-1015")])
    assert not result.auto_accepted


def test_supplier_article_digit_groups_are_not_merged():
    source = _product("S", "BD-7", supplier_article="10-100", supplier_code="partner")
    other = _product("1", "BD-7", supplier_identifiers={"partner": ("101-00",)})
    result = match_product(source, [other])
    assert result.method != "SUPPLIER_ARTICLE_EXACT"
    same = _product("2", "BD-7", supplier_identifiers={"partner": ("10 100",)})
    assert match_product(source, [same]).method == "SUPPLIER_ARTICLE_EXACT"


def test_registry_identifier_lookup_uses_same_article_key():
    products = {"5": {"sterbrust_product_id": "5", "name": "x", "brand": "", "model": "", "article": "",
                      "category": "", "properties": {}, "supplier_identifiers": {"partner": ["10 100"]}}}
    registry = RegistryContext(products, {("partner", normalize_article("10 100")): ["5"]}, {}, {}, {})
    hit = Product(key="S", name="x", brand="", model="", supplier_article="10-100", supplier_code="partner")
    miss = Product(key="S", name="x", brand="", model="", supplier_article="101-00", supplier_code="partner")
    assert [r["sterbrust_product_id"] for r in _candidates(hit, registry)] == ["5"]
    assert _candidates(miss, registry) == []


# 3. Brand homoglyphs.
def test_brand_mixed_script_homoglyphs_fold():
    assert normalize_brand("JЕТ") == normalize_brand("JET") == "jet"
    assert normalize_brand("Сталекс") == "stalex"
    assert normalize_brand("Беркут") == "беркут"


# 4./5. Property normalization.
def test_identity_token_folds_cyrillic_lookalikes_in_codes():
    assert normalize_property("МТ3", "string").normalized_text == normalize_property("MT3", "string").normalized_text
    assert normalize_property("3 фазы", "string").normalized_text == "3фазы"


def test_dimension_unit_directly_after_digit():
    value = normalize_property("50x30cm", "dimension")
    assert value.normalized_text == "500x300" and value.canonical_unit == "mm"
    assert normalize_property("50 x 30 см", "dimension").normalized_text == "500x300"


# 6. Title millimetres.
def test_title_mm_parses_full_dimensions_and_skips_decimals():
    assert primary_millimeters("Диск 230х1,25 мм") != primary_millimeters("Диск 125х1,25 мм")
    assert primary_millimeters("Диск 1,25 мм") == ""
    assert primary_millimeters("Патрон 160mm") == "160"
    assert primary_millimeters("Планшайба 250 мм") == "250"


# 7. V2.2 negative guards on retained V2.1 acceptance.
@pytest.mark.parametrize("left,right,reason", [
    ("Вертикальный ленточнопильный станок TEST M200", "Горизонтальный ленточнопильный станок TEST M200",
     "V22_EXPLICIT_ORIENTATION_CONTRADICTION"),
    ("Двухколонный ленточнопильный станок TEST M200", "Маятниковый ленточнопильный станок TEST M200",
     "V22_EXPLICIT_FRAME_CONTRADICTION"),
])
def test_v21_retained_runs_structural_guards(left, right, reason):
    s, t = row("M200", left, "TEST"), row("M200", right, "TEST")
    assert assess_pair(s, t)["confirmed"]
    result = assess_pair_v22(s, t)
    assert not result["confirmed"] and reason in result["blocking_reasons"]


def test_v21_retained_unchanged_without_contradiction():
    s = row("M200", "Ленточнопильный станок TEST M200", "TEST")
    result = assess_pair_v22(s, dict(s))
    assert result["confirmed"] and result["rule_id"] == "V21_RETAINED"


# 8. Deterministic tie-break.
def test_equal_similarity_tie_breaks_on_smallest_id():
    source = Product(key="S", name="Тиски слесарные", brand="", model="")
    candidates = [Product(key=k, name="Тиски слесарные", brand="", model="") for k in ("30", "4", "120")]
    assert match_product(source, candidates).candidate_key == "4"
    assert match_product(source, candidates[::-1]).candidate_key == "4"


def test_registry_select_is_ordered():
    source = (ROOT / "universal_supplier/matching_service.py").read_text(encoding="utf-8")
    assert "ORDER BY sterbrust_product_id" in source


# 9. Canonical property ambiguity.
def test_contradicting_duplicate_property_is_not_first_wins():
    props = {"Мощность двигателя, кВт": "1.5", "Мощность электродвигателя главного привода, кВт": "3"}
    assert "motor_power" not in canonical_properties(props)
    assert "motor_power" in canonical_property_ambiguities(props)
    conflicts, _ = compare_category_identity("lathe", props, {"Мощность двигателя, кВт": "1.5"})
    assert conflicts["category_property:motor_power"]["result"] == "PROPERTY_AMBIGUOUS_DUPLICATE_VALUES"


def test_agreeing_duplicate_property_keeps_value():
    props = {"Мощность двигателя, кВт": "1.5", "Мощность электродвигателя, кВт": "1,5"}
    assert canonical_properties(props)["motor_power"] == "1.5 kW"
    assert canonical_property_ambiguities(props) == {}


# 10. Offer selection policy fields are validated.
@pytest.mark.parametrize("key,value", [
    ("ranking_order", ["supplier_priority", "availability"]),
    ("deterministic_tie_break", ["offer_id"]),
    ("unknown_price_behavior", "preferred"),
    ("zero_price_behavior", "valid"),
    ("unknown_availability_behavior", "excluded"),
])
def test_unimplemented_policy_values_are_rejected(key, value):
    raw = json.loads((ROOT / "config/offer_selection.json").read_text(encoding="utf-8"))
    OfferSelectionPolicy.from_dict(raw)
    with pytest.raises(ValueError, match=key):
        OfferSelectionPolicy.from_dict({**raw, key: value})


# Lower priority: kami overlap reuses NON_MACHINE.
def test_kami_overlap_blocks_every_non_machine_kind():
    assert {"collet", "faceplate", "steady_rest"} <= NON_MACHINE
    base = {"brand": "TEST", "model": "M200", "model_role": "OWN_MODEL", "evidence_ref": "x",
            "name": "Цанга TEST M200", "source_category": ""}
    assert "PRODUCT_KIND_NOT_EXACT" in equivalence(base, dict(base))["blocking_reasons"]
