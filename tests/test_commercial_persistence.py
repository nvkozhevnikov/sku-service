from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import unittest

from universal_supplier.adapters.bekamak import parse_bekamak_detail
from universal_supplier.adapters.intervesp import parse_intervesp_detail
from universal_supplier.commercial import PriceState
from universal_supplier.commercial_persistence import (
    CommercialObservationLedger, HttpCapture, observation_from_snapshot,
    product_card_from_snapshot,
)
from universal_supplier.offer_selection import OfferCandidate, OfferSelectionPolicy, evaluate_offers
from universal_supplier.postgres import PASSIVE_COMMERCIAL_SUPPLIERS


FIXTURES = Path(__file__).with_name("fixtures") / "commercial" / "real"
ROOT = Path(__file__).resolve().parents[1]
SHA_A = "a" * 64
SHA_B = "b" * 64


def capture(url: str, digest: str = SHA_A, *, region: str | None = "msk", hour: int = 0) -> HttpCapture:
    return HttpCapture(url, url, 200, "text/html; charset=utf-8", datetime(2026, 9, 23, hour, tzinfo=timezone.utc),
                       digest, SHA_B, "fixture://sanitized", region_code=region, region_label="Москва")


class CommercialPersistenceTests(unittest.TestCase):
    def intervesp(self, name: str):
        return parse_intervesp_detail((FIXTURES / name).read_text(encoding="utf-8"))

    def beka(self, name: str):
        return parse_bekamak_detail((FIXTURES / name).read_text(encoding="utf-8"))

    def test_two_suppliers_keep_independent_external_ids(self):
        ledger = CommercialObservationLedger()
        intervesp = self.intervesp("intervesp_bms_230dg_reconstructed_sanitized.html")
        beka = self.beka("bekamak_bms_230dg_sanitized.html")
        ledger.ingest(intervesp, capture(intervesp.source_url or "https://intervesp.ru/product/8992", SHA_A))
        ledger.ingest(beka, capture(beka.source_url or "https://beka-mak.su/product/19240", "c" * 64))
        self.assertEqual(set(ledger.products), {("intervesp", "8992"), ("beka_mak", "19240")})

    def test_same_capture_is_idempotent(self):
        ledger = CommercialObservationLedger()
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        first = ledger.ingest(product, capture(product.source_url or "https://beka-mak.su/product/19240"))
        second = ledger.ingest(product, capture(product.source_url or "https://beka-mak.su/product/19240"))
        self.assertTrue(first.source_product_created and first.offer_created and first.observation_created)
        self.assertFalse(second.source_product_created or second.offer_created or second.observation_created)
        self.assertEqual(len(ledger.observations), 1)

    def test_changed_price_creates_history_not_a_second_product_or_offer(self):
        ledger = CommercialObservationLedger()
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        url = product.source_url or "https://beka-mak.su/product/19240"
        ledger.ingest(product, capture(url, SHA_A))
        changed_html = (FIXTURES / "bekamak_bms_230dg_sanitized.html").read_text(encoding="utf-8").replace('data-value="772191"', 'data-value="772192"', 1)
        changed = parse_bekamak_detail(changed_html)
        result = ledger.ingest(changed, capture(url, "c" * 64))
        self.assertFalse(result.source_product_created or result.offer_created)
        self.assertTrue(result.observation_created)
        self.assertEqual(len(ledger.products), 1)
        self.assertEqual(len(ledger.offers), 1)
        self.assertEqual(len(ledger.observations), 2)
        self.assertEqual(result.product.price, Decimal("772192"))

    def test_price_on_request_is_null_not_zero_and_unknown_stock_stays_unknown(self):
        product = self.intervesp("intervesp_bms_230dg_reconstructed_sanitized.html")
        card = product_card_from_snapshot(product, capture(product.source_url or "https://intervesp.ru/product/8992"))
        observation = observation_from_snapshot(product, capture(product.source_url or "https://intervesp.ru/product/8992"))
        self.assertEqual(product.price.state, PriceState.PRICE_ON_REQUEST)
        self.assertIsNone(card.price)
        self.assertEqual(card.availability_normalized, "unknown")
        self.assertEqual(observation.unusable_price_reasons, ("request_marker",))

    def test_request_replaces_previous_current_offer_price(self):
        ledger = CommercialObservationLedger()
        numeric = self.beka("bekamak_bms_230dg_sanitized.html")
        requested = self.intervesp("intervesp_bms_230dg_reconstructed_sanitized.html")
        # Use one source key to prove the state transition itself cannot retain
        # a prior numeric value in the current default-offer representation.
        requested = requested.__class__(**{**requested.__dict__, "supplier": "beka_mak", "site_internal_id": "19240"})
        url = numeric.source_url or "https://beka-mak.su/product/19240"
        ledger.ingest(numeric, capture(url, SHA_A))
        result = ledger.ingest(requested, capture(url, "c" * 64, hour=1))
        self.assertIsNone(result.product.price)
        self.assertEqual(result.observation.price_state, PriceState.PRICE_ON_REQUEST)

    def test_later_capture_with_same_response_is_historical_but_exact_repeat_is_not(self):
        ledger = CommercialObservationLedger()
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        url = product.source_url or "https://beka-mak.su/product/19240"
        first = capture(url, SHA_A, hour=0)
        later = capture(url, SHA_A, hour=1)
        ledger.ingest(product, first)
        self.assertFalse(ledger.ingest(product, first).observation_created)
        self.assertTrue(ledger.ingest(product, later).observation_created)
        self.assertEqual(len(ledger.observations), 2)

    def test_region_change_is_separate_capture_history(self):
        ledger = CommercialObservationLedger()
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        url = product.source_url or "https://beka-mak.su/product/19240"
        ledger.ingest(product, capture(url, SHA_A, region="msk"))
        result = ledger.ingest(product, capture(url, SHA_A, region="spb"))
        self.assertTrue(result.observation_created)
        self.assertEqual(result.observation.region_code, "spb")

    def test_old_or_other_region_capture_does_not_replace_current_projection(self):
        ledger = CommercialObservationLedger()
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        url = product.source_url or "https://beka-mak.su/product/19240"
        ledger.ingest(product, capture(url, SHA_A, region="msk", hour=2))
        changed_html = (FIXTURES / "bekamak_bms_230dg_sanitized.html").read_text(encoding="utf-8").replace('data-value="772191"', 'data-value="1"', 1)
        changed = parse_bekamak_detail(changed_html)
        ledger.ingest(changed, capture(url, "c" * 64, region="msk", hour=1))
        ledger.ingest(changed, capture(url, "d" * 64, region="spb", hour=3))
        self.assertEqual(ledger.offers[("beka_mak", "19240", "default")].price, Decimal("772191"))
        self.assertEqual(len(ledger.observations), 3)

    def test_wp2_and_base_execution_remain_two_source_products(self):
        ledger = CommercialObservationLedger()
        wp2 = self.intervesp("intervesp_bmsy_440dgh_wp2_sanitized.html")
        base = self.beka("bekamak_bmsy_440dgh_sanitized.html")
        ledger.ingest(wp2, capture(wp2.source_url or "https://intervesp.ru/product/9028", SHA_A))
        ledger.ingest(base, capture(base.source_url or "https://beka-mak.su/product/19224", "c" * 64))
        self.assertEqual(set(ledger.products), {("intervesp", "9028"), ("beka_mak", "19224")})
        self.assertNotEqual(wp2.supplier_model, base.supplier_model)

    def test_card_has_no_identity_or_sterbrust_selling_price_side_effect(self):
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        card = product_card_from_snapshot(product, capture(product.source_url or "https://beka-mak.su/product/19240"))
        self.assertNotIn("catalog_product_id", card.raw_data)
        self.assertNotIn("sterbrust", card.raw_data)
        self.assertEqual(card.sku, "BMS 230 DG")
        self.assertNotEqual(card.sku, card.external_id)
        self.assertEqual(card.raw_data["source_identity"]["raw_model"], "BMS 230 DG")
        self.assertNotEqual(card.raw_data["source_identity"]["normalized_model"], "BMS 230 DG")

    def test_bekamak_title_model_without_explicit_article_is_not_invented_article(self):
        html = ('<link rel="canonical" href="https://beka-mak.su/product/bmso_270_dgs_nc/">'
                '<h1 id="pagetitle">Автоматический станок Beka-Mak BMSO-270DGS NC</h1>'
                '<script>setViewedProduct(19283, {"PRODUCT_ID":"19283"});</script>')
        product = parse_bekamak_detail(html)
        self.assertEqual(product.site_internal_id, "19283")
        self.assertEqual(product.supplier_model, "BMSO-270DGS NC")
        self.assertIsNone(product.manufacturer_article)
        self.assertIn("manufacturer_article_missing_from_detail", product.diagnostics)
        card = product_card_from_snapshot(product, capture(product.source_url))
        self.assertEqual(card.sku, "")
        self.assertEqual(card.raw_data["source_identity"]["raw_model"], "BMSO-270DGS NC")

    def test_capture_rejects_session_query_parameter_and_legacy_unknown_status(self):
        with self.assertRaises(ValueError):
            capture("https://example.test/card?sessid=secret")
        product = self.beka("bekamak_bms_230dg_sanitized.html")
        with self.assertRaises(ValueError):
            HttpCapture("https://example.test/card", "https://example.test/card", None, None,
                        datetime(2026, 9, 23, tzinfo=timezone.utc), SHA_A, SHA_B, "fixture://legacy")

    def test_capture_requires_html_200_and_timezone(self):
        arguments = ("https://example.test/card", "https://example.test/card", 200, "text/html",
                     datetime(2026, 9, 23, tzinfo=timezone.utc), SHA_A, SHA_B, "fixture://safe")
        with self.assertRaises(ValueError):
            HttpCapture(*arguments[:2], 404, *arguments[3:])
        with self.assertRaises(ValueError):
            HttpCapture(*arguments[:3], "application/pdf", *arguments[4:])
        with self.assertRaises(ValueError):
            HttpCapture(*arguments[:4], datetime(2026, 9, 23), *arguments[5:])

    def test_capture_rejects_userinfo_fragment_and_any_query(self):
        for url in ("https://user:secret@example.test/card", "https://example.test/card#token",
                    "https://example.test/card?api_key=secret", "https://example.test/card?utm_source=x"):
            with self.assertRaises(ValueError):
                capture(url)

    def test_capture_rejects_session_data_in_evidence_or_diagnostics(self):
        with self.assertRaises(ValueError):
            HttpCapture("https://example.test/card", "https://example.test/card", 200, None,
                        datetime(2026, 9, 23, tzinfo=timezone.utc), SHA_A, SHA_B,
                        "archive?token=secret")
        with self.assertRaises(ValueError):
            HttpCapture("https://example.test/card", "https://example.test/card", 200, None,
                        datetime(2026, 9, 23, tzinfo=timezone.utc), SHA_A, SHA_B,
                        "fixture://safe", diagnostics=("cookie=secret",))

    def test_passive_supplier_and_offer_cannot_enter_v1_selection(self):
        self.assertTrue({"intervesp", "beka_mak"} <= PASSIVE_COMMERCIAL_SUPPLIERS)
        policy = OfferSelectionPolicy.load("config/offer_selection.json")
        candidate = OfferCandidate(
            catalog_product_id=1, offer_id=1, source_product_id=1, supplier_id=1, offer_supplier_id=1,
            supplier_code="intervesp", supplier_enabled=False, source_catalog_product_id=1,
            source_active=True, source_missed_crawls=0, source_last_success_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
            offer_active=False, offer_kind="default", offer_missed_crawls=0,
            offer_last_success_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
            latest_successful_full_crawl_at=datetime(2026, 9, 23, tzinfo=timezone.utc),
            price=None, currency=None, availability="unknown", availability_raw=None, quantity=None,
        )
        decision = evaluate_offers(1, [candidate], policy)
        self.assertEqual((decision.selection_status, decision.selected_offer_id), ("no_eligible_offer", None))
        self.assertIn("supplier_disabled", decision.evidence["candidate_offers"][0]["exclusion_reasons"])

    def test_migration_is_append_only_and_quarantines_public_prices(self):
        migration = (ROOT / "migrations" / "015_commercial_capture_observations.sql").read_text(encoding="utf-8")
        self.assertIn("capture_fingerprint", migration)
        self.assertIn("price_type = 'unknown'", migration)
        self.assertIn("price_state <> 'numeric_public' AND price IS NULL", migration)
        self.assertNotIn("CREATE TABLE product_matches", migration)
        self.assertNotIn("CREATE TABLE catalog_products", migration)
        self.assertNotIn("ON DELETE CASCADE", migration)
        self.assertGreaterEqual(migration.count("ON DELETE RESTRICT"), 8)

    def test_passive_supplier_upsert_does_not_mutate_an_existing_namespace(self):
        source = (ROOT / "universal_supplier" / "postgres.py").read_text(encoding="utf-8")
        start = source.index("def _ensure_passive_commercial_supplier")
        end = source.index("def persist_commercial_observation", start)
        helper = source[start:end]
        self.assertIn("ON CONFLICT (code) DO NOTHING", helper)
        self.assertIn("SELECT id FROM suppliers WHERE code=%s FOR UPDATE", helper)
        self.assertNotIn("DO UPDATE SET enabled=false, updated_at=now()", helper)
