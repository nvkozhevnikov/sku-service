import sys
import unittest
from xml.etree import ElementTree as ET

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from generate_stage5d_commercial_qa_xml import rows_from_payloads
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
