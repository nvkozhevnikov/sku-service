from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.product_identity import (
    OWN_MODEL,
    REFERENCE_MODEL,
    classify_model_role,
    classify_product_kind,
    safe_new_product_decision,
)
from sterbrust_matching.property_normalization import compare_property


def product(key, name, model, **kwargs):
    return Product(key=key, name=name, model=model, brand=kwargs.pop("brand", "Optimum"), **kwargs)


def assert_not_auto(source, candidate):
    result = match_product(source, [candidate])
    assert result.auto_accepted is False
    return result


def test_jaw_hcv125_is_not_hcv125_vise():
    result = assert_not_auto(
        product("561", "L-образные губки для тисков OPTIMUM HCV 125", "HCV125"),
        product("18451", "Гидравлические тиски Optimum HCV 125", "HCV125"),
    )
    assert "product_kind" in result.conflicts or "semantic_identity" in result.conflicts


def test_handle_etm16u_is_not_manipulator():
    assert_not_auto(
        product("1837", "Ручка для манипулятора ETM-16U", "ETM-16U", brand="Partner"),
        product("96887", "Резьбонарезной манипулятор Partner ETM-16U", "ETM-16U", brand="Partner"),
    )


def test_controller_etm16u_is_not_manipulator():
    assert_not_auto(
        product("1835", "Контроллер для манипулятора ETM-16U", "ETM-16U", brand="Partner"),
        product("96887", "Резьбонарезной манипулятор Partner ETM-16U", "ETM-16U", brand="Partner"),
    )


def test_disk_for_pp800f_is_not_pp800f_grinder():
    assert_not_auto(
        product("d", "Алмазный диск для станка Partner PP-800F", "PP-800F", brand="Partner"),
        product("m", "Partner PP-800F универсальный заточной станок", "PP-800F", brand="Partner"),
    )


def test_table_for_manipulator_is_not_manipulator():
    assert_not_auto(
        product("t", "Стол для манипулятора ETM-16U", "ETM-16U", brand="Partner"),
        product("m", "Манипулятор Partner ETM-16U", "ETM-16U", brand="Partner"),
    )


def test_tu2304v_is_not_tu2304():
    assert_not_auto(
        product("782", "Токарный станок Optimum TU 2304 V", "TU2304V"),
        product("c", "Токарный станок Optimum TU 2304", "TU2304"),
    )


def test_tu2304v_may_match_tu2304v():
    result = match_product(
        product("s", "Токарный станок Optimum TU 2304 V", "TU2304V"),
        [product("c", "Токарный станок Optimum TU 2304 V", "TU2304V")],
    )
    assert result.auto_accepted is True


def test_execution_words_are_identity_significant():
    for source_model, candidate_model in (("LX20 PRO", "LX20 NEW"), ("PP-13D NEW", "PP-13D"), ("A1 PLUS", "A1 ECO")):
        assert_not_auto(
            product("s", f"Заточной станок {source_model}", source_model),
            product("c", f"Заточной станок {candidate_model}", candidate_model),
        )


def test_same_brand_model_incompatible_kind_is_not_auto():
    assert_not_auto(
        product("s", "Цанга для Optimum BF20", "BF20"),
        product("c", "Фрезерный станок Optimum BF20", "BF20"),
    )


def test_same_product_kind_and_model_can_match():
    result = match_product(
        product("s", "Станочные тиски Optimum QW125", "QW125"),
        [product("c", "Трехосевые станочные тиски Optimum QW125", "QW125")],
    )
    assert result.auto_accepted is True


def test_category_critical_property_conflict_is_not_auto():
    result = assert_not_auto(
        product("s", "Токарный станок Optimum TU2406", "TU2406", properties={"Мощность двигателя": "7.5 kW"}),
        product("c", "Токарный станок Optimum TU2406", "TU2406", properties={"Motor power": "5500 W"}),
    )
    assert any(key.startswith("category_property:") for key in result.conflicts)


def test_unit_normalization_power_length_thread_and_rpm():
    assert compare_property("7.5 kW", "7500 W", "power").compatible
    assert compare_property("2.5 m", "2500 mm", "length").compatible
    assert compare_property("M3-M16", "M3–M16", "thread_range", {"thread_range": __import__('sterbrust_matching.product_identity', fromlist=['PROPERTY_POLICIES']).PROPERTY_POLICIES["thread_range"]}).compatible
    assert compare_property("4400 об/мин", "4400 rpm", "rpm", {"rpm": __import__('sterbrust_matching.product_identity', fromlist=['PropertyPolicy']).PropertyPolicy("rpm", "SUPPORTING")}).compatible


def test_reference_model_detection():
    jaw = classify_model_role("L-образные губки для тисков OPTIMUM HCV 125")
    handle = classify_model_role("Ручка для манипулятора ETM-16U")
    assert (jaw.role, jaw.reference_model, jaw.reference_parent_kind) == (REFERENCE_MODEL, "hcv125", "vise")
    assert (handle.role, handle.reference_model, handle.reference_parent_kind) == (REFERENCE_MODEL, "etm16u", "manipulator")


def test_machine_purpose_phrase_does_not_turn_own_model_into_reference():
    role = classify_model_role("Partner PP-13D станок для заточки сверл")
    assert role.role == OWN_MODEL
    assert role.own_model == "pp13d"


def test_taxonomy_russian_morphology():
    assert classify_product_kind("Прецизионные станочные тиски").product_kind == "vise"
    assert classify_product_kind("Сменные Г-образные губки").product_kind == "jaw"
    assert classify_product_kind("Резьбонарезной манипулятор").product_kind == "manipulator"


def test_fuzzy_name_is_never_auto():
    result = match_product(
        Product("s", "Настольный заточной станок", brand=""),
        [Product("c", "Настольный заточной станок", brand="")],
    )
    assert result.method == "NAME_CANDIDATE_ONLY"
    assert result.auto_accepted is False


def test_new_product_safety_is_conservative():
    assert safe_new_product_decision(
        existing_confirmed=False, exact_article_match=False,
        compatible_brand_model_candidates=[], normalized_model_candidates=[], semantic_candidates=[],
        model_role=OWN_MODEL, product_kind="lathe",
    )[0] == "SAFE_NEW_PRODUCT_CANDIDATE"
    assert safe_new_product_decision(
        existing_confirmed=False, exact_article_match=False,
        compatible_brand_model_candidates=[], normalized_model_candidates=["42"], semantic_candidates=[],
        model_role=OWN_MODEL, product_kind="lathe",
    )[0] == "REVIEW_EXISTING"
    assert safe_new_product_decision(
        existing_confirmed=False, exact_article_match=False,
        compatible_brand_model_candidates=[], normalized_model_candidates=[], semantic_candidates=[],
        model_role=REFERENCE_MODEL, product_kind="jaw",
    )[0] == "ACCESSORY_OR_COMPATIBILITY_ITEM"
