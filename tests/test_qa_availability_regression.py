"""Regression tests for the vekprom/intervesp QA availability contract.

Root cause (2026-10-12): QA_GATE_VEKPROM failed 66/100 with
``db_availability:out_of_stock!=run1:unknown`` because the QA script carried a
local expectation table mapping ``OutOfStock -> 'unknown'``, contradicting the
canonical persistence contract (``commercial_persistence``), which persists
``OutOfStock -> 'out_of_stock'``.  These tests pin the canonical contract with
independent expected values (so QA cannot become tautological) and prove the
QA check fails on the originally-defective DB value.
"""
import importlib.util
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.adapters.vekprom import parse_vekprom_detail
from universal_supplier.commercial_persistence import (
    HttpCapture, normalize_availability, observation_from_snapshot, product_card_from_snapshot,
)
from universal_supplier.sterbrust_import_contract import normalize_public_availability

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"


def _qa_module():
    spec = importlib.util.spec_from_file_location(
        "qa_vekprom_intervesp", ROOT / "scripts" / "qa_vekprom_intervesp.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class CanonicalAvailabilityContractTests(unittest.TestCase):
    def test_canonical_mapping_matches_independent_expected_values(self):
        # Independent literals (NOT read back from the mapping table) so that a
        # wrong change to _AVAILABILITY itself fails here instead of passing QA.
        self.assertEqual(normalize_availability("InStock"), "in_stock")
        self.assertEqual(normalize_availability("OutOfStock"), "out_of_stock")
        self.assertEqual(normalize_availability("PreOrder"), "preorder")
        self.assertEqual(normalize_availability("Incoming"), "incoming")
        self.assertEqual(normalize_availability("BackOrder"), "backorder")
        self.assertEqual(normalize_availability(None), "unknown")
        self.assertEqual(normalize_availability(""), "unknown")
        self.assertEqual(normalize_availability("unknown"), "unknown")

    def test_agrees_with_public_import_contract_on_schema_org_tokens(self):
        for token in ("InStock", "OutOfStock", "PreOrder", "BackOrder"):
            self.assertEqual(normalize_availability(token), normalize_public_availability(token), token)


class AvailabilityPersistenceRegressionTests(unittest.TestCase):
    def _capture(self, url: str) -> HttpCapture:
        return HttpCapture(url, url, 200, "text/html; charset=utf-8",
                           datetime(2026, 10, 12, tzinfo=timezone.utc), "a" * 64, "b" * 64,
                           "fixture://sanitized", region_code="msk", region_label="Москва")

    def test_vekprom_out_of_stock_fixture_persists_as_out_of_stock(self):
        # The exact class that failed the night QA gate.
        product = parse_vekprom_detail(
            (FIXTURES / "vekprom_bmso_825xs_sanitized.html").read_text(encoding="utf-8"))
        self.assertEqual(product.availability, "OutOfStock")
        card = product_card_from_snapshot(product, self._capture(product.source_url or "https://vekprom.ru/x"))
        observation = observation_from_snapshot(product, self._capture(product.source_url or "https://vekprom.ru/x"))
        self.assertEqual(card.availability_normalized, "out_of_stock")
        self.assertEqual(observation.availability_normalized, "out_of_stock")

    def test_vekprom_in_stock_fixture_persists_as_in_stock(self):
        product = parse_vekprom_detail(
            (FIXTURES / "vekprom_bmsy_440dgh_sanitized.html").read_text(encoding="utf-8"))
        self.assertEqual(product.availability, "InStock")
        card = product_card_from_snapshot(product, self._capture(product.source_url or "https://vekprom.ru/x"))
        self.assertEqual(card.availability_normalized, "in_stock")
        self.assertEqual(card.price, Decimal("2840637"))


class QAAvailabilityCheckTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qa = _qa_module()

    def test_accepts_canonical_projections(self):
        self.assertIsNone(self.qa.availability_mismatch("InStock", "in_stock"))
        self.assertIsNone(self.qa.availability_mismatch("OutOfStock", "out_of_stock"))
        self.assertIsNone(self.qa.availability_mismatch(None, "unknown"))
        self.assertIsNone(self.qa.availability_mismatch("unknown", "unknown"))

    def test_rejects_the_original_defect_out_of_stock_expected_unknown(self):
        # The night gate saw DB 'out_of_stock' where the OLD (buggy) QA
        # expected 'unknown'. The fixed QA must expect 'out_of_stock' and
        # therefore FAIL if the DB really held 'unknown':
        self.assertEqual(self.qa.availability_mismatch("OutOfStock", "unknown"),
                         "db_availability:unknown!=run1:out_of_stock")

    def test_rejects_other_wrong_db_values(self):
        self.assertIsNotNone(self.qa.availability_mismatch("InStock", "out_of_stock"))
        self.assertIsNotNone(self.qa.availability_mismatch("OutOfStock", None))
        self.assertIsNotNone(self.qa.availability_mismatch("OutOfStock", "OutOfStock"))

    def test_qa_script_uses_canonical_helper_not_a_local_table(self):
        source = (ROOT / "scripts" / "qa_vekprom_intervesp.py").read_text(encoding="utf-8")
        self.assertIn("from universal_supplier.commercial_persistence import normalize_availability", source)
        self.assertNotIn("_AVAILABILITY", source)
        # The old defective local expectation table must not come back.
        self.assertNotIn("'OutOfStock': 'unknown'", source)


if __name__ == "__main__":
    unittest.main()
