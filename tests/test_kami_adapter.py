import hashlib
import json
from pathlib import Path
import unittest
from universal_supplier.adapters.kami import KamiAdapter, canonical_source_url, parse_listing
from universal_supplier.models import FetchRecord
from universal_supplier.repository import InMemoryRepository

FIX = Path(__file__).parent / 'fixtures/kami'
BASE = 'https://www.stanki.ru/catalog/test/item/'


def parse(source, url=BASE):
    return KamiAdapter().parse_product(FetchRecord(url, url, 200, (), 1, 0, source))


class KamiAdapterTests(unittest.TestCase):
    def setUp(self):
        self.single = (FIX / 'ironmac_single_sale.html').read_text(encoding='utf-8')
        self.group = (FIX / 'spectr_group_from.html').read_text(encoding='utf-8')

    def test_fixture_integrity(self):
        for entry in json.loads((FIX / 'manifest.json').read_text(encoding='utf-8')):
            self.assertEqual(hashlib.sha256((FIX / entry['fixture']).read_bytes()).hexdigest(), entry['fixture_sha256'])

    def test_identity_brand_article_roles(self):
        card = parse(self.single)
        self.assertEqual(card.external_id, 'site:9807314')
        self.assertEqual(card.brand, 'IRONMAC')
        self.assertEqual(card.manufacturer, '')
        self.assertEqual(card.sku, 'МС 441507')
        self.assertEqual(card.raw_data['manufacturer_article'], '')
        self.assertEqual(card.raw_data['full_model'], 'ITX-508MY')

    def test_sale_and_alternative_currencies(self):
        card = parse(self.single)
        self.assertEqual(str(card.price), '7093124')
        self.assertEqual(str(card.old_price), '9190015')
        self.assertEqual(card.currency, 'RUB')
        self.assertEqual(card.price_type, 'unknown')
        self.assertEqual(card.raw_data['price_basis'], 'exact_price')

    def test_from_price_group_never_exact_execution(self):
        card = parse(self.group)
        self.assertEqual(card.raw_data['price_basis'], 'from_price')
        self.assertEqual(card.raw_data['card_kind'], 'MULTI_VARIANT_WITH_EXPLICIT_IDENTITIES')
        self.assertEqual(card.raw_data['full_model'], '')
        self.assertEqual(card.properties, ())
        self.assertEqual(len(card.raw_data['variant_evidence']), 4)

    def test_variant_column_values_are_not_collapsed(self):
        card = parse(self.group)
        variants = card.raw_data['variant_evidence']
        self.assertEqual(variants[0]['model'], 'G5 - 42Y стандарт')
        self.assertEqual(variants[1]['supplier_article'], '')
        self.assertEqual(variants[2]['supplier_article'], 'МС 490691')
        self.assertTrue(variants[0]['characteristics'])

    def test_stock_not_quantity_content_unknown_not_not_found(self):
        card = parse(self.single)
        self.assertEqual(card.availability_normalized, 'in_stock')
        self.assertIsNone(card.quantity)
        self.assertEqual(card.raw_data['description_state'], 'UNKNOWN')
        self.assertTrue(card.media)

    def test_offline_replay_no_duplicate_or_changed_facts(self):
        cards = [parse(self.single), parse(self.group)]
        repo = InMemoryRepository()
        self.assertEqual(repo.ingest(cards, '2026-10-05T08:00:00Z').new_products, 2)
        replay = repo.ingest(cards, '2026-10-05T08:00:00Z')
        self.assertEqual(replay.new_products, 0)
        self.assertEqual(replay.changed_products, 0)
        self.assertEqual(replay.changed_offers, 0)
        self.assertEqual(len(repo.state.products), 2)

    def test_por_null_and_missing_not_zero(self):
        card = parse('<div class="productGrid" data-product-id="1"><h1 class="productGrid__name">Станок</h1><div class="productCost__price">Цена по запросу</div></div>')
        self.assertIsNone(card.price)
        self.assertEqual(card.raw_data['price_state'], 'price_on_request')
        self.assertEqual(card.raw_data['card_kind'], 'AMBIGUOUS')

    def test_document_links_are_observed_without_download_or_related_links(self):
        raw = '''<div class="productGrid" data-product-id="1"><h1 class="productGrid__name">Станок</h1>
        <div class="productTabs__content"><a href="/files/manual.pdf">Инструкция</a>
        <a href="/files/manual.pdf">Повтор</a><a href="javascript:test.pdf">Bad</a></div></div>
        <a href="/files/another.pdf">Related</a>'''
        card = parse(raw)
        self.assertEqual([m.url for m in card.media if m.media_type == 'document'],
                         ['https://www.stanki.ru/files/manual.pdf'])
        self.assertEqual(card.raw_data['documents_state'], 'OBSERVED')
        self.assertIn('not_download_verified', card.raw_data['documents_provenance'])

    def test_lazy_content_prevents_asserting_documents_absent(self):
        card = parse(self.single)
        self.assertEqual(card.raw_data['documents_state'], 'UNKNOWN')

    def test_same_label_in_different_sections_preserves_both_roles(self):
        html = '''<div class="productGrid" data-product-id="10">
        <h1 class="productGrid__name">Machine X1</h1><div class="compareTable">
        <div class="thead"><div class="th"><div class="tcell"><span>X1</span></div></div></div>
        <div class="tcontrol"><button>Main spindle</button></div><div class="tgroup">
        <div class="tline"><div class="td_large"><div class="tcell">Power, kW</div></div>
        <div class="tw"><div class="tr"><div class="td"><div class="tcell">15</div></div></div></div></div></div>
        <div class="tcontrol"><button>Tool turret</button></div><div class="tgroup">
        <div class="tline"><div class="td_large"><div class="tcell">Power, kW</div></div>
        <div class="tw"><div class="tr"><div class="td"><div class="tcell">3.7</div></div></div></div></div></div>
        </div></div>'''
        card = parse(html)
        self.assertEqual([p.value for p in card.properties], ['15', '3.7'])
        self.assertEqual([p['role'] for p in card.raw_data['property_evidence']],
                         ['Main spindle', 'Tool turret'])
        self.assertEqual([p['position'] for p in card.raw_data['property_evidence']], [0, 1])

    def test_observed_por_and_incoming_cards(self):
        card = parse((FIX / 'observed_price_on_request.html').read_text(encoding='utf-8'))
        self.assertIsNone(card.price)
        self.assertEqual(card.raw_data['price_state'], 'price_on_request')
        self.assertEqual(card.brand, 'QUADRO')
        incoming = parse((FIX / 'observed_incoming.html').read_text(encoding='utf-8'))
        self.assertEqual(incoming.availability_normalized, 'incoming')
        self.assertIsNone(incoming.quantity)
        self.assertEqual(incoming.raw_data['full_model'], 'IMV-10.55')

    def test_recommendation_prices_excluded(self):
        card = parse(self.single + '<div class="productCard"><span data-currency="rub">1 ₽</span></div>')
        self.assertEqual(str(card.price), '7093124')

    def test_foreign_canonical_rejected(self):
        with self.assertRaises(ValueError):
            canonical_source_url('https://evil.example/catalog/item/')
        with self.assertRaises(ValueError):
            canonical_source_url('https://www.stanki.ru/catalog/item/?token=x')

    def test_listing_is_not_full_discovery(self):
        self.assertEqual(parse_listing('<div class="productCard" data-product-id="42"><a class="productCard__name" href="/catalog/a/item/">IRONMAC X</a></div>', BASE)[0]['site_internal_id'], '42')
        with self.assertRaisesRegex(RuntimeError, 'FULL_DISCOVERY_MANIFEST_REQUIRED'):
            KamiAdapter().discover(None)

    def test_non_product_and_non_200_fail_closed(self):
        with self.assertRaises(ValueError):
            parse('<h1>Category</h1>')
        with self.assertRaises(ValueError):
            KamiAdapter().parse_product(FetchRecord(BASE, BASE, 404, (), 1, 0, self.single))

    def test_shared_parent_id_does_not_collapse_variants(self):
        def variant(sku):
            return '<div class="productGrid" data-product-id="42"><h1 class="productGrid__name">Variant</h1><div class="productTabs" data-sku="' + sku + '"></div></div>'
        a, b = parse(variant('101')), parse(variant('102'))
        self.assertEqual(a.external_id, 'variant:101')
        self.assertEqual(b.external_id, 'variant:102')
        self.assertEqual(a.raw_data['card_kind'], 'AMBIGUOUS')
        self.assertEqual(a.raw_data['manufacturer_article'], '')

    def test_conflicting_variant_ids_fail_closed(self):
        with self.assertRaisesRegex(ValueError, 'VARIANT_INTERNAL_ID_AMBIGUOUS'):
            parse('<div class="productGrid" data-product-id="42"><h1 class="productGrid__name">Variant</h1><div class="productTabs" data-sku="101"></div><div class="productTabs" data-sku="102"></div></div>')

    def test_observed_variant_span_headers_preserve_execution_and_column(self):
        cards = []
        for filename, suffix, key, model in (
            ('spectr_variant_standard.html', 'g5_42y_standart_2/', 'variant:9833201', 'G5 - 42Y стандарт'),
            ('spectr_variant_counterspindle.html', 'g5_42ys_protivoshpindel_2/', 'variant:9833203', 'G5 - 42YS + противошпиндель'),
        ):
            url = 'https://www.stanki.ru/catalog/tokarnye_avtomaty_s_chpu/tokarno_frezernyy_tsentr_s_chpu_spectr_model_g5_42y/' + suffix
            card = parse((FIX / filename).read_text(encoding='utf-8'), url)
            self.assertEqual(card.external_id, key)
            self.assertEqual(card.raw_data['full_model'], model)
            self.assertEqual(card.raw_data['card_kind'], 'SINGLE_PRODUCT')
            self.assertEqual(card.canonical_url, url)
            self.assertTrue(card.properties)
            self.assertNotIn('МС', card.raw_data['full_model'])
            cards.append(card)
        self.assertNotEqual(cards[0].external_id, cards[1].external_id)
        self.assertNotEqual(cards[0].raw_data['full_model'], cards[1].raw_data['full_model'])

    def test_visible_title_disagreement_keeps_selected_variant_ambiguous(self):
        html = (FIX / 'spectr_variant_standard.html').read_text(encoding='utf-8')
        html = html.replace('SPECTR G5 - 42Y стандарт', 'SPECTR G5 - 42YS + противошпиндель', 1)
        card = parse(html)
        self.assertEqual(card.raw_data['card_kind'], 'AMBIGUOUS')
        self.assertEqual(card.raw_data['full_model'], '')
