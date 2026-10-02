from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import unittest

from universal_supplier.intervesp_listing import (product_url, url_identity, identity_index, resolve_identity,
    listing_card, listing_snapshot, persistence_identity, bounded_rate_plan, retain_detail_content)
from universal_supplier.commercial_persistence import HttpCapture, CommercialObservationLedger, product_card_from_snapshot
from universal_supplier.proposed_matching import _card

URL = 'https://intervesp.ru/catalog/test/stanok-lx20-pro/'


def row():
    return {'product_url': URL, 'title': 'Станок JET LX20 Pro', 'price_state': 'numeric',
            'price_text': '900 000 ₽', 'price_old_text': 'UNKNOWN', 'availability': 'В наличии',
            'evidence_sha256': 'a' * 64, 'brand': 'JET', 'model_candidate': 'LX20 Pro',
            'category': 'Станки', 'short_specs': 'Мощность, кВт: 0,9',
            'listing_pages': 'https://intervesp.ru/catalog/test/?PAGEN_2=2'}


def capture(scope='intervesp_listing'):
    return HttpCapture('https://intervesp.ru/catalog/test/?PAGEN_2=2',
        'https://intervesp.ru/catalog/test/?PAGEN_2=2', 200, 'text/html',
        datetime(2026, 10, 1, tzinfo=timezone.utc), 'a' * 64, 'a' * 64, 'listing://a',
        diagnostics=('intervesp_listing_evidence',), capture_scope=scope)


