from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.adapters.vekprom import parse_vekprom_detail


FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"


class VekpromAdapterTests(unittest.TestCase):
    def parse(self, name):
        return parse_vekprom_detail((FIXTURES / name).read_text(encoding="utf-8"))

    def test_numeric_price_page_uses_element_id_and_props_article(self):
        product = self.parse("vekprom_bmsy_440dgh_sanitized.html")
        self.assertEqual(product.supplier, "vekprom")
        self.assertEqual(product.site_internal_id, "6704")
        self.assertEqual(product.manufacturer_article, "BMSY 440 DGH")
        self.assertEqual(product.supplier_model, "BMSY 440 DGH")
        self.assertEqual(product.name, "BMSY 440 DGH Полуавтоматический ленточнопильный станок")
        self.assertEqual(product.availability, "InStock")
        self.assertEqual(product.source_brand, "Beka-Mak")
        self.assertIn(("Артикул", "BMSY 440 DGH"), product.technical_properties)
        self.assertIn(("Производитель", "Beka-Mak"), product.technical_properties)
        self.assertTrue(product.source_url.startswith(
            "https://vekprom.ru/catalog/lentochnopilnye-stanki-po-metallu/"))

    def test_numeric_price_is_public_microdata_cross_checked_with_visible_dom(self):
        product = self.parse("vekprom_bmsy_440dgh_sanitized.html")
        self.assertEqual(product.price.current_price, Decimal("2840637"))
        self.assertEqual(product.price.currency, "RUB")
        self.assertEqual(product.price.state.value, "numeric_public")
        # The page shows both the numeric matrix price and a «Узнать цену» CTA;
        # the numeric value wins but the marker stays on the record.
        self.assertIn("request_marker_coexists_with_numeric_price", product.price.diagnostics)

    def test_slider_prices_after_product_block_are_never_read(self):
        source = (FIXTURES / "vekprom_bmsy_440dgh_sanitized.html").read_text(encoding="utf-8")
        product = parse_vekprom_detail(source)
        first_card = source.find('class="card-item')
        self.assertGreater(first_card, -1)
        product_with_slider = parse_vekprom_detail(
            source[:first_card] + '<div class="card-item"><span itemprop="price" content="1"></span></div>'
            + source[first_card:])
        self.assertEqual(product_with_slider.price.current_price, Decimal("2840637"))
        self.assertEqual(product_with_slider.site_internal_id, product.site_internal_id)

    def test_on_request_page_is_price_on_request_not_zero(self):
        product = self.parse("vekprom_bmso_825xs_sanitized.html")
        self.assertEqual(product.site_internal_id, "6619")
        self.assertEqual(product.manufacturer_article, "BMSO 825 XS")
        self.assertIsNone(product.price.current_price)
        self.assertEqual(product.price.state.value, "price_on_request")
        self.assertIn("request_marker", product.price.diagnostics)
        self.assertEqual(product.availability, "OutOfStock")

    def test_hidden_discontinued_banner_is_not_a_discontinued_product(self):
        product = self.parse("vekprom_bmsy_440dgh_sanitized.html")
        self.assertNotIn("discontinued_marker_visible", product.diagnostics)
        self.assertEqual(product.availability, "InStock")

    def test_visible_discontinued_banner_overrides_microdata_stock(self):
        source = (FIXTURES / "vekprom_bmsy_440dgh_sanitized.html").read_text(encoding="utf-8")
        visible_banner = source.replace(
            'class="product__outof" style=\'display:none\';>', 'class="product__outof">', 1)
        self.assertNotEqual(visible_banner, source)
        product = parse_vekprom_detail(visible_banner)
        self.assertIn("discontinued_marker_visible", product.diagnostics)
        self.assertEqual(product.availability, "OutOfStock")

    def test_missing_element_id_is_reported_not_invented(self):
        source = (FIXTURES / "vekprom_bmsy_440dgh_sanitized.html").read_text(encoding="utf-8")
        stripped = source.replace("name='ELEMENT_ID' value='6704'", "", 1)
        self.assertNotEqual(stripped, source)
        product = parse_vekprom_detail(stripped)
        self.assertIsNone(product.site_internal_id)
        self.assertIn("element_id_missing_from_detail", product.diagnostics)

    def test_site_internal_id_is_explicitly_not_an_article(self):
        product = self.parse("vekprom_bmsy_440dgh_sanitized.html")
        self.assertNotEqual(product.site_internal_id, product.manufacturer_article)
        self.assertIn("site_internal_id_is_not_manufacturer_article", product.diagnostics)


if __name__ == "__main__":
    unittest.main()
