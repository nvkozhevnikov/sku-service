import unittest

from sterbrust_matching.matching import Product, match_product
from sterbrust_matching.normalization import (
    extract_model, model_identity_evidence, normalize_model, normalize_model_with_flags,
)
from sterbrust_matching.property_normalization import (
    IDENTITY_CRITICAL,
    SUPPORTING,
    PROPERTY_IDENTITY_CONFLICT,
    PROPERTY_UNIT_VARIATION,
    PROPERTY_WITHIN_TOLERANCE,
    PropertyPolicy,
    compare_property,
)
from sterbrust_matching.decision_history import (
    INSERT_DECISION_EVENT,
    UPDATE_LAST_VALIDATED,
    PersistedDecision,
    decision_fingerprint,
    persistence_action,
    warning_fingerprint,
)


class AntiDuplicateTests(unittest.TestCase):
    def _pp800(self):
        source = Product("297", "Partner PP-800F Универсальный заточной станок", "Partner", "PP-800F", "508002", supplier_code="partner_st")
        target = Product("88585", "Универсальный заточной станок Partner PP-800F", "Partner", "PP-600F",
                         manufacturer_article="508002", supplier_identifiers={"partner_st": ("508002",)})
        return source, target

    def test_existing_link_stale_model_property(self):
        source, target = self._pp800()
        result = match_product(source, [], existing_link=target)
        self.assertEqual((result.status, result.method, result.auto_accepted), ("EXACT_MATCH", "EXISTING_LINK", True))
        self.assertIn("STERBRUST_MODEL_PROPERTY_STALE", result.warnings)

    def test_existing_link_supplier_article_confirms(self):
        source, target = self._pp800()
        result = match_product(source, [], existing_link=target)
        self.assertTrue(result.evidence["supplier_specific_article_confirmed"])

    def test_existing_link_real_identity_change_conflict(self):
        source = Product("s", "Partner MR-X4", "Partner", "MR-X4", "221495", supplier_code="partner_st")
        old = Product("t", "Partner MR-X3", "Partner", "MR-X3", manufacturer_article="211301",
                      supplier_identifiers={"partner_st": ("211301",)})
        result = match_product(source, [], existing_link=old)
        self.assertEqual(result.status, "CONFLICT")
        self.assertEqual(result.evidence["conflict_class"], "IDENTITY_CONFLICT")

    def test_existing_link_repeated_validation_stable(self):
        source, target = self._pp800()
        run1 = match_product(source, [target])
        run2 = match_product(source, [], existing_link=target)
        self.assertEqual(run1.status, run2.status)
        self.assertEqual(run2.method, "EXISTING_LINK")

    def test_property_voltage_unit_variation(self):
        result = compare_property("380 В", "380V", "voltage")
        self.assertTrue(result.compatible)
        self.assertEqual(result.classification, PROPERTY_UNIT_VARIATION)

    def test_property_power_decimal_variation(self):
        result = compare_property("7,5 кВт", "7.5 kW", "power")
        self.assertTrue(result.compatible)

    def test_property_power_unit_conversion(self):
        result = compare_property("7.5 kW", "7500 W", "power")
        self.assertTrue(result.compatible)
        self.assertEqual(result.classification, PROPERTY_UNIT_VARIATION)

    def test_property_length_conversion(self):
        self.assertTrue(compare_property("2500 mm", "2.5 m", "length").compatible)

    def test_property_identity_critical_difference(self):
        result = compare_property("2500 mm", "3200 mm", "length")
        self.assertFalse(result.compatible)
        self.assertEqual(result.classification, PROPERTY_IDENTITY_CONFLICT)

    def test_property_supporting_tolerance(self):
        result = compare_property("820 kg", "821 kg", "weight")
        self.assertTrue(result.compatible)
        self.assertEqual(result.classification, PROPERTY_WITHIN_TOLERANCE)

    def test_duplicate_supplier_article_review(self):
        self.test_exact_partner_article_duplicate_review()

    def test_model_brand_prefix_still_supported(self):
        self.test_model_brand_prefix_removed()

    def test_model_latin_cyrillic_still_supported(self):
        self.test_model_latin_cyrillic_confusable()

    def test_unchanged_decision_updates_validation_only(self):
        value = PersistedDecision(
            decision_fingerprint("EXACT_MATCH", "EXISTING_LINK", "500"),
            warning_fingerprint({"STERBRUST_MODEL_PROPERTY_STALE": {"field": "MODEL"}}),
        )
        self.assertEqual(persistence_action(value, value), UPDATE_LAST_VALIDATED)

    def test_material_warning_change_creates_history_event(self):
        decision = decision_fingerprint("EXACT_MATCH", "EXISTING_LINK", "500")
        before = PersistedDecision(decision, warning_fingerprint({}))
        after = PersistedDecision(decision, warning_fingerprint({"NEW_WARNING": {}}))
        self.assertEqual(persistence_action(before, after), INSERT_DECISION_EVENT)

    def test_model_brand_prefix_removed(self):
        normalized, flags = normalize_model_with_flags("Partner M3080A", "Partner")
        self.assertEqual(normalized, "m3080a")
        self.assertIn("MODEL_CONTAINS_BRAND_PREFIX", flags)

    def test_model_latin_cyrillic_confusable(self):
        self.assertEqual(normalize_model("M3080B"), normalize_model("M3080В"))

    def test_exact_supplier_article_stale_model_property(self):
        source = Product("s", "Partner PP-800F", "Partner", "PP-800F", "508002", supplier_code="partner_st")
        target = Product("t", "Partner PP-800F", "Partner", "PP-600F", manufacturer_article="508002",
                         supplier_identifiers={"partner_st": ("508002",)})
        result = match_product(source, [target])
        self.assertEqual(result.status, "EXACT_MATCH")
        self.assertIn("STERBRUST_MODEL_PROPERTY_STALE", result.warnings)
        self.assertEqual(result.evidence["conflict_class"], "FIELD_DATA_CONFLICT")

    def test_exact_supplier_article_candidate_name_confirms_model(self):
        source = Product("s", "Partner ISY-250", "Partner", "ISY-250", "810250", supplier_code="partner_st")
        target = Product("t", "Фаскосниматель Partner ISY-250", "Partner", "ISY-150",
                         manufacturer_article="810250", supplier_identifiers={"partner_st": ("810250",)})
        self.assertTrue(match_product(source, [target]).auto_accepted)

    def test_exact_supplier_article_real_model_conflict(self):
        source = Product("s", "Partner MR-X3", "Partner", "MR-X3", "211301", supplier_code="partner_st")
        target = Product("t", "Partner MR-X4", "Partner", "MR-X4", manufacturer_article="221495",
                         supplier_identifiers={"partner_st": ("211301",)})
        result = match_product(source, [target])
        self.assertEqual(result.status, "CONFLICT")
        self.assertEqual(result.evidence["conflict_class"], "IDENTITY_CONFLICT")

    def test_new_suffix_extraction_warning(self):
        source = Product("s", "Partner LX13 NEW", "Partner", "LX13", "211306", supplier_code="partner_st")
        target = Product("t", "Partner LX13 NEW", "Partner", "LX13 NEW", manufacturer_article="211306",
                         supplier_identifiers={"partner_st": ("211306",)})
        result = match_product(source, [target])
        self.assertEqual(result.status, "EXACT_MATCH")
        self.assertEqual(result.evidence["conflict_class"], "EXTRACTION_CONFLICT")

    def test_mrx3_mrx4_remains_conflict(self):
        self.test_exact_supplier_article_real_model_conflict()

    def test_duplicate_supplier_article_remains_review(self):
        self.test_exact_partner_article_duplicate_review()

    def test_fuzzy_name_never_auto_match(self):
        source = Product("s", "Identical product title")
        result = match_product(source, [Product("t", "Identical product title")])
        self.assertEqual(result.status, "REVIEW")
        self.assertFalse(result.auto_accepted)

    def test_supplier_specific_article_extraction(self):
        target = Product("t", "Partner machine", "Partner", supplier_identifiers={"partner_st": ("SKU-1",)})
        self.assertEqual(target.supplier_identifiers["partner_st"], ("SKU-1",))

    def test_multiple_supplier_articles_one_product(self):
        target = Product("t", "Machine", supplier_identifiers={"partner_st": ("P-1",), "kedr": ("K-2",)})
        self.assertEqual(set(target.supplier_identifiers), {"partner_st", "kedr"})

    def test_exact_partner_article_unique_match(self):
        source = Product("s", "Partner machine", "Partner", supplier_article="P-100", supplier_code="partner_st")
        target = Product("t", "Partner machine", "Partner", supplier_identifiers={"partner_st": ("P-100",)})
        result = match_product(source, [target])
        self.assertEqual((result.status, result.method, result.auto_accepted),
                         ("EXACT_MATCH", "SUPPLIER_ARTICLE_EXACT", True))

    def test_exact_partner_article_duplicate_review(self):
        source = Product("s", "Partner machine", "Partner", supplier_article="P-100", supplier_code="partner_st")
        targets = [
            Product("t1", "Partner machine A", "Partner", supplier_identifiers={"partner_st": ("P-100",)}),
            Product("t2", "Partner machine B", "Partner", supplier_identifiers={"partner_st": ("P-100",)}),
        ]
        result = match_product(source, targets)
        self.assertEqual((result.status, result.method, result.auto_accepted),
                         ("REVIEW", "DUPLICATE_SUPPLIER_ARTICLE", False))

    def test_exact_supplier_article_brand_conflict(self):
        source = Product("s", "Machine", "Brand A", supplier_article="P-100", supplier_code="partner_st")
        target = Product("t", "Machine", "Brand B", supplier_identifiers={"partner_st": ("P-100",)})
        result = match_product(source, [target])
        self.assertEqual(result.status, "CONFLICT")
        self.assertFalse(result.auto_accepted)

    def test_supplier_article_not_global(self):
        source = Product("s", "Machine", supplier_article="100", supplier_code="partner_st")
        target = Product("t", "Machine", supplier_identifiers={"kedr": ("100",)})
        result = match_product(source, [target])
        self.assertNotEqual(result.method, "SUPPLIER_ARTICLE_EXACT")
        self.assertFalse(result.auto_accepted)

    def test_legacy_unnamespaced_supplier_article_never_auto_matches(self):
        source = Product("s", "BrandX machine", "BrandX", supplier_article="100", supplier_code="partner_st")
        candidate = Product("t", "BrandX machine", "BrandX", supplier_article="100", supplier_code="kedr")
        result = match_product(source, [candidate])
        self.assertFalse(result.auto_accepted)
        self.assertNotIn(result.method, {"SUPPLIER_ARTICLE_EXACT", "SUPPLIER_ARTICLE_BRAND"})

    def test_namespaced_supplier_article_positive_regression(self):
        source = Product("s", "BrandX machine", "BrandX", supplier_article="100", supplier_code="partner_st")
        candidate = Product("t", "BrandX machine", "BrandX", supplier_identifiers={"partner_st": ("100",)})
        result = match_product(source, [candidate])
        self.assertEqual((result.status, result.method, result.auto_accepted),
                         ("EXACT_MATCH", "SUPPLIER_ARTICLE_EXACT", True))

    def test_cross_supplier_identifier_namespace_never_matches(self):
        source = Product("s", "BrandX machine", "BrandX", supplier_article="100", supplier_code="partner_st")
        candidate = Product("t", "BrandX machine", "BrandX", supplier_article="100",
                            supplier_code="kedr", supplier_identifiers={"kedr": ("100",)})
        result = match_product(source, [candidate])
        self.assertFalse(result.auto_accepted)
        self.assertNotIn(result.method, {"SUPPLIER_ARTICLE_EXACT", "SUPPLIER_ARTICLE_BRAND"})

    def test_general_article_not_supplier_article(self):
        source = Product("s", "Machine", supplier_article="100", supplier_code="partner_st")
        target = Product("t", "Machine", manufacturer_article="100")
        result = match_product(source, [target])
        self.assertNotEqual(result.method, "SUPPLIER_ARTICLE_EXACT")

    def test_existing_mapping_precedes_supplier_article(self):
        source = Product("s", "Machine", supplier_article="100", supplier_code="partner_st")
        exact = Product("article", "Machine", supplier_identifiers={"partner_st": ("100",)})
        existing = Product("existing", "Machine")
        result = match_product(source, [exact], existing_link=existing)
        self.assertEqual((result.method, result.candidate_key), ("EXISTING_LINK", "existing"))

    def test_case_a_brand_model_matches(self):
        source = Product("supplier", "Ленточнопильный станок Stalex BS 912 G", "Stalex", "BS 912 G")
        target = Product("sterbrust", "STALEX BS-912G", "STALEX", "BS-912G")
        self.assertEqual(match_product(source, [target]).status, "HIGH_CONFIDENCE_MATCH")

    def test_case_b_suffix_is_significant(self):
        source = Product("supplier", "STALEX BS-912GC", "STALEX", "BS-912GC")
        target = Product("sterbrust", "STALEX BS-912G", "STALEX", "BS-912G")
        result = match_product(source, [target])
        self.assertIn(result.status, {"CONFLICT", "NEW_CANDIDATE", "REVIEW"})
        self.assertFalse(result.auto_accepted)
        self.assertNotEqual(normalize_model("BS-912G"), normalize_model("BS-912GC"))

    def test_case_c_same_supplier_article_different_brand_conflicts(self):
        source = Product("supplier", "Machine XYZ 500", "Brand A", "XYZ 500", "S-1")
        target = Product("sterbrust", "Machine XYZ-500", "Brand B", "XYZ-500", "S-1")
        result = match_product(source, [target])
        self.assertEqual(result.status, "CONFLICT")
        self.assertFalse(result.auto_accepted)

    def test_case_d_high_name_similarity_model_differs(self):
        source = Product("supplier", "Станок промышленный XYZ-700", "ACME", "XYZ-700")
        target = Product("sterbrust", "Станок промышленный XYZ-500", "ACME", "XYZ-500")
        result = match_product(source, [target])
        self.assertFalse(result.auto_accepted)
        self.assertIn("model", result.conflicts)

    def test_case_e_two_offers_one_catalog_product(self):
        canonical = "catalog-1"
        source_a = {"catalog_product_id": canonical, "offer": {"price": 450000, "availability": "in_stock"}}
        source_b = {"catalog_product_id": canonical, "offer": {"price": 438000, "availability": "out_of_stock"}}
        self.assertEqual(len({source_a["catalog_product_id"], source_b["catalog_product_id"]}), 1)
        self.assertEqual(len([source_a["offer"], source_b["offer"]]), 2)

    def test_fuzzy_name_still_never_auto_matches(self):
        source = Product("s", "Almost Identical Machine")
        target = Product("t", "Almost Identical Machine")
        result = match_product(source, [target])
        self.assertEqual(result.status, "REVIEW")
        self.assertFalse(result.auto_accepted)

    def test_execution_qualifiers_are_preserved(self):
        self.assertEqual(extract_model("Partner LX20 PRO станок"), "lx20pro")
        self.assertEqual(extract_model("Partner LX20 NEW станок"), "lx20new")
        self.assertNotEqual(extract_model("Partner LX20 PRO"), extract_model("Partner LX20 NEW"))

    def test_compact_uppercase_model_suffix_is_preserved(self):
        self.assertEqual(extract_model("Токарный станок Optimum TU 2304 V"), "tu2304v")
        self.assertEqual(extract_model("Токарный станок Optimum TU2304 V"), "tu2304v")
        self.assertEqual(extract_model("ABC-123 V"), "abc123v")
        self.assertEqual(extract_model("ABC123 S"), "abc123s")
        self.assertEqual(extract_model("ABC123 D"), "abc123d")
        self.assertNotEqual(extract_model("TU2304 V"), extract_model("TU2304"))

    def test_following_noun_is_not_absorbed_as_model_suffix(self):
        self.assertEqual(extract_model("PP-800F Универсальный станок"), "pp800f")

    def test_tu2304v_does_not_auto_match_tu2304(self):
        target = Product("19862", "Станок токарный настольный Optimum TU2304 3420320",
                         "Optimum", "TU2304", manufacturer_article="3420320")
        for title in ("Токарный станок Optimum TU 2304 V",
                      "Токарный станок Optimum TU2304 V",
                      "Токарный станок Optimum TU2304V"):
            source = Product("782", title, "Optimum", extract_model(title),
                             supplier_article="3420325", supplier_code="optimum")
            result = match_product(source, [target])
            self.assertFalse(result.auto_accepted, title)
            self.assertIn(result.status, {"NEW_CANDIDATE", "REVIEW"}, title)

    def test_tu2304v_can_match_same_execution(self):
        title = "Токарный станок Optimum TU 2304 V"
        result = match_product(Product("s", title, "Optimum", extract_model(title)),
                               [Product("t", "Токарный станок Optimum TU2304 V", "Optimum", "TU2304 V")])
        self.assertEqual((result.method, result.auto_accepted), ("BRAND_MODEL", True))

    def test_etm_pro_models_remain_distinct_and_matchable(self):
        for model in ("ETM-16 Pro", "ETM-30 Pro"):
            title = f"Partner {model} Электрический резьбонарезной манипулятор"
            result = match_product(Product("s", title, "Partner", extract_model(title)),
                                   [Product("t", title, "Partner", model)])
            self.assertEqual((result.method, result.auto_accepted), ("BRAND_MODEL", True))

    def test_controller_compatibility_model_is_not_auto_identity(self):
        source = Product("1835", "Контроллер для электрического манипулятора ETM-16U", "Partner", "ETM-16U")
        target = Product("96887", "Манипулятор для нарезки резьбы Partner ETM-16U", "Partner", "ETM-16U")
        result = match_product(source, [target])
        self.assertFalse(result.auto_accepted)
        self.assertEqual(result.method, "BRAND_MODEL_IDENTITY_REVIEW")
        self.assertEqual(model_identity_evidence(source.name)["model_ownership"], "REFERENCE_COMPATIBILITY")

    def test_handle_compatibility_model_is_not_auto_identity(self):
        source = Product("1837", "Ручка управления для манипулятора ETM-16U", "Partner", "ETM-16U")
        target = Product("96887", "Манипулятор Partner ETM-16U", "Partner", "ETM-16U")
        self.assertFalse(match_product(source, [target]).auto_accepted)

    def test_jaws_compatibility_model_is_not_auto_identity(self):
        source = Product("561", "L-образные губки для тисков OPTIMUM HCV 125", "Optimum", "HCV 125")
        target = Product("18451", "Гидравлические тиски Optimum HCV 125", "Optimum", "HCV 125")
        self.assertFalse(match_product(source, [target]).auto_accepted)

    def test_lx20_pro_and_new_do_not_auto_collapse(self):
        source = Product("1655", "Partner LX20 PRO Станок для заточки фрез", "Partner", extract_model("Partner LX20 PRO"))
        target = Product("61421", "Станок для заточки фрез Partner LX20 NEW", "Partner", "LX20")
        result = match_product(source, [target], existing_link=target, existing_link_origin="BRAND_MODEL")
        self.assertEqual(result.status, "CONFLICT")
        self.assertFalse(result.auto_accepted)

    def test_pp13d_new_and_base_do_not_auto_collapse(self):
        source = Product("1656", "Partner PP-13D NEW Станок для заточки сверл", "Partner", extract_model("Partner PP-13D NEW"))
        target = Product("40495", "Станок для заточки сверл Partner PP-13D", "Partner", "PP-13D")
        result = match_product(source, [target], existing_link=target, existing_link_origin="BRAND_MODEL")
        self.assertEqual(result.status, "CONFLICT")
        self.assertFalse(result.auto_accepted)

    def test_tm9060_genuine_title_remains_brand_model_matchable(self):
        title = "Partner TM-9060, тумба металлическая, 900х600х700 мм"
        result = match_product(Product("1704", title, "Partner", extract_model(title)),
                               [Product("117191", title, "Partner", "TM-9060")])
        self.assertEqual((result.status, result.method, result.auto_accepted),
                         ("HIGH_CONFIDENCE_MATCH", "BRAND_MODEL", True))

    def test_manual_existing_link_is_preserved_without_becoming_automatic(self):
        source = Product("s", "Контроллер для станка X1", "Brand", "X1")
        target = Product("t", "Станок X1", "Brand", "X1")
        result = match_product(source, [], existing_link=target, existing_link_manual=True)
        self.assertEqual(result.method, "EXISTING_LINK_MANUAL")
        self.assertFalse(result.auto_accepted)


if __name__ == "__main__":
    unittest.main()
