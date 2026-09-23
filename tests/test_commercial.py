from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.commercial import PriceSourceKind, PriceState, extract_public_price, parse_rub_money


FIXTURES = Path(__file__).with_name("fixtures") / "commercial"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


class PriceExtractionTests(unittest.TestCase):
    def extract(self, source: str, identity: str = "CURRENT-1"):
        return extract_public_price(source, product_identity=identity, source_url="https://example.test/current")

    def test_real_intervesp_transcript_preserves_current_and_old_price(self):
        result = extract_public_price(fixture("intervesp_bmsy_440dgh_wp2_price.html"), product_identity="BMSY-440DGH-WP2")
        self.assertEqual((result.state, result.current_price, result.old_price),
                         (PriceState.NUMERIC_PUBLIC, Decimal("2347301"), Decimal("2470843")))
        self.assertEqual(result.source_kind, PriceSourceKind.PRODUCT_PRICE_DOM)

    def test_real_bekamak_transcript_parses_public_backorder_price(self):
        result = extract_public_price(fixture("bekamak_bms_230dg_price.html"), product_identity="BMS-230DG")
        self.assertEqual((result.state, result.current_price, result.currency),
                         (PriceState.NUMERIC_PUBLIC, Decimal("757197"), "RUB"))

    def test_real_intervesp_request_transcript_is_not_zero(self):
        result = extract_public_price(fixture("intervesp_bms_230dg_request.html"), product_identity="BMS-230DG")
        self.assertEqual((result.state, result.current_price), (PriceState.PRICE_ON_REQUEST, None))

    def test_single_price_has_context_and_provenance(self):
        result = self.extract('<main data-product-identity="CURRENT-1"><span class="price-current">1 250 000 ₽</span></main>')
        self.assertEqual(result.current_price, Decimal("1250000"))
        self.assertEqual(result.context_path, "explicit_product_context")

    def test_missing_price_block(self):
        self.assertEqual(self.extract('<main data-product-identity="CURRENT-1"><h1>Machine</h1></main>').state, PriceState.MISSING)

    def test_zero_is_invalid(self):
        self.assertEqual(self.extract('<main data-product-identity="CURRENT-1"><span class="price-current">0 ₽</span></main>').state, PriceState.ZERO_INVALID)

    def test_invalid_price_node_is_parse_error(self):
        self.assertEqual(self.extract('<main data-product-identity="CURRENT-1"><span class="price-current">уточняйте ₽</span></main>').state, PriceState.PARSE_ERROR)

    def test_microdata_price_inside_scope(self):
        source = '<main data-product-identity="CURRENT-1"><meta itemprop="price" content="1234567"><meta itemprop="priceCurrency" content="RUB"></main>'
        result = self.extract(source)
        self.assertEqual((result.current_price, result.source_kind), (Decimal("1234567"), PriceSourceKind.MICRODATA))

    def test_json_ld_price_requires_identity(self):
        source = '<script type="application/ld+json">{"@type":"Product","sku":"CURRENT-1","offers":{"price":"1234567.50","priceCurrency":"RUB"}}</script>'
        result = self.extract(source)
        self.assertEqual((result.current_price, result.source_kind), (Decimal("1234567.50"), PriceSourceKind.JSON_LD))

    def test_json_ld_execution_suffix_is_not_a_substring_match(self):
        source = '<script type="application/ld+json">{"@type":"Product","sku":"BMS-230DG-WP2","offers":{"price":"500000","priceCurrency":"RUB"}}</script>'
        result = self.extract(source, "BMS-230DG")
        self.assertIsNone(result.current_price)
        self.assertNotEqual(result.state, PriceState.NUMERIC_PUBLIC)
        self.assertIn("json_ld_product_identity_mismatch", result.diagnostics)

    def test_json_ld_base_model_is_not_evidence_for_requested_execution(self):
        source = '<script type="application/ld+json">{"@type":"Product","sku":"BMS 230 DG","offers":{"price":"500000","priceCurrency":"RUB"}}</script>'
        result = self.extract(source, "BMS-230DG-WP2")
        self.assertIsNone(result.current_price)
        self.assertNotEqual(result.state, PriceState.NUMERIC_PUBLIC)

    def test_json_ld_graph_uses_only_exact_product_identity(self):
        source = '''<script type="application/ld+json">{"@graph":[
          {"@type":"Product","sku":"BMS-230DG-WP2","offers":{"price":"500000","priceCurrency":"RUB"}},
          {"@type":"Product","sku":"BMS 230 DG","offers":{"price":"400000","priceCurrency":"RUB"}}
        ]}</script>'''
        result = self.extract(source, "BMS-230DG")
        self.assertEqual((result.state, result.current_price), (PriceState.NUMERIC_PUBLIC, Decimal("400000")))

    def test_dom_rub_and_microdata_usd_are_ambiguous(self):
        source = '<main data-product-identity="CURRENT-1"><span class="price-current">1 000 ₽</span><meta itemprop="price" content="1000"><meta itemprop="priceCurrency" content="USD"></main>'
        result = self.extract(source)
        self.assertEqual(result.state, PriceState.AMBIGUOUS)
        self.assertIn("currency_conflict:microdata=USD", result.diagnostics)

    def test_unparseable_dom_and_numeric_microdata_are_ambiguous(self):
        source = '<main data-product-identity="CURRENT-1"><span class="price-current">уточняйте ₽</span><meta itemprop="price" content="1000"><meta itemprop="priceCurrency" content="RUB"></main>'
        result = self.extract(source)
        self.assertEqual(result.state, PriceState.AMBIGUOUS)
        self.assertIn("unparseable_current_source:product_price_dom", result.diagnostics)

    def test_conflicting_dom_and_json_ld_requires_review(self):
        source = '<main data-product-identity="CURRENT-1"><span class="price-current">1 000 ₽</span></main><script type="application/ld+json">{"@type":"Product","sku":"CURRENT-1","offers":{"price":"2000","priceCurrency":"RUB"}}</script>'
        self.assertEqual(self.extract(source).state, PriceState.AMBIGUOUS)

    def test_equal_dom_and_json_ld_prices_are_corroborated(self):
        source = '<main data-product-identity="CURRENT-1"><span class="price-current">1 000 ₽</span></main><script type="application/ld+json">{"@type":"Product","sku":"CURRENT-1","offers":{"price":"1000","priceCurrency":"RUB"}}</script>'
        result = self.extract(source)
        self.assertEqual((result.state, result.current_price), (PriceState.NUMERIC_PUBLIC, Decimal("1000")))
        self.assertIn("corroborated_current_price_sources", result.diagnostics)

    def test_neighbor_json_ld_is_ignored_with_diagnostic(self):
        source = '<main data-product-identity="CURRENT-1"><span class="price-current">1 000 ₽</span></main><script type="application/ld+json">{"@type":"Product","sku":"NEIGHBOR-1","offers":{"price":"2000","priceCurrency":"RUB"}}</script>'
        result = self.extract(source)
        self.assertEqual((result.state, result.current_price), (PriceState.NUMERIC_PUBLIC, Decimal("1000")))
        self.assertIn("json_ld_product_identity_mismatch", result.diagnostics)

    def test_lease_numbers_are_not_price(self):
        source = '<main data-product-identity="CURRENT-1"><div class="lease">Цена в лизинг от 35 210 ₽/мес.</div><input value="1000000"></main>'
        self.assertEqual(self.extract(source).state, PriceState.MISSING)

    def test_recommended_product_price_is_not_price(self):
        source = '<main data-product-identity="CURRENT-1"><h1>Machine</h1></main><aside data-product-identity="OTHER"><span class="price-current">999 999 ₽</span></aside>'
        self.assertEqual(self.extract(source).state, PriceState.MISSING)

    def test_technical_numbers_are_not_price(self):
        source = '<main data-product-identity="CURRENT-1"><table><tr><td>Мощность</td><td>3 кВт</td></tr><tr><td>Скорость</td><td>20-100 м/мин</td></tr></table></main>'
        self.assertEqual(self.extract(source).state, PriceState.MISSING)

    def test_ruble_separators(self):
        for raw, expected in (("1 234 567 ₽", "1234567"), ("1\u00a0234\u00a0567 руб.", "1234567"),
                              ("1.234.567,89 RUB", "1234567.89"), ("1,234,567.50 ₽", "1234567.50")):
            with self.subTest(raw=raw):
                self.assertEqual(parse_rub_money(raw), Decimal(expected))

    def test_old_price_never_substitutes_current(self):
        result = self.extract('<main data-product-identity="CURRENT-1"><del class="old-price">2 000 ₽</del></main>')
        self.assertEqual((result.state, result.current_price), (PriceState.MISSING, None))


if __name__ == "__main__":
    unittest.main()
