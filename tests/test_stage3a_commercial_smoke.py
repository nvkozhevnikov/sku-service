"""Offline end-to-end smoke path; it deliberately has no PostgreSQL dependency."""

from datetime import datetime, timezone
from pathlib import Path
import unittest

from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.intervesp import parse_intervesp_detail
from universal_supplier.commercial import PriceState
from universal_supplier.commercial_persistence import CommercialObservationLedger, HttpCapture


FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"


def capture(url: str, suffix: str) -> HttpCapture:
    return HttpCapture(url, url, 200, "text/html; charset=utf-8", datetime(2026, 9, 24, tzinfo=timezone.utc),
                       suffix * 64, "e" * 64, f"fixture://stage3a/{suffix}", region_code="msk")


class Stage3ACommercialSmokeTests(unittest.TestCase):
    def test_four_html_cards_cross_adapters_extraction_and_persistence_contract(self):
        inputs = (
            (parse_intervesp_detail, "intervesp_bms_230dg_reconstructed_sanitized.html", "a"),
            (parse_intervesp_detail, "intervesp_bmsy_440dgh_wp2_sanitized.html", "b"),
            (parse_bekamak_detail, "bekamak_bms_230dg_sanitized.html", "c"),
            (parse_bekamak_detail, "bekamak_bmsy_440dgh_sanitized.html", "d"),
        )
        ledger = CommercialObservationLedger()
        states = {}
        for adapter, filename, digest in inputs:
            product = adapter((FIXTURES / filename).read_text(encoding="utf-8"))
            result = ledger.ingest(product, capture(product.source_url or "https://example.test/card", digest))
            self.assertTrue(result.source_product_created and result.offer_created and result.observation_created)
            states[(product.supplier.replace("-", "_"), product.site_internal_id)] = result.observation.price_state
        self.assertEqual(len(ledger.products), 4)
        self.assertEqual(len(ledger.offers), 4)
        self.assertEqual(len(ledger.observations), 4)
        self.assertEqual(states[("intervesp", "8992")], PriceState.PRICE_ON_REQUEST)
        self.assertEqual(states[("intervesp", "9028")], PriceState.NUMERIC_PUBLIC)
        self.assertEqual(states[("beka_mak", "19240")], PriceState.NUMERIC_PUBLIC)
        self.assertEqual(states[("beka_mak", "19224")], PriceState.NUMERIC_PUBLIC)
