import unittest

from universal_supplier.adapters.bekamak_manufacturer import parse_bekamak_manufacturer_reference


class BekaMakManufacturerReferenceTests(unittest.TestCase):
    def test_offline_reference_parser_keeps_options_separate(self):
        source = """
        <h1>BMSY-440DGH</h1>
        <p class="product-type">Double sided miter semi automatic bandsaw machine</p>
        <table><tr><th>Blade Speed</th><td>15 - 120 m/min.</td></tr>
        <tr><th>Blade Dimensions</th><td>5200x34x1,1 mm</td></tr></table>
        <h3>Standard Features</h3><ul><li>Hydraulic Vise</li></ul>
        <h3>Optional Features</h3><ul><li>Hydraulic Top Clamping</li></ul>
        """
        card = parse_bekamak_manufacturer_reference(source, source_url="https://www.bekamak.com/urun/bmsy-440dgh/en")
        self.assertEqual(card.manufacturer, "Bekamak")
        self.assertEqual(card.model, "BMSY-440DGH")
        self.assertEqual([(item.name, item.value) for item in card.properties], [
            ("Blade Speed", "15 - 120 m/min."), ("Blade Dimensions", "5200x34x1,1 mm"),
        ])
        self.assertEqual(card.standard_features, ("Hydraulic Vise",))
        self.assertEqual(card.optional_features, ("Hydraulic Top Clamping",))
        self.assertIn("not_a_supplier_adapter", card.diagnostics)

    def test_empty_or_unproven_page_fails_closed(self):
        card = parse_bekamak_manufacturer_reference("<h1>BMS-230DG</h1>")
        self.assertEqual(card.properties, ())
        self.assertIn("technical_table_not_found_or_unproven_structure", card.diagnostics)
