"""Synthetic/offline contract: evidence is advisory, never matcher mutation."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from universal_supplier.characteristic_evidence import (
    build_evidence, evaluate_candidate, features, normalize_characteristic,
    property_key, unit_from_label,
)


def props(power="0,9", blade="3660x27x0.9"):
    return [{"name": "Размеры полотна, (мм)", "value": blade},
            {"name": "Мощность двигателя, (кВт)", "value": power},
            {"name": "Распил 0 град. (круг), (мм)", "value": "230"},
            {"name": "Тип станка", "value": "двухколонный"},
            {"name": "Тип автоматизации", "value": "автоматический"}]


def pair(i=1, section=4037, source="intervesp", target=None):
    return {"source": source, "external_id": str(i), "sterbrust_product_id": str(target or i),
            "classification": "EXISTING_CONFIRMED", "full_model_confirmed": True,
            "equipment_type": "bandsaw", "source_category": "Ленточнопильные станки",
            "source_url": f"https://example.invalid/{i}", "evidence_ref": f"capture://{i}",
            "source_properties": props(), "sterbrust_properties": [
                {"name": "Длина пильной ленты, мм", "value": "3660"},
                {"name": "Ширина полотна, мм", "value": "27"},
                {"name": "Толщина полотна, мм", "value": "0.9"},
                {"name": "Мощность, Вт", "value": "900"},
                {"name": "Max диаметр заготовки 90°, мм", "value": "230"},
                {"name": "Конструкция", "value": "двухколонный"},
                {"name": "Тип автоматизации", "value": "автоматический"}],
            "sections": [{"id": section, "active": "Y", "path": "Станки/Ленточнопильные"}]}


def candidate():
    return {"source": "intervesp", "external_id": "new1", "new_candidate_id": "NEWC-1",
            "classification": "NEW_CANDIDATE", "name": "Ленточнопильный станок BEKAMAK BMS-999DG",
            "brand": "BEKAMAK", "model": "BMS-999DG", "model_role": "OWN_MODEL",
            "source_category": "Ленточнопильные станки", "properties": props(),
            "evidence_ref": "capture://synthetic", "absence_review_ref": "synthetic-reviewed-absence",
            "absence_registry_sha256": "registry-hash"}


def group(row):
    return {"status": "PROVEN_EQUIVALENT_NEW_PROPOSAL", "new_group_id": "NEWG-synthetic",
            "equivalence_evidence": [{"synthetic": True}], "source_records": [
                {k: row[k] for k in ("source", "external_id", "new_candidate_id")}]}


INDEX = {"complete": True, "sha256": "registry-hash", "models": {}, "families": {}}


class TypedNormalizationTests(unittest.TestCase):
    def test_units_and_comma(self):
        for text, unit in (("2500", "мм"), ("2500 mm", ""), ("2,5 m", "")):
            self.assertEqual(normalize_characteristic(text, "blade_length", unit)["values"], ["2500"])
        self.assertEqual(normalize_characteristic("0,9 кВт", "main_motor_power")["values"], ["900"])
        self.assertEqual(normalize_characteristic("900", "main_motor_power", "W")["values"], ["900"])

    def test_dimensions_preserve_axes(self):
        a = normalize_characteristic("3660x27x0.9", "blade_dimensions", "mm")
        b = normalize_characteristic("3660 × 27 × 0,9 мм", "blade_dimensions")
        self.assertEqual(a["values"], b["values"])
        self.assertEqual(a["values"], ["3660", "27", "0.9"])
        self.assertNotEqual(a["values"], normalize_characteristic("27x3660x0.9", "blade_dimensions", "mm")["values"])

    def test_speed_not_confused_with_millimeters(self):
        self.assertEqual(unit_from_label("Скорость резки, мм/мин"), "мм/мин")
        self.assertEqual(normalize_characteristic("1200", "blade_speed", "мм/мин")["values"], ["1.2"])
        self.assertEqual(normalize_characteristic("20–100", "blade_speed", "м/мин")["values"], ["20", "100"])

    def test_missing_unknown_and_ambiguous_fail_closed(self):
        for value, unit in ((None, "mm"), ("", "mm"), ("unknown", "mm"), ("10", "inch"), ("10", "")):
            self.assertFalse(normalize_characteristic(value, "blade_length", unit)["parsed"])
        self.assertFalse(normalize_characteristic("1x2x3x4", "blade_dimensions", "mm")["parsed"])

    def test_category_and_signed_angle_scope(self):
        self.assertEqual(property_key("Размеры полотна, мм", "bandsaw"), "blade_dimensions")
        self.assertIsNone(property_key("Размеры полотна, мм", "lathe"))
        self.assertIsNone(property_key("Max диаметр заготовки 45°, мм", "bandsaw"))
        self.assertNotEqual(property_key("Распил +45 град. (круг), мм", "bandsaw"),
                            property_key("Распил -45 град. (круг), мм", "bandsaw"))
        self.assertEqual(property_key("Max диаметр заготовки 90°, мм", "bandsaw"), "cut_round_0")

    def test_component_derivation_retains_raw_evidence(self):
        found = features(props(), "bandsaw")
        component = next(f for f in found if f["key"] == "blade_thickness")
        self.assertEqual(component["derivation"], "dimension_component_2")
        self.assertEqual(component["raw_value"], "3660x27x0.9")

    def test_pump_power_is_not_main_motor_power(self):
        self.assertEqual(property_key("Мощность насоса СОЖ, (кВт)", "bandsaw"), "coolant_pump_power")
        self.assertEqual(property_key("Мощность гидронасоса, кВт", "bandsaw"), "hydraulic_pump_power")
        self.assertIsNone(property_key("Мощность насоса, кВт", "lathe"))
        self.assertEqual(property_key("Мощность, Вт", "bandsaw"), "main_motor_power")


class LearningTests(unittest.TestCase):
    def test_only_confirmed_full_models(self):
        pairs = [pair(i) for i in range(1, 4)]
        ignored = pair(4); ignored["classification"] = "NEW_CANDIDATE"
        other = pair(5); other["full_model_confirmed"] = False
        before = deepcopy(pairs)
        evidence = build_evidence(pairs + [ignored, other])
        self.assertEqual(evidence["confirmed_source_records"], 3)
        self.assertTrue(all(m["verdict"] == "SAFE" for m in evidence["synonym_mappings"]))
        self.assertEqual(evidence["section_mappings"][0]["verdict"], "SAFE")
        self.assertEqual(pairs, before)

    def test_vendor_duplicates_do_not_inflate_canonical_examples(self):
        evidence = build_evidence([pair(i, target=1) for i in range(1, 4)])
        self.assertTrue(all(m["verdict"] == "INSUFFICIENT" for m in evidence["synonym_mappings"]))
        self.assertEqual(evidence["section_mappings"][0]["confirmed_examples_count"], 1)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_evidence([pair(), pair()])

    def test_mismatched_values_are_review_not_synonyms(self):
        pairs = [pair(i) for i in range(1, 4)]
        pairs[2]["source_properties"][1]["value"] = "1.1"
        evidence = build_evidence(pairs)
        mapping = next(m for m in evidence["synonym_mappings"] if m["canonical_property_key"] == "main_motor_power")
        self.assertEqual(mapping["verdict"], "REVIEW")
        self.assertEqual(len(mapping["contradiction_examples"]), 1)
        self.assertAlmostEqual(mapping["agreement_ratio"], 2 / 3)

    def test_section_conflicts_and_inactive_fail_closed(self):
        evidence = build_evidence([pair(1), pair(2), pair(3, section=4074)])
        mapping = evidence["section_mappings"][0]
        self.assertEqual(mapping["verdict"], "REVIEW")
        self.assertEqual(mapping["conflicting_sections"], [4074])
        pairs = [pair(i) for i in range(1, 4)]
        pairs[0]["sections"][0]["active"] = "N"
        self.assertEqual(build_evidence(pairs)["section_mappings"][0]["verdict"], "REVIEW")


class EvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.evidence = build_evidence([pair(i) for i in range(1, 4)])

    def evaluate(self, row=None, **kwargs):
        row = row or candidate()
        return evaluate_candidate(row, self.evidence, registry_index=kwargs.pop("registry_index", INDEX),
                                  new_group=kwargs.pop("new_group", group(row)), **kwargs)

    def test_readiness_is_proposal_opt_in_only_and_no_mutation(self):
        row = candidate(); before = deepcopy(row)
        result = self.evaluate(row)
        self.assertEqual(result["proposed_readiness"], "IDENTITY_EVIDENCE_COMPLETE")
        self.assertFalse(result["ready_to_create_proposed"])
        self.assertEqual(row, before)
        self.assertEqual(self.evaluate(row, enable_ready_proposal=True)["proposed_readiness"], "READY_TO_CREATE")

    def test_existing_conflict_not_rewritten(self):
        for state in ("EXISTING_CONFIRMED", "CONFLICT"):
            row = candidate(); row["classification"] = state
            result = self.evaluate(row, enable_ready_proposal=True)
            self.assertIsNone(result["proposed_readiness"])
            self.assertEqual(result["input_classification"], state)
            self.assertEqual(row["classification"], state)

    def test_missing_properties_never_complete_identity(self):
        row = candidate(); row["properties"] = []
        result = self.evaluate(row, enable_ready_proposal=True)
        self.assertFalse(result["ready_to_create_proposed"])
        self.assertIn("blade_thickness", result["CHARACTERISTIC_IDENTITY_SUPPORT"]["missing_required_properties"])

    def test_no_group_or_wrong_membership_blocks_progress(self):
        row = candidate()
        for value in (None, {}, {**group(row), "source_records": []}):
            self.assertEqual(self.evaluate(row, new_group=value)["proposed_readiness"], "NEW_CANDIDATE")

    def test_absence_support_not_absence_verification(self):
        row = candidate(); row.pop("absence_review_ref")
        result = self.evaluate(row)
        self.assertTrue(result["CANONICAL_ABSENCE_SUPPORT"]["support"])
        self.assertFalse(result["CANONICAL_ABSENCE_SUPPORT"]["verified"])
        self.assertEqual(result["proposed_readiness"], "NEW_GROUPED")
        self.assertFalse(self.evaluate(registry_index={**INDEX, "complete": False})["CANONICAL_ABSENCE_SUPPORT"]["support"])

    def test_exact_or_family_including_inactive_prevents_absence(self):
        for index in ({**INDEX, "models": {"bms999dg": ["inactive-id"]}},
                      {**INDEX, "families": {"bms999": ["another-execution"]}}):
            self.assertFalse(self.evaluate(registry_index=index)["CANONICAL_ABSENCE_SUPPORT"]["support"])

    def test_critical_difference_blocks_but_equal_values_support(self):
        row = candidate(); ref = deepcopy(row)
        good = self.evaluate(row, reference=ref)
        self.assertIn("main_motor_power", good["CHARACTERISTIC_IDENTITY_SUPPORT"]["keys"])
        ref["properties"][1]["value"] = "1.1"
        bad = self.evaluate(row, reference=ref, enable_ready_proposal=True)
        self.assertTrue(bad["CHARACTERISTIC_CONTRADICTION"]["blocked"])
        self.assertFalse(bad["ready_to_create_proposed"])

    def test_known_execution_and_accessory_regressions(self):
        cases = [("OPTIMUM", "Токарный станок TU2304V", "TU2304V", "Токарный станок TU2304", "TU2304"),
                 ("JET", "Станок LX20 Pro", "LX20 Pro", "Станок LX20 NEW", "LX20 NEW"),
                 ("OPTIMUM", "Губки для тисков HCV125", "HCV125", "Тиски HCV125", "HCV125"),
                 ("BEKAMAK", "Ленточнопильный станок BMSY440DGH WP2", "BMSY440DGH WP2",
                  "Ленточнопильный станок BMSY440DGH", "BMSY440DGH")]
        for brand, name, model, other_name, other_model in cases:
            with self.subTest(model=model):
                row = candidate(); row.update(brand=brand, name=name, model=model)
                ref = {**row, "name": other_name, "model": other_model}
                result = self.evaluate(row, reference=ref, enable_ready_proposal=True)
                self.assertTrue(result["CHARACTERISTIC_CONTRADICTION"]["blocked"])
                self.assertFalse(result["ready_to_create_proposed"])

    def test_upstream_conflict_and_execution_cannot_be_overridden(self):
        for key, value in (("match_conflicts", {"model": "conflict"}), ("execution", "WP2"), ("brand", "")):
            row = candidate(); row[key] = value
            self.assertTrue(self.evaluate(row, enable_ready_proposal=True)["CHARACTERISTIC_CONTRADICTION"]["blocked"])

    def test_same_model_with_different_equipment_scope_blocks(self):
        row = candidate(); ref = {**row, "name": "Дисковый станок BEKAMAK BMS-999DG", "source_category": "Дисковые пилы"}
        result = self.evaluate(row, reference=ref, enable_ready_proposal=True)
        self.assertTrue(result["CHARACTERISTIC_CONTRADICTION"]["blocked"])
        row["name"] = ref["name"]
        self.assertIn("title_category_equipment_contradiction", self.evaluate(row)["CHARACTERISTIC_CONTRADICTION"]["reasons"])


class SavedArtifactTests(unittest.TestCase):
    def test_built_offline_bundle_checksums_and_real_ready_zero(self):
        import hashlib
        root = Path(__file__).resolve().parents[1] / "reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED"
        if not root.exists():
            self.skipTest("local saved evidence bundle unavailable; no replacement baseline")
        summary = json.loads((root / "BUILD_VERIFIED.json").read_text(encoding="utf-8"))
        self.assertEqual(summary["ready_to_create_real_records"], 0)
        self.assertEqual(summary["usable_confirmed_pairs"], 119)
        for name, digest in summary["output_sha256"].items():
            self.assertEqual(hashlib.sha256((root / name).read_bytes()).hexdigest(), digest)


class OfflineExtractionTests(unittest.TestCase):
    def test_only_characteristic_table_not_navigation(self):
        from scripts.build_characteristic_evidence import source_properties
        html = '<div class="elTabPropName">NAV</div><div class="elTabPropNum">123</div>'
        html += '<div class="elTabPropGroup"><div class="elTabPropName">Мощность, Вт</div><div class="elTabPropNum">900</div></div>'
        self.assertEqual(source_properties(html, "intervesp"), [{"name": "Мощность, Вт", "value": "900", "unit": "вт"}])
        html = '<table class="props_list"><tr itemprop="additionalProperty"><td itemprop="name">Вес, кг</td><td itemprop="value">500</td></tr>'
        html += '<tr itemprop="additionalProperty"><td itemprop="name">Режущая способность</td><td itemprop="value"></td></tr></table>'
        self.assertEqual(len(source_properties(html, "beka_mak")), 1)
        with self.assertRaises(ValueError):
            source_properties(html, "beka_mak_tr")

    def test_section_path_and_ancestor_guards(self):
        from scripts.build_characteristic_evidence import section_evidence
        sections = {"1": {"name": "Станки", "active": "Y", "iblockSectionId": None},
                    "2": {"name": "Пилы", "active": "Y", "iblockSectionId": 1}}
        self.assertEqual(section_evidence([2], sections)[0]["path"], "Станки / Пилы")
        sections["1"]["active"] = "N"
        self.assertEqual(section_evidence([2], sections)[0]["active"], "N")
        sections["1"].update(active="Y", iblockSectionId=2)
        self.assertEqual(section_evidence([2], sections)[0]["active"], "N")

    def test_output_cannot_replace_old_bundle(self):
        from scripts.build_characteristic_evidence import build_bundle
        with self.assertRaisesRegex(ValueError, "already exists"):
            build_bundle(Path(__file__).parent)


if __name__ == "__main__":
    unittest.main()
