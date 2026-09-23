from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.adapters.intervesp import parse_intervesp_detail
from universal_supplier.commercial import PriceState


FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"


class IntervespAdapterTests(unittest.TestCase):
    def parse(self, name):
        return parse_intervesp_detail((FIXTURES / name).read_text(encoding="utf-8"))

    def test_request_price_is_not_zero(self):
        product = self.parse("intervesp_bms_230dg_reconstructed_sanitized.html")
        self.assertEqual(product.price.state, PriceState.PRICE_ON_REQUEST)
        self.assertIsNone(product.price.current_price)
        self.assertEqual(product.price.source_path, "#elPrice")

    def test_wp2_uses_main_price_not_leasing_payment(self):
        product = self.parse("intervesp_bmsy_440dgh_wp2_sanitized.html")
        self.assertEqual(product.supplier_model, "BMSY-440DGH-WP2")
        self.assertEqual(product.price.current_price, Decimal("2888086"))
        self.assertNotEqual(product.price.current_price, Decimal("43321"))
        self.assertEqual(product.price.source_path, "#elPrice [itemprop=price]")
        self.assertEqual(product.availability, "InStock")

    def test_no_manufacturer_article_is_invented(self):
        product = self.parse("intervesp_bmsy_440dgh_wp2_sanitized.html")
        self.assertIsNone(product.manufacturer_article)
        self.assertIn("manufacturer_article_not_explicit", product.diagnostics[0])

