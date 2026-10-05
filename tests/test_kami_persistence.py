from datetime import datetime, timezone
import unittest
from unittest.mock import MagicMock
from pathlib import Path
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.models import FetchRecord
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.kami_persistence import prepare_observation
from universal_supplier.postgres import SUPPLIER_CONFIGS, PASSIVE_COMMERCIAL_SUPPLIERS, PostgresRepository


class KamiPersistenceTests(unittest.TestCase):
    def parse(self, fixture):
        source = (Path(__file__).parent / 'fixtures/kami' / fixture).read_text(encoding='utf-8')
        card = KamiAdapter().parse_product(FetchRecord('https://www.stanki.ru/catalog/x/y/', 'https://www.stanki.ru/catalog/x/y/', 200, (), 1, 0, source))
        capture = HttpCapture(card.canonical_url, card.canonical_url, 200, 'text/html', datetime(2026, 10, 5, tzinfo=timezone.utc), 'a'*64, 'b'*64, 'fixture://kami/detail')
        return card, capture

    def test_namespace_disabled_by_default(self):
        self.assertIn('kami', SUPPLIER_CONFIGS)
        self.assertIn('kami', PASSIVE_COMMERCIAL_SUPPLIERS)

    def test_exact_price_business_role_unknown(self):
        card, capture = self.parse('ironmac_single_sale.html')
        projection, observation = prepare_observation(card, capture)
        self.assertEqual(observation.price_type, 'unknown')
        self.assertEqual(observation.extraction_evidence['price_basis'], 'exact_price')
        self.assertEqual(projection.raw_data['source_identity']['raw_model'], 'ITX-508MY')
        self.assertFalse(projection.raw_data['source_identity']['manufacturer_article_explicit'])

    def test_group_from_price_excluded_from_exact_comparison(self):
        card, capture = self.parse('spectr_group_from.html')
        _, observation = prepare_observation(card, capture)
        self.assertIn('FROM_PRICE_NOT_AN_EXACT_EXECUTION_PRICE', observation.unusable_price_reasons)
        self.assertIn('GROUP_PRICE_NOT_AN_EXACT_EXECUTION_PRICE', observation.unusable_price_reasons)

    def test_wrong_capture_url_fails_closed(self):
        card, capture = self.parse('ironmac_single_sale.html')
        from dataclasses import replace
        with self.assertRaises(ValueError):
            prepare_observation(card, replace(capture, final_url='https://www.stanki.ru/catalog/another/item/'))

    def test_forged_identity_fails_closed(self):
        card, capture = self.parse('ironmac_single_sale.html')
        from dataclasses import replace
        with self.assertRaises(ValueError):
            prepare_observation(replace(card, external_id='made-up'), capture)

    def test_fact_hashes_do_not_treat_new_capture_as_changed_product(self):
        from dataclasses import replace
        from datetime import timedelta
        card, capture = self.parse('ironmac_single_sale.html')
        first, _ = prepare_observation(card, capture)
        newer, _ = prepare_observation(card, replace(capture, observed_at=capture.observed_at + timedelta(days=1), response_sha256='c'*64))
        for key in ('_kami_content_fact_hash', '_kami_commercial_fact_hash'):
            self.assertEqual(first.raw_data[key], newer.raw_data[key])
        changed, _ = prepare_observation(replace(card, price=card.price + 1), capture)
        self.assertEqual(first.raw_data['_kami_content_fact_hash'], changed.raw_data['_kami_content_fact_hash'])
        self.assertNotEqual(first.raw_data['_kami_commercial_fact_hash'], changed.raw_data['_kami_commercial_fact_hash'])

    def materializer(self, prior, capture, linked=None):
        repo = object.__new__(PostgresRepository)
        repo.connection = MagicMock()
        cursor = repo.connection.cursor.return_value.__enter__.return_value
        cursor.fetchone.side_effect = [(7, prior, linked, 8), (6,)]
        repo._replace_details = MagicMock()
        return repo, cursor

    def test_materialization_replay_is_noop_without_any_write(self):
        card, capture = self.parse('ironmac_single_sale.html')
        prepared, _ = prepare_observation(card, capture)
        repo, cursor = self.materializer(prepared.raw_data, capture)
        self.assertFalse(repo.materialize_kami_saved_content(card, capture))
        repo._replace_details.assert_not_called()
        self.assertTrue(all(call.args[0].lstrip().startswith('SELECT') for call in cursor.execute.call_args_list))

    def test_materialization_does_not_fabricate_capture_or_change_commercial_time(self):
        card, capture = self.parse('ironmac_single_sale.html')
        prepared, _ = prepare_observation(card, capture)
        prior = {**prepared.raw_data, '_kami_content_fact_hash': 'old-extraction'}
        repo, cursor = self.materializer(prior, capture)
        self.assertTrue(repo.materialize_kami_saved_content(card, capture))
        statements = [call.args[0] for call in cursor.execute.call_args_list]
        self.assertFalse(any('INSERT' in s or 'UPDATE offers' in s or 'last_changed_at' in s for s in statements))
        repo._replace_details.assert_called_once()

    def test_materialization_refuses_canonical_link_or_newer_capture(self):
        card, capture = self.parse('ironmac_single_sale.html')
        prepared, _ = prepare_observation(card, capture)
        repo, _ = self.materializer(prepared.raw_data, capture, linked=123)
        with self.assertRaisesRegex(RuntimeError, 'unlinked existing KAMI'):
            repo.materialize_kami_saved_content(card, capture)
        repo._replace_details.assert_not_called()
        prior = {**prepared.raw_data, 'commercial_capture': {'response_sha256': 'newer'}}
        repo, _ = self.materializer(prior, capture)
        with self.assertRaisesRegex(RuntimeError, 'historical capture'):
            repo.materialize_kami_saved_content(card, capture)
        repo._replace_details.assert_not_called()
