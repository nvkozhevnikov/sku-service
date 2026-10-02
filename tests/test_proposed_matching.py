from collections import defaultdict
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from universal_supplier.matching_service import RegistryContext
from universal_supplier.proposed_matching import classify_persisted_sources
from universal_supplier.full_supplier_collection import FullCandidate, FullManifest, _candidate_sha
from scripts.generate_proposed_matching import classify_with_manifest, prior_unresolved_urls


def _registry(model: str) -> RegistryContext:
    key = "18077"
    row = {"sterbrust_product_id": key, "name": f"Ленточнопильный станок Beka-Mak {model}",
           "brand": "Beka-Mak", "model": model, "article": "",
           "category": "Ленточнопильные станки", "properties": {},
           "supplier_identifiers": defaultdict(list)}
    compact = "".join(char for char in model.lower() if char.isalnum())
    return RegistryContext({key: row}, defaultdict(list), {compact: [key]},
                           {"beka-mak": [key]}, defaultdict(list))


def _source(model: str) -> dict:
    return {"supplier_code": "intervesp", "external_id": "9028", "source_product_id": 1,
            "offer_id": 2, "source_url": "https://intervesp.ru/catalog/beka-mak/example/",
            "name": f"Ленточнопильный станок Beka-Mak {model}", "sku": "",
            "raw_data": {"source_identity": {"raw_model": model},
                         "source_content": {"source_category": "Ленточнопильные станки"}},
            "price_state": "numeric_public", "price": 100, "currency": "RUB"}


class ProposedMatchingTests(unittest.TestCase):
    def test_wp2_cannot_be_confirmed_as_base(self):
        row = classify_persisted_sources([_source("BMSY-440DGH-WP2")],
                                          _registry("BMSY-440DGH"))[0]
        self.assertNotEqual(row["classification"], "EXISTING_CONFIRMED")
        self.assertEqual(row["sterbrust_product_id"], "")
        self.assertFalse(row["full_model_confirmed"])

    def test_exact_execution_can_be_confirmed(self):
        row = classify_persisted_sources([_source("BMSY-440DGH")],
                                          _registry("BMSY-440DGH"))[0]
        self.assertEqual(row["classification"], "EXISTING_CONFIRMED")
        self.assertTrue(row["full_model_confirmed"])

    def test_meaningful_execution_suffixes_never_match_a_base_or_other_drive(self):
        for candidate, canonical in (("BMSO-420CS-NC", "BMSO-420CS"),
                                     ("BMSY-360DG-ECO", "BMSY-360DGH-ECO"),
                                     ("BMSY-440DGH-WP1", "BMSY-440DGH-WP2"),
                                     ("BMSY-440DGH-PRO", "BMSY-440DGH")):
            with self.subTest(candidate=candidate, canonical=canonical):
                row = classify_persisted_sources([_source(candidate)], _registry(canonical))[0]
                self.assertNotEqual(row["classification"], "EXISTING_CONFIRMED")
                self.assertEqual(row["sterbrust_product_id"], "")

    def test_historical_card_outside_pinned_manifest_is_review_even_if_model_matches(self):
        candidate = FullCandidate("intervesp", "https://intervesp.ru/catalog/beka-mak/other/", "BMSY-440DGH")
        manifest = FullManifest((candidate,), {}, (), _candidate_sha((candidate,)))
        rows, quarantined = classify_with_manifest([_source("BMSY-440DGH")], manifest,
                                                   _registry("BMSY-440DGH"))
        self.assertEqual(quarantined, 1)
        self.assertEqual(rows[0]["classification"], "REVIEW")
        self.assertEqual(rows[0]["sterbrust_product_id"], "")
        self.assertEqual(rows[0]["match_status"], "OUTSIDE_PINNED_MANIFEST")

    def test_prior_review_stays_review_after_later_successful_parse(self):
        source = _source("BMSY-440DGH")
        candidate = FullCandidate("intervesp", source["source_url"], "BMSY-440DGH")
        manifest = FullManifest((candidate,), {}, (), _candidate_sha((candidate,)))
        rows, outside = classify_with_manifest(
            [source], manifest, _registry("BMSY-440DGH"),
            prior_review_urls={(source["supplier_code"], source["source_url"])})
        self.assertEqual(outside, 0)
        self.assertEqual(rows[0]["classification"], "REVIEW")
        self.assertEqual(rows[0]["sterbrust_product_id"], "")
        self.assertEqual(rows[0]["match_status"], "PRIOR_REVIEW_PENDING_OPERATOR")

    def test_prior_review_report_must_match_pinned_manifest(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            path.write_text(json.dumps({"manifest_sha256": "wrong", "rows": [
                {"source": "intervesp", "url": _source("BMSY-440DGH")["source_url"],
                 "status": "REVIEW"}]}), encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "manifest changed"):
                prior_unresolved_urls([path], manifest_sha256="a" * 64)

    def test_later_persisted_report_cannot_erase_prior_review(self):
        url = _source("BMSY-440DGH")["source_url"]
        with TemporaryDirectory() as directory:
            first, later = (Path(directory) / name for name in ("first.json", "later.json"))
            for path, status in ((first, "REVIEW"), (later, "PERSISTED")):
                path.write_text(json.dumps({"manifest_sha256": "a" * 64, "rows": [
                    {"source": "intervesp", "url": url, "status": status}]}), encoding="utf-8")
            self.assertEqual(prior_unresolved_urls([first, later], manifest_sha256="a" * 64),
                             {("intervesp", url)})


if __name__ == "__main__":
    unittest.main()
