from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.adapters.bekamak import parse_bekamak_detail


FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"


class BekaMakAdapterTests(unittest.TestCase):
    def parse(self, name):
        return parse_bekamak_detail((FIXTURES / name).read_text(encoding="utf-8"))

    def test_bms_230dg_uses_main_detail_price_and_article(self):
        product = self.parse("bekamak_bms_230dg_sanitized.html")
        self.assertEqual(product.price.current_price, Decimal("772191"))
        self.assertEqual(product.manufacturer_article, "BMS 230 DG")
        self.assertEqual(product.site_internal_id, "19240")
        self.assertEqual(product.availability, "InStock")

    def test_base_model_is_not_wp2_and_uses_its_own_price(self):
        product = self.parse("bekamak_bmsy_440dgh_sanitized.html")
        self.assertEqual(product.manufacturer_article, "BMSY 440 DGH")
        self.assertNotIn("WP2", product.supplier_model)
        self.assertEqual(product.price.current_price, Decimal("2840637"))
        self.assertEqual(product.site_internal_id, "19224")

    def test_site_internal_id_is_explicitly_not_an_article(self):
        product = self.parse("bekamak_bmsy_440dgh_sanitized.html")
        self.assertNotEqual(product.site_internal_id, product.manufacturer_article)
        self.assertIn("site_internal_id_is_not_manufacturer_article", product.diagnostics)
        self.assertEqual(product.price.source_path, ".prices_block .price[data-currency=RUB][data-value]")

    def test_price_in_similar_products_cannot_replace_detail_price(self):
        source = (FIXTURES / "bekamak_bms_230dg_sanitized.html").read_text(encoding="utf-8")
        source = source.replace("Похожее оборудование", 'Похожее оборудование <div class="price" data-currency="RUB" data-value="1">1 ₽</div>', 1)
        product = parse_bekamak_detail(source)
        self.assertEqual(product.price.current_price, Decimal("772191"))