class IdentityTests(unittest.TestCase):
    def test_deterministic_url_id_not_site_or_factory_article(self):
        self.assertEqual(url_identity(URL), url_identity(URL.replace('https://', 'http://www.').rstrip('/')))
        source = row(); card = listing_card(source)
        self.assertTrue(card.external_id.startswith('url-sha256:'))
        self.assertEqual(card.sku, '')
        self.assertIsNone(card.raw_data['source_identity']['site_internal_id'])
        self.assertEqual(source, row())

    def test_80_legacy_ids_preserved_without_pk_copy(self):
        records = [{'external_id': str(i), 'source_url': URL.replace('lx20-pro', f'bms{i}'),
                    'source_product_id': i + 100, 'source': 'intervesp'} for i in range(80)]
        index = identity_index(records)
        self.assertEqual(len(index), 80)
        for record in records:
            self.assertEqual(resolve_identity(record['source_url'], index), record['external_id'])

    def test_duplicates_and_namespace_do_not_merge(self):
        with self.assertRaises(ValueError):
            identity_index([{'external_id': '1', 'source_url': URL}, {'external_id': '2', 'source_url': URL}])
        self.assertEqual(identity_index([{'source': 'beka_mak', 'external_id': '1', 'source_url': URL}]), {})

    def test_unsafe_urls_rejected(self):
        for url in ('https://evil.invalid/catalog/a/b/', URL + '?id=1', URL + '#one',
                    'https://user:pass@intervesp.ru/catalog/a/b/',
                    'https://intervesp.ru/catalog/a/%2e%2e/', 'https://intervesp.ru/catalog/a/'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                product_url(url)

    def test_future_detail_write_reuses_url_identity_and_collision_fails(self):
        class Cursor:
            def __init__(self, rows): self.rows = rows
            def execute(self, query, args): self.args = args
            def fetchall(self): return self.rows
        card = listing_card(row())
        self.assertEqual(persistence_identity(Cursor([('8992', URL, URL)]), 5, card), '8992')
        self.assertEqual(persistence_identity(Cursor([]), 5, card), card.external_id)
        for rows in ([('1', URL, URL), ('2', URL, URL)], [('1', URL.replace('lx20-pro', 'lx20-new'), None)]):
            with self.assertRaises(ValueError):
                persistence_identity(Cursor(rows), 5, card)


class EvidenceTests(unittest.TestCase):
    def test_saved_archive_digest_is_explicit_not_raw_http(self):
        archived = replace(capture(), response_hash_basis='sanitized_archive')
        card = product_card_from_snapshot(listing_snapshot(row()), archived)
        self.assertEqual(card.raw_data['commercial_capture']['response_hash_basis'], 'sanitized_archive')
        self.assertEqual(card.raw_data['listing_evidence'], row())
        with self.assertRaises(ValueError): replace(archived, response_sha256='b' * 64)
        with self.assertRaises(ValueError): replace(archived, capture_scope='detail')

    def test_listing_does_not_erase_known_detail_or_site_identity(self):
        previous = {'source_content': {'description_text': 'real description', 'source_images': ['https://intervesp.ru/photo.jpg']},
                    'source_identity': {'site_internal_id': '8992'}, 'commercial_capture': {'evidence_ref': 'detail://original'}}
        fresh = {'source_content': {'description_text': '', 'source_images': [], 'source_category': 'new category'},
                 'source_identity': {'site_internal_id': '', 'model_status': 'CANDIDATE'}}
        result = retain_detail_content(fresh, previous)
        self.assertEqual(result['source_content']['description_text'], 'real description')
        self.assertEqual(result['source_identity']['site_internal_id'], '8992')
        self.assertEqual(result['previous_detail_capture'], previous['commercial_capture'])
        self.assertEqual(previous['source_content']['description_text'], 'real description')

    def test_incoming_is_not_lost_in_listing_projection(self):
        item = row(); item['availability'] = 'В пути'
        self.assertEqual(product_card_from_snapshot(listing_snapshot(item), capture()).availability_normalized, 'incoming')

    def test_prices_presence_and_unknown_are_separate(self):
        numeric = listing_card(row()); self.assertEqual(str(numeric.price), '900000')
        self.assertEqual(numeric.availability_normalized, 'in_stock')
        for state in ('price_on_request', 'UNKNOWN', 'missing'):
            record = row(); record.update(price_state=state, price_text='UNKNOWN', availability='UNKNOWN')
            card = listing_card(record)
            self.assertIsNone(card.price)
            self.assertEqual(card.availability_normalized, 'unknown')
        for value in ('0 ₽', 'UNKNOWN', '20 EUR'):
            record = row(); record['price_text'] = value
            with self.assertRaises(ValueError): listing_card(record)

    def test_listing_metadata_is_not_fabricated_product_get(self):
        product = listing_snapshot(row()); cap = capture()
        card = product_card_from_snapshot(product, cap)
        self.assertEqual(card.canonical_url, URL)
        self.assertEqual(card.requested_url, URL)
        self.assertEqual(card.raw_data['commercial_capture']['requested_url'], cap.requested_url)
        self.assertEqual(card.raw_data['source_identity']['site_internal_id'], '')
        self.assertEqual(card.brand, 'JET')
        self.assertFalse(card.raw_data['source_identity']['manufacturer_article_explicit'])

    def test_listing_pagination_only_explicit_scope_not_general_query_relaxation(self):
        with self.assertRaises(ValueError): capture('detail')
        for url in ('https://intervesp.ru/catalog/test/?token=abc',
                    'https://intervesp.ru/catalog/test/?PAGEN_2=2&PAGEN_2=3',
                    'https://other.invalid/catalog/test/?PAGEN_2=2'):
            cap = capture()
            from dataclasses import replace
            with self.assertRaises(ValueError): replace(cap, requested_url=url)

    def test_offline_exact_replay_full_noop(self):
        ledger = CommercialObservationLedger()
        product = listing_snapshot(row()); cap = capture()
        self.assertTrue(ledger.ingest(product, cap).observation_created)
        replay = ledger.ingest(product, cap)
        self.assertFalse(replay.source_product_created)
        self.assertFalse(replay.offer_created)
        self.assertFalse(replay.observation_created)
        self.assertEqual((len(ledger.products), len(ledger.offers), len(ledger.observations)), (1, 1, 1))

    def test_generic_brand_is_preserved_for_matching_not_assumed_bekamak(self):
        card = product_card_from_snapshot(listing_snapshot(row()), capture())
        record = {'supplier_code': 'intervesp', 'external_id': card.external_id, 'raw_data': card.raw_data,
                  'source_url': URL, 'name': card.name}
        self.assertEqual(_card(record).brand, 'JET')
        record['raw_data']['source_identity']['brand'] = ''
        self.assertEqual(_card(record).brand, '')

    def test_faster_rate_cannot_bypass_robots(self):
        plan = bounded_rate_plan(20)
        self.assertEqual(plan['faster_seconds'], 20)
        self.assertFalse(plan['faster_distinct_permitted'])
        self.assertEqual(plan['parallel_requests'], 1)
        with self.assertRaises(ValueError): bounded_rate_plan(-1)


if __name__ == '__main__': unittest.main()
