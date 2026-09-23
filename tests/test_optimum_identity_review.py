"""Optimum identity regressions: kind, voltage, execution, review sync, short XML."""
from __future__ import annotations

import xml.etree.ElementTree as ET
import json
from collections import defaultdict
from decimal import Decimal

import pytest

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.decision_history import decision_fingerprint, warning_fingerprint
from sterbrust_matching.normalization import brand_evidenced_by_name
from sterbrust_matching.product_identity import (
    REFERENCE_MODEL,
    classify_model_role,
    classify_product_kind,
)
from universal_supplier.adapters.optimum import _brand_from_source
from universal_supplier.control_plane.admin_store import plan_review_sync, review_proposal_id
from universal_supplier.matching_service import RegistryContext, match_cards
from universal_supplier.models import ProductCard
from universal_supplier.xml_export import STALE_SELECTION_RU, generate_canonical_xml, iter_full_xml
from universal_supplier.yml_feed import FeedGenerationError, build_yml

from test_stage5 import POLICY, row, snapshot


def product(key, name, model, **kwargs):
    return Product(key=key, name=name, model=model, brand=kwargs.pop("brand", "Optimum"), **kwargs)


def assert_not_auto(source, candidate):
    result = match_product(source, [candidate])
    assert result.auto_accepted is False
    return result


def test_accessory_heads_are_not_the_parent_machine():
    cases = {
        "Неподвижный люнет Макс. 50 мм для токарных станков TU 2004V": "steady_rest",
        "Педаль ножного управления для сверлильных станков OPTidrill DH 32GS и DH 32GSV": "foot_switch",
        "Автоматический ловитель готовых деталей для токарного станка с ЧПУ OPTiturn S 600": "parts_catcher",
        "Планшайба 240 мм для токарных станков TU 2406": "faceplate",
        "Четырехкулачковый литой токарный патрон CI4P PREMIUM 125 мм": "chuck",
        "Кулачки для токарного патрона CI4P 3442120": "chuck_jaw",
        "Алмазный круг для заточного станка": "disk",
        "Оснастка для токарного станка TU 2406": "fixture",
    }
    for name, kind in cases.items():
        evidence = classify_product_kind(name)
        assert evidence.product_kind == kind, name
        if " для " in name.lower() or " для " in name:
            role = classify_model_role(name)
            assert role.role == REFERENCE_MODEL, name
            assert role.own_model == "", name


def test_steady_rest_is_not_the_lathe():
    result = assert_not_auto(
        product("1250", "Неподвижный люнет Макс. 50 мм для токарных станков TU 2004V", "TU2004V"),
        product("24286", "Токарный станок OPTIturn TU 2004V", "TU2004V"),
    )
    assert "product_kind" in result.conflicts or "model_role" in result.conflicts


def test_parts_catcher_pedal_and_chuck_are_not_the_parent():
    assert_not_auto(
        product("1679", "Автоматический ловитель готовых деталей для токарного станка с ЧПУ OPTiturn S 600", "S600"),
        product("147566", "Токарный станок с ЧПУ OPTiturn S 600", "S600"),
    )
    assert_not_auto(
        product("1766", "Педаль ножного управления для сверлильных станков OPTidrill DH 32GS и DH 32GSV", "DH32GS"),
        product("17309", "Сверлильный станок OPTidrill DH 32GS", "DH32GS"),
    )
    assert_not_auto(
        product("1088", "Четырехкулачковый литой токарный патрон CI4P PREMIUM 125 мм", "CI4P"),
        product("23918", "Кулачки для токарного патрона CI4P 3442120", "CI4P"),
    )


def test_s300dg_vario_is_not_s300dg():
    plain = product("842", "Ленточнопильный станок OPTIsaw S 300 DG", "S300DG")
    vario = product("862", "Ленточнопильный станок OPTIsaw S 300 DG Vario", "S300DG")
    assert_not_auto(vario, plain)
    linked = match_product(vario, [], existing_link=plain, existing_link_origin="BRAND_MODEL")
    assert linked.auto_accepted is False
    assert linked.status == "CONFLICT"


