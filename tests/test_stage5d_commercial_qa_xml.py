import sys
import unittest
from xml.etree import ElementTree as ET

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_stage5d_commercial_qa_xml import rows_from_database_records, rows_from_payloads
from universal_supplier.xml_export import supplier_xml_bytes


class Stage5DCommercialQaXmlTests(unittest.TestCase):
    def test_only_persisted_non_review_rows_are_rendered_without_invented_identity(self):
        payload = {"rows": [
            {"site": "intervesp", "result": "PERSISTED", "external_id": "8992", "requested_url": "https://example.test/a", "parsed_model": "BMS-230DG", "price_state": "price_on_request", "price": None, "currency": None, "availability": None},
            {"site": "beka_mak", "result": "PERSISTED", "external_id": "19240", "requested_url": "https://example.test/b", "parsed_model": "BMS-230DG", "price_state": "numeric_public", "price": "772191", "currency": "RUB", "availability": "InStock"},
            {"site": "beka_mak", "result": "REVIEW", "external_id": "19219", "requested_url": "https://example.test/c", "parsed_model": "BMSY-360DGH-ECO"},
        ]}
        rows = rows_from_payloads([payload])
        root = ET.fromstring(supplier_xml_bytes(rows))
        products = root.findall(".//product")
        self.assertEqual([node.get("external_id") for node in products], ["19240", "8992"])
        self.assertEqual(products[0].findtext(".//sterbrust_product_id"), None)
        self.assertEqual(products[1].findtext(".//price"), "")
        self.assertEqual(products[1].findtext(".//availability_normalized"), "unknown")

    def test_database_projection_keeps_non_numeric_price_states_null_and_identity_blank(self):
        rows = rows_from_database_records([
            {"source_product_id": 1, "external_id": "8992", "supplier_code": "intervesp",
             "source_url": "https://example.test/request", "name": "BMS-230DG", "sku": "BMS-230DG",
             "price_source": "price_on_request", "price": "700000", "old_price": None, "currency": "RUB",
             "availability_normalized": "unknown"},
            {"source_product_id": 2, "external_id": "19240", "supplier_code": "beka_mak",
             "source_url": "https://example.test/numeric", "name": "BMS-230DG", "sku": "BMS-230DG",
             "price_source": "numeric_public", "price": "772191", "old_price": None, "currency": "RUB",
             "availability_normalized": "in_stock"},
            {"source_product_id": 3, "external_id": "19241", "supplier_code": "beka_mak",
             "source_url": "https://example.test/missing", "name": "BMS-270DG", "sku": "BMS-270DG",
             "price_source": "missing", "price": "0", "old_price": None, "currency": "RUB",
             "availability_normalized": "unknown"},
        ])
        root = ET.fromstring(supplier_xml_bytes(rows))
        products = {node.get("external_id"): node for node in root.findall(".//product")}
        self.assertEqual(products["8992"].findtext(".//price"), "")
        self.assertEqual(products["19241"].findtext(".//price"), "")
        self.assertEqual(products["19241"].findtext(".//currency"), "")
        self.assertEqual(products["19240"].findtext(".//price"), "772191")
        self.assertEqual(products["19240"].findtext(".//currency"), "RUB")
        self.assertIsNone(products["19240"].findtext(".//sterbrust_product_id"))
