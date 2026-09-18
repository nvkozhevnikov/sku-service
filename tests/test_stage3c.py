import csv
import json
import unittest
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORTS = ROOT / "reports"


class Stage3CReportsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.traces = json.loads((REPORTS / "STAGE3C_PRODUCT_TRACE.json").read_text(encoding="utf-8"))

    def test_all_required_outputs_exist(self):
        required = {
            "STAGE3C_SUMMARY.txt", "STAGE3C_QA_REPORT.md", "STAGE3C_FUNCTIONAL_MVP_REPORT.md",
            "STAGE3C_PRODUCT_TRACE.csv", "STAGE3C_PRODUCT_TRACE.json", "STAGE3C_SAMPLE_SELECTION.csv",
            "STAGE3C_NEW_CANDIDATE_AUDIT.csv", "STAGE3C_REVIEW_AUDIT.csv", "STAGE3C_CONFLICT_AUDIT.csv",
            "STAGE3C_SOURCE_DB_CONSISTENCY.csv", "STAGE3C_MATCH_EXPLANATIONS.md",
            "STAGE3C_PP800F_TRACE.md", "STAGE3C_MRX3_MRX4_TRACE.md",
        }
        self.assertEqual([], sorted(name for name in required if not (REPORTS / name).is_file()))

    def test_representative_sample_quotas(self):
        counts = Counter(row["matching status"] for row in self.traces)
        self.assertGreaterEqual(len(self.traces), 20)
        for status, minimum in {"EXACT_MATCH": 5, "HIGH_CONFIDENCE_MATCH": 2, "REVIEW": 3, "NEW_CANDIDATE": 3, "CONFLICT": 3}.items():
            self.assertGreaterEqual(counts[status], minimum)

    def test_protected_regressions(self):
        by_id = {row["source_external_id"]: row for row in self.traces}
        self.assertEqual("EXACT_MATCH / SUPPLIER_ARTICLE_EXACT", by_id["297"]["RUN1 decision"])
        self.assertEqual("EXACT_MATCH / EXISTING_LINK", by_id["297"]["RUN2 decision"])
        self.assertEqual("CONFLICT", by_id["305"]["matching status"])
        self.assertEqual("FALSE", by_id["305"]["auto_accepted"])

    def test_required_feature_coverage(self):
        self.assertTrue(any(not row["brand_raw"] for row in self.traces))
        self.assertTrue(any(row["properties_count"] >= 15 for row in self.traces))
        self.assertTrue(any(row["images_count"] and row["documents_count"] for row in self.traces))
        self.assertTrue(any(row["options_count"] and row["relations_count"] for row in self.traces))
        self.assertTrue(any(row["availability_normalized"] in {"out_of_stock", "preorder"} for row in self.traces))
        self.assertTrue(any(row["commercial_changed_between_runs"] == "TRUE" for row in self.traces))

    def test_no_mandatory_data_loss(self):
        with (REPORTS / "STAGE3C_SOURCE_DB_CONSISTENCY.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertEqual(len(self.traces), len(rows))
        self.assertEqual(0, sum(int(row["mandatory_data_loss"]) for row in rows))

    def test_new_candidate_audit_has_ten_rows_and_no_auto_create(self):
        with (REPORTS / "STAGE3C_NEW_CANDIDATE_AUDIT.csv").open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
        self.assertGreaterEqual(len(rows), 10)
        self.assertEqual({"FALSE"}, {row["auto_create_catalog_product"] for row in rows})


if __name__ == "__main__":
    unittest.main()