def test_s300dg_without_vario_can_match_same_execution():
    result = match_product(
        product("842", "Ленточнопильный станок OPTIsaw S 300 DG", "S300DG"),
        [product("82431", "Ленточнопильный станок OPTIsaw S 300 DG Optimum 3290290", "S300DG")],
    )
    assert result.auto_accepted is True
    assert result.candidate_key == "82431"


def test_voltage_band_blocks_220_versus_380_and_keeps_220_near_230():
    low = product("5447", "Сверлильный станок OPTIdrill B 16H (220 В)", "B16H")
    high = product("80596", "Сверлильный станок Optimum 3020217 OPTIdrill B16H (380 В)", "B16H")
    assert_not_auto(low, high)
    linked = match_product(low, [], existing_link=high, existing_link_origin="SUPPLIER_ARTICLE")
    assert linked.status == "CONFLICT" and linked.auto_accepted is False
    same_band = match_product(
        product("s", "Сверлильный станок OPTIdrill DP 26-T (230 V)", "DP26T"),
        [product("c", "Сверлильный станок OPTIdrill DP 26-T (220 В)", "DP26T")],
    )
    assert same_band.auto_accepted is True


def test_400v_is_not_recommended_as_230v_even_with_close_model():
    assert_not_auto(
        product("459", "Вертикально-сверлильный станок OPTIdrill DP 26-T (400 V)", "DP26T"),
        product("80738", "Вертикально-сверлильный станок OPTIdrill DP 26-T (230 V)", "DP26T"),
    )
    assert_not_auto(
        product("461", "Вертикально-сверлильный станок OPTIdrill DP 26-F (400 V)", "DP26F"),
        product("80735", "Вертикально-сверлильный станок OPTIdrill DP 26-F (230 V)", "DP26F"),
    )


def test_same_article_does_not_auto_accept_faceplate_size_conflict():
    source = Product(
        key="1505", name="Планшайба 240 мм для токарных станков TU 2406", model="TU2406",
        brand="Optimum", supplier_article="3440552", supplier_code="optimum",
    )
    candidate = Product(
        key="23928", name="Планшайба 250 мм Optimum 3440552", model="TU2406", brand="Optimum",
        supplier_identifiers={"optimum": ("3440552",)},
    )
    result = match_product(source, [candidate])
    assert result.auto_accepted is False
    assert result.status == "CONFLICT"
    assert "physical_size" in result.conflicts


def test_brand_is_not_copied_from_supplier_code():
    assert _brand_from_source("Токарный станок OPTIturn TU 2406 (230 В)", "") == "Optimum"
    assert _brand_from_source("Кулачки для патрона CI4P", None) == ""
    assert _brand_from_source("Кулачки для патрона CI4P", "Bison") == "Bison"
    assert brand_evidenced_by_name("Педаль для OPTidrill DH 32GS") == "Optimum"
    assert brand_evidenced_by_name("Оснастка 125 мм") == ""


def test_review_proposal_is_only_a_real_review_id():
    assert review_proposal_id("REVIEW_EXISTING", "80741") == "80741"
    assert review_proposal_id("CONFLICT", "18093") is None
    assert review_proposal_id("ACCESSORY_OR_COMPATIBILITY_ITEM", "23938") is None
    assert review_proposal_id("HIGH_CONFIDENCE_EXISTING", "82431") is None


def _linked(source, candidate):
    return match_product(source, [], existing_link=candidate, existing_link_origin="EXISTING_LINK")


