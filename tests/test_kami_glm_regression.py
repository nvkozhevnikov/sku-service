"""Independent GLM offline regression guards for the KAMI integration.

Audit branch: feature/universal-supplier-kami-glm-audit-rc1 (2026-10-05).
Every test here is offline: saved/sanitized fixtures only, no HTTP, no DB,
no mutation of production state. These tests complement the existing
test_kami_*.py suite by covering the guards it does not pin explicitly:
URL-slug identity, robots-excluded scope, supplier-namespace scoping of
persistence SQL, KAMI-path accessory blocking, cross-source fuzzy-only
evidence, and NEW-readiness of listing-only evidence.
"""
import hashlib
import json
import re
import unittest
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

from tests.test_new_group_proposals import candidate
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.beka_canonical_offline import SavedCandidateIndex
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.kami_matching import propose_card
from universal_supplier.kami_robots import KamiRobots
from universal_supplier.models import CategoryNode, FetchRecord, PropertyValue
from universal_supplier.new_group_proposals import propose_new_groups
from universal_supplier.new_readiness import propose_readiness
from universal_supplier.postgres import PostgresRepository

ROOT = Path(__file__).resolve().parents[1]
LEDGER_PATH = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/ROBOTS_SCOPE_BLOCKER.json'
CHECKPOINT_PATH = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION/CHECKPOINT.json'
ROBOTS_CAPTURE_PATH = next(
    (ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/SPECTR_VARIANT_SAMPLE').glob('d56f78af*.capture'))

DENIED_URLS = (
    'https://www.stanki.ru/catalog/tortsevaya_prisadka_s_chpu_new/'
    'avtomaticheskiy_sverlilno_prisadochnyy_tsentr_s_chpu_filato_drillmatic_sd3/',
    'https://www.stanki.ru/catalog/tortsevaya_prisadka_s_chpu_new/'
    'tortsevoy_prisadochnyy_stanok_nanxing_ncb2806d/',
)
DENIED_SITE_IDS = ('site:9967380', 'site:9527937')
ALLOWED_EXAMPLE_URL = ('https://www.stanki.ru/catalog/3_kh_koordinatnoe_proizvodstvo/'
                       'sverlilno_prisadochnyy_tsentr_s_chpu_nanxing_nsg3312dr/')

SINGLE_PRODUCT_HTML = """
<html><body>
<div class="productGrid" data-product-id="{internal}">
  <h1 class="productGrid__name">{name}</h1>
  <div class="productGrid__brand"><img alt="{brand}"></div>
  <div class="compareTable">
    <div class="thead">
      <div class="th th_large"><div class="tcell">Характеристика</div></div>
      <div class="th"><div class="tcell"><span>{model}</span></div></div>
    </div>
    <div class="tgroup">
      <div class="tcontrol">Основные</div>
      <div class="tline">
        <div class="td_large"><div class="tcell">Масса, кг</div></div>
        <div class="tw"><div class="tr"><div class="td"><div class="tcell">1200</div></div></div></div>
      </div>
    </div>
  </div>
  <div class="productCost">{cost_nodes}
    <div class="productCost__status">В наличии</div>
  </div>
</div>
</body></html>
"""


def _fetch(url, body):
    return FetchRecord(url, url, 200, (), 1, 0, body)


class KamiIdentityGuards(unittest.TestCase):
    def test_url_slug_is_never_article_model_or_site_identity(self):
        # No productGrid site id and no variant sku: the only remaining stable
        # identity is a hash of the URL itself. The slug must not leak into
        # brand/model/article, and the hashed id must not pretend to be one.
        body = SINGLE_PRODUCT_HTML.format(internal='', name='Станок G05-420',
                                          brand='SteelMac', model='G05-420', cost_nodes=(
            '<div class="productCost__price"><span data-currency="rub">1 500 000 ₽</span></div>'))
        url = 'https://www.stanki.ru/catalog/tokarnye/stanok_g05_420_promyshlennyy/'
        card = KamiAdapter().parse_product(_fetch(url, body))
        self.assertEqual(card.external_id, 'url:' + hashlib.sha256(url.encode()).hexdigest())
        self.assertTrue(card.external_id[4:].isalnum())
        self.assertEqual(card.sku, '')
        self.assertEqual(card.raw_data['supplier_article'], '')
        self.assertEqual(card.raw_data['manufacturer_article'], '')
        self.assertEqual(card.raw_data['manufacturer_state'], 'NOT_FOUND')
        self.assertEqual(card.raw_data['identity_status'], 'SOURCE_MODEL_OBSERVED')
        # Slug words never surface as model/name/article evidence.
        for slug_token in ('stanok', 'promyshlennyy', 'tokarnye'):
            self.assertNotIn(slug_token, card.name.casefold())
            self.assertNotIn(slug_token, card.raw_data['full_model'].casefold())
        self.assertEqual(card.raw_data['full_model'], 'G05-420')
        self.assertNotIn(card.external_id, (url, url.rstrip('/').rsplit('/', 1)[-1]))

    def test_old_price_alone_is_never_current_price(self):
        # A crossed-out sale price without a current price must not become the
        # offer price: price stays NULL/missing while the old value is kept as
        # raw evidence only.
        body = SINGLE_PRODUCT_HTML.format(internal='7001', name='Станок G05-420',
                                          brand='SteelMac', model='G05-420', cost_nodes=(
            '<div class="productCost__price productCost__price_old">'
            '<span data-currency="rub">9 190 015 ₽</span></div>'))
        card = KamiAdapter().parse_product(
            _fetch('https://www.stanki.ru/catalog/tokarnye/g05_420/', body))
        self.assertEqual(card.external_id, 'site:7001')
        self.assertIsNone(card.price)
        self.assertEqual(card.currency, '')
        self.assertEqual(card.raw_data['price_state'], 'missing')
        self.assertEqual(card.raw_data['old_price_raw'], '9 190 015 ₽')
        self.assertEqual(card.old_price, Decimal('9190015'))
        self.assertEqual(card.raw_data['price_basis'], 'exact_price')


class KamiRobotsScopeGuards(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture_text = ROBOTS_CAPTURE_PATH.read_text(encoding='utf-8')
        cls.ledger = json.loads(LEDGER_PATH.read_text(encoding='utf-8'))

    def test_saved_robots_capture_denies_exactly_the_blocked_scope(self):
        # The saved robots.txt capture is the ledger-recorded bytes...
        digest = hashlib.sha256(ROBOTS_CAPTURE_PATH.read_bytes()).hexdigest()
        self.assertEqual(digest, self.ledger['robots_sha256'])
        # ...and its rules deny both blocked URLs while a normal catalog item
        # stays fetchable.
        robots = KamiRobots(self.capture_text)
        for url in DENIED_URLS:
            self.assertFalse(robots.can_fetch(url), url)
        self.assertTrue(robots.can_fetch(ALLOWED_EXAMPLE_URL))

    def test_blocker_ledger_contract_and_checkpoint_absence(self):
        self.assertEqual(self.ledger['status'], 'BLOCKED_SOURCE_ROBOTS_DISCOVERY_INCOMPLETE')
        self.assertFalse(self.ledger['collector_running'])
        self.assertTrue(self.ledger['no_HTTP_by_this_audit'])
        self.assertTrue(self.ledger['SQL_read_only'])
        self.assertEqual(len(self.ledger['blocked_URLs']), 2)
        blocked_urls = set()
        for entry in self.ledger['blocked_URLs']:
            blocked_urls.add(entry['url'])
            self.assertFalse(entry['detail_capture_exists'])
            self.assertTrue(entry['no_GET_sent_to_denied_URL'])
            self.assertTrue(entry['saved_listing_evidence'])
            self.assertEqual(entry['identity_state'], 'LISTING_EVIDENCE_ONLY_NOT_FULL_CARD')
        self.assertEqual(blocked_urls, set(DENIED_URLS))
        # New operator contract permits terminal exclusion, not product proof.
        state = json.loads(CHECKPOINT_PATH.read_text(encoding='utf-8'))
        self.assertEqual(state['sql_writes'], 0)
        cards = state['cards']
        for site_id in DENIED_SITE_IDS:
            self.assertNotIn(site_id, cards)
        for url in DENIED_URLS:
            decision = state['completed'].get(url)
            if decision is not None:
                self.assertEqual(decision['route'], 'ROBOTS_EXCLUDED')
                self.assertFalse(decision['downstream_actionable'])
                self.assertNotIn('external_id', decision)

    def test_listing_only_robots_evidence_cannot_become_new(self):
        # Even if someone hand-forges rows from listing-only evidence, both the
        # grouping layer and NEW readiness fail closed.
        rows = [{'source': 'kami', 'external_id': 'site:9967380',
                 'new_candidate_id': '', 'classification': 'ROBOTS_LISTING_EVIDENCE_ONLY',
                 'brand': '', 'model': '', 'execution': '',
                 'name': 'Фрезерный центр с ЧПУ Filato Drillmatic SD3 (листинг)',
                 'product_kind': 'machine', 'model_role': 'OWN_MODEL',
                 'source_url': DENIED_URLS[0],
                 'evidence_ref': 'ledger://robots_blocked/site:9967380',
                 'properties': {}},
                {'source': 'kami', 'external_id': 'site:9527937',
                 'new_candidate_id': '', 'classification': 'ROBOTS_LISTING_EVIDENCE_ONLY',
                 'brand': '', 'model': '', 'execution': '',
                 'name': 'Торцовой присадочный станок Nanxing NCB2806D (листинг)',
                 'product_kind': 'machine', 'model_role': 'OWN_MODEL',
                 'source_url': DENIED_URLS[1],
                 'evidence_ref': 'ledger://robots_blocked/site:9527937',
                 'properties': {}}]
        result = propose_new_groups(deepcopy(rows), allowed_sources={'kami'})
        self.assertFalse(result['groups'])
        self.assertEqual(len(result['review']), 2)
        for row in result['review']:
            self.assertEqual(row['grouping_status'], 'REVIEW')
            self.assertIn('not_new_candidate', row['grouping_reasons'])
        passthrough = propose_readiness({'classification': 'ROBOTS_LISTING_EVIDENCE_ONLY',
                                          'new_candidate_id': ''}, {},
                                         dedup_verified=False, group_id=None)
        self.assertEqual(passthrough['state'], 'ROBOTS_LISTING_EVIDENCE_ONLY')
        self.assertFalse(passthrough['input_mutated'])
        review = propose_readiness({'classification': 'REVIEW', 'new_candidate_id': 'x'}, {},
                                   dedup_verified=True, group_id='NEWG-X')
        self.assertFalse(review['identity_ready'])
        self.assertIn('REVIEW_OR_SEMANTIC_CONTRADICTION', review['identity_blockers'])


class KamiMatchingPathGuards(unittest.TestCase):
    def card(self, fixture):
        raw = (Path(__file__).parent / 'fixtures/kami' / fixture).read_text(encoding='utf-8')
        return KamiAdapter().parse_product(FetchRecord('https://www.stanki.ru/catalog/x/item/',
                                                       'https://www.stanki.ru/catalog/x/item/',
                                                       200, (), 1, 0, raw))

    def test_accessory_kind_card_cannot_confirm_machine_execution(self):
        # Exact full-model match plus two independent anchors must still not
        # confirm when the KAMI card is an accessory of the machine, not the
        # machine itself (controller for ETM-16U manipulator).
        properties = (PropertyValue('Расстояние между центрами, мм', '400'),
                      PropertyValue('Диаметр отверстия шпинделя, мм', '21'))
        card = self.card('ironmac_single_sale.html')
        raw = {**card.raw_data, 'full_model': 'ETM-16U', 'property_evidence': []}
        card = replace(card, name='Контроллер для электрического манипулятора Partner ETM-16U',
                       brand='Partner', properties=properties, raw_data=raw,
                       categories=(CategoryNode('Манипуляторы', '', 0),))
        target = {'sterbrust_product_id': '18350',
                  'name': 'Электрический манипулятор Partner ETM-16U',
                  'model': 'ETM-16U', 'brand': 'Partner', 'active': 'Y',
                  'source_category': 'Манипуляторы',
                  'observed_properties': [{'name': p.name, 'value': p.value, 'unit': 'mm'}
                                          for p in properties],
                  'properties': {p.name: p.value for p in properties}}
        row = propose_card(card, 'saved.capture', SavedCandidateIndex({'18350': target}))
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertEqual(row['sterbrust_product_id'], '')

    def test_kami_cross_source_fuzzy_only_evidence_never_groups(self):
        # Same brand+model across beka_mak and kami with no shared article and
        # no shared identity anchors must stay in review, never a NEW group.
        rows = [candidate('beka_mak', '19240'),
                candidate('kami', 'site:77', model='BMS-230DG', execution='BMS-230DG',
                          brand='Beka-Mak', name='Ленточнопильный станок Beka-Mak BMS-230DG',
                          properties={'voltage': '380 V'})]
        result = propose_new_groups(rows, allowed_sources={'beka_mak', 'kami'})
        self.assertFalse(result['groups'])
        reasons = {row['source']: row['grouping_reasons'] for row in result['review']}
        self.assertIn('shared_equivalence_evidence_missing', reasons['kami'])
        self.assertIn('independent_equivalence_evidence_missing', reasons['kami'])


class RecordingCursor:
    """Offline SQL recorder: never talks to PostgreSQL."""

    def __init__(self):
        self.statements = []
        self._last = ''
        self._next_id = 10

    def execute(self, sql, params=()):
        self.statements.append((sql, params))
        self._last = sql

    def fetchone(self):
        lowered = self._last.casefold()
        if 'from suppliers' in lowered:
            return (5,)
        if 'returning id' in lowered:
            self._next_id += 1
            return (self._next_id,)
        return None


class KamiPersistenceNamespaceGuards(unittest.TestCase):
    def test_persistence_sql_is_supplier_scoped_and_passive(self):
        raw = (Path(__file__).parent / 'fixtures/kami' / 'ironmac_single_sale.html').read_text(encoding='utf-8')
        card = KamiAdapter().parse_product(FetchRecord('https://www.stanki.ru/catalog/x/y/',
                                                       'https://www.stanki.ru/catalog/x/y/',
                                                       200, (), 1, 0, raw))
        capture = HttpCapture(card.canonical_url, card.canonical_url, 200, 'text/html',
                              datetime(2026, 10, 5, tzinfo=timezone.utc),
                              'a' * 64, 'b' * 64, 'fixture://kami/detail')
        repo = object.__new__(PostgresRepository)
        repo.connection = MagicMock()
        cursor = RecordingCursor()
        repo.connection.cursor.return_value.__enter__.return_value = cursor
        repo._replace_details = MagicMock()
        repo.persist_kami_observation(card, capture)
        # Advisory locking is namespaced by supplier before any row access.
        locks = [params for sql, params in cursor.statements if 'pg_advisory_xact_lock' in sql]
        self.assertEqual(locks, [(f'commercial:5:{card.external_id}',)])
        product_statements = [(sql, params) for sql, params in cursor.statements
                              if 'source_products' in sql]
        self.assertTrue(product_statements)
        for sql, _ in product_statements:
            first_word = sql.lstrip().upper().split(None, 1)[0]
            if first_word == 'SELECT':
                self.assertIn('supplier_id=%s AND external_id=%s', sql)
            elif first_word == 'UPDATE':
                self.assertRegex(sql, r'WHERE id=%s AND supplier_id=%s')
            elif first_word == 'INSERT':
                self.assertIn('(supplier_id,external_id', sql)
        # external_id is never used as a global cross-supplier key.
        for sql, _ in cursor.statements:
            if 'external_id=%s' in sql:
                self.assertIn('supplier_id', sql)
        # Passive commercial suppliers never activate their offers.
        offer_inserts = [sql for sql, _ in cursor.statements
                         if sql.lstrip().upper().startswith('INSERT INTO OFFERS')]
        self.assertTrue(offer_inserts)
        for sql in offer_inserts:
            self.assertIn('false,0)', sql)  # active=false, missed_crawls=0


if __name__ == '__main__':
    unittest.main()