def test_four_collision_groups_keep_only_the_card_that_matches_execution():
    card_80596 = product("80596", "Сверлильный станок Optimum 3020217 OPTIdrill B16H  (380 В)", "B16H")
    keep_400 = product("432", "Сверлильный станок OPTIdrill B 16H (400 В)", "B16H",
                       supplier_article="3020217", supplier_code="optimum")
    drop_220 = product("5447", "Сверлильный станок OPTIdrill B 16H (220 В)", "B16H",
                       supplier_article="3020217-1", supplier_code="optimum")
    assert _linked(keep_400, card_80596).auto_accepted is True
    assert _linked(drop_220, card_80596).status == "CONFLICT"

    card_80623 = product("80623", "Сверлильный станок Optimum 3034252 OPTIdrill DH35V (380 В)", "DH35V")
    keep_252 = product("406", "Вертикально-сверлильный станок DH 35V", "DH35V",
                       supplier_article="3034252", supplier_code="optimum")
    drop_253 = product("5481", "Вертикально-сверлильный станок OPTIdrill DH35V", "DH35V",
                       supplier_article="3034253", supplier_code="optimum")
    assert _linked(keep_252, card_80623).auto_accepted is True
    assert _linked(drop_253, card_80623).status == "CONFLICT"

    card_80638 = product("80638", "Сверлильный станок Optimum 3034355 OPTIdrill DH40G (380 В)", "DH40G")
    keep_355 = product("368", "Универсальный колонный сверлильный станок OPTIdrill DH 40G", "DH40G",
                       supplier_article="3034355", supplier_code="optimum")
    drop_356 = product("5482", "Вертикально-сверлильный станок OPTIdrill DH40G", "DH40G",
                       supplier_article="3034356", supplier_code="optimum")
    assert _linked(keep_355, card_80638).auto_accepted is True
    dropped = _linked(drop_356, card_80638)
    assert dropped.auto_accepted is False and dropped.status == "CONFLICT"

    card_82429 = product("82429", "Универсальные сверлильно-фрезерный станок OPTImill MH 50V", "MH50V")
    keep_universal = product("5479", "Универсальные сверлильно-фрезерный станок OPTImill MH 50V", "MH50V",
                             supplier_article="3338186", supplier_code="optimum")
    drop_precision = product("868", "Прецизионный сверлильно-фрезерный станок OPTImill MH 50V", "MH50V",
                             supplier_article="3338185", supplier_code="optimum")
    assert _linked(keep_universal, card_82429).auto_accepted is True
    assert _linked(drop_precision, card_82429).status == "CONFLICT"


def _runtime_card(external_id, sku, name):
    return ProductCard(
        supplier_code="optimum", external_id=external_id, sku=sku, name=name,
        requested_url="https://optimum.example/item", final_url="https://optimum.example/item",
        canonical_url="https://optimum.example/item", http_status=200, redirect_urls=(),
        price=None, old_price=None, currency="RUB", availability_raw="",
        availability_normalized="unknown", quantity=None, description_text="",
        description_html="", brand="Optimum",
    )


def _runtime_registry(key, name, model):
    row = {
        "sterbrust_product_id": key, "name": name, "brand": "Optimum",
        "model": model, "article": "", "category": "Станки",
        "properties": {}, "supplier_identifiers": defaultdict(list),
    }
    return RegistryContext(
        products={key: row}, identifier_index={}, by_model={model.lower(): [key]},
        by_brand={"optimum": [key]}, title_identifier_index={},
    )


@pytest.mark.parametrize(("card", "registry", "target"), [
    (
        _runtime_card("5447", "3020217-1", "Сверлильный станок OPTIdrill B 16H (220 В)"),
        _runtime_registry("80596", "Сверлильный станок Optimum 3020217 OPTIdrill B16H (380 В)", "B16H"),
        "80596",
    ),
    (
        _runtime_card("1664", "351517010", "Неподвижный люнет для токарного станка S 750"),
        _runtime_registry("82947", "Токарный станок OPTIturn S 750", "S750"),
        "82947",
    ),
])
def test_automatic_quarantine_persists_the_stable_unlinked_decision(card, registry, target):
    existing = {
        card.external_id: {
            "sterbrust_product_id": target,
            "current_auto_accepted": True,
            "origin_match_method": "BRAND_MODEL",
            "manual_or_human_confirmed": False,
        }
    }
    first = match_cards([card], registry, existing)[0]
    repeated = match_cards([card], registry, {})[0]

    assert first["auto_accepted"] == "FALSE"
    assert first["match_method"] != "EXISTING_LINK_CONFLICT"
    assert (first["status"], first["match_method"], first["sterbrust_product_id"], first["conflict_class"]) == (
        repeated["status"], repeated["match_method"], repeated["sterbrust_product_id"], repeated["conflict_class"],
    )
    assert decision_fingerprint(
        first["status"], first["match_method"], first["sterbrust_product_id"] or None, first["conflict_class"],
    ) == decision_fingerprint(
        repeated["status"], repeated["match_method"], repeated["sterbrust_product_id"] or None,
        repeated["conflict_class"],
    )
    assert warning_fingerprint(json.loads(first["warnings"])) == warning_fingerprint(
        json.loads(repeated["warnings"])
    )


def test_manual_confirmation_is_not_reconfirmed_or_reopened():
    assert plan_review_sync(
        lifecycle="REOPENED_SOURCE_CHANGED", current_proposed="82431",
        decision="EXACT_EXISTING", best_id="82431", fingerprint_changed=True,
        human_decision_class="MANUAL_CONFIRMED", human_sterbrust_id="82431",
    ) == "restore_manual"
    assert plan_review_sync(
        lifecycle="RESOLVED", current_proposed=None, decision="EXACT_EXISTING",
        best_id="82431", fingerprint_changed=False,
        human_decision_class="MANUAL_CONFIRMED", human_sterbrust_id="82431",
    ) == "unchanged"
    assert plan_review_sync(
        lifecycle="RESOLVED", current_proposed=None, decision="CONFLICT",
        best_id="80596", fingerprint_changed=True,
        human_decision_class="MANUAL_CONFIRMED", human_sterbrust_id="80596",
    ) == "unchanged"


def test_open_high_syncs_without_second_confirmation_and_is_idempotent():
    assert plan_review_sync(
        lifecycle="OPEN", current_proposed="82431", decision="HIGH_CONFIDENCE_EXISTING",
        best_id="82431", fingerprint_changed=False,
    ) == "sync_confirmed"
    assert plan_review_sync(
        lifecycle="RESOLVED", current_proposed=None, decision="HIGH_CONFIDENCE_EXISTING",
        best_id="82431", fingerprint_changed=False,
    ) == "unchanged"
    assert plan_review_sync(
        lifecycle="OPEN", current_proposed="80738", decision="CONFLICT",
        best_id="18093", fingerprint_changed=True,
    ) == "refresh_open"
    assert plan_review_sync(
        lifecycle="OPEN", current_proposed=None, decision="CONFLICT",
        best_id="18093", fingerprint_changed=False,
    ) == "unchanged"


def test_conflict_candidate_is_not_emitted_as_a_proposal():
    row_data = {
        "supplier_code": "optimum", "external_id": "862", "source_product_id": 3370,
        "decision": "CONFLICT", "proposed_sterbrust_id": "18093",
    }
    product_node = ET.fromstring(b"".join(iter_full_xml([row_data]))).find(".//product")
    assert product_node.find("matching/proposed_sterbrust_id") is None
    assert product_node.find("identity/stb_1c_bitrix_id") is None


def test_backorder_without_quantity_is_not_available_true():
    bad = snapshot(row(availability_normalized="backorder", quantity=None))
    with pytest.raises(FeedGenerationError, match="BACKORDER_UNKNOWN_QUANTITY"):
        build_yml(bad, POLICY)
    artifact = generate_canonical_xml(bad)
    assert not ET.fromstring(artifact.data).findall(".//offer")
    assert artifact.manifest["short_xml_exclusion_counts"]["backorder_unknown_quantity"] == 1
    assert b'available="true"' not in artifact.data


def test_unknown_price_type_is_not_exported_as_retail():
    artifact = generate_canonical_xml(snapshot(row(price_type="unknown")))
    assert not ET.fromstring(artifact.data).findall(".//offer")
    assert artifact.manifest["short_xml_exclusion_counts"]["unsupported_price_type"] == 1


def test_empty_price_is_not_replaced_by_zero():
    artifact = generate_canonical_xml(snapshot(row(price=None)))
    assert not ET.fromstring(artifact.data).findall(".//offer")
    assert b">0<" not in artifact.data


def test_stale_confirmed_id_fails_closed_in_russian():
    with pytest.raises(FeedGenerationError, match="Короткий XML остановлен") as caught:
        generate_canonical_xml(snapshot(row(confirmed_identity_id="17252")))
    assert STALE_SELECTION_RU in caught.value.violations
    assert any("88585" in item and "17252" in item for item in caught.value.violations)


def test_supported_price_and_known_backorder_quantity_still_export():
    artifact = generate_canonical_xml(snapshot(row(
        price_type="retail", availability_normalized="backorder", quantity=Decimal("2"),
    )))
    offer = ET.fromstring(artifact.data).find(".//offer")
    assert offer.get("available") == "true"
    assert offer.findtext("stb_1c_bitrix_id") == offer.get("id")
    assert offer.find("param").text == "2"
