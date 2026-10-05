from copy import deepcopy
from dataclasses import replace
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.models import FetchRecord, PropertyValue, CategoryNode
from universal_supplier.kami_matching import propose_card, merge_preserving_predecessor
from universal_supplier.beka_canonical_offline import SavedCandidateIndex


class KamiMatchingTests(unittest.TestCase):
    def card(self, fixture):
        raw = (Path(__file__).parent / 'fixtures/kami' / fixture).read_text(encoding='utf-8')
        return KamiAdapter().parse_product(FetchRecord('https://www.stanki.ru/catalog/x/item/',
            'https://www.stanki.ru/catalog/x/item/', 200, (), 1, 0, raw))

    def test_group_does_not_use_convenient_model_or_canonical_id(self):
        index = Mock()
        row = propose_card(self.card('spectr_group_from.html'), 'saved.capture', index)
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertEqual(row['sterbrust_product_id'], '')
        index.retrieve.assert_not_called()

    def test_missing_exact_model_is_not_new(self):
        index = Mock()
        index.retrieve.return_value = {'exact_full_model_ids': [], 'family_retrieval_only_ids': []}
        card = replace(self.card('ironmac_single_sale.html'), properties=())
        row = propose_card(card, 'saved.capture', index)
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertFalse(row['canonical_absence_verified'])
        self.assertIn('EXACT_FULL_MODEL_NOT_IN_PINNED_REGISTRY', row['kami_matching_evidence']['blocking_reasons'])

    def test_observed_multi_role_properties_require_reconciliation(self):
        index = Mock()
        row = propose_card(self.card('ironmac_single_sale.html'), 'saved.capture', index)
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertIn('TYPED_CHARACTERISTIC_ROLE_RECONCILIATION_REQUIRED',
                      row['kami_matching_evidence']['blocking_reasons'])
        self.assertTrue(row['typed_role_holds'])
        index.retrieve.assert_not_called()

    def test_authoritative_predecessor_is_not_recalculated_or_mutated(self):
        old = [{'source': 'optimum', 'external_id': '1', 'classification': 'EXISTING_CONFIRMED',
                'sterbrust_product_id': '199', 'operator_proof': {'keep': True}}]
        before = deepcopy(old)
        output = merge_preserving_predecessor(old, [{'source': 'kami', 'external_id': 'site:2', 'classification': 'REVIEW'}])
        self.assertEqual(output[:1], before)
        output[0]['operator_proof']['keep'] = False
        self.assertEqual(old, before)

    def test_wrong_namespace_is_rejected(self):
        with self.assertRaises(ValueError):
            merge_preserving_predecessor([], [{'source': 'optimum', 'external_id': '1'}])

    def exact_case(self):
        properties = (PropertyValue('Расстояние между центрами, мм', '400'),
                      PropertyValue('Диаметр отверстия шпинделя, мм', '21'))
        card = self.card('ironmac_single_sale.html')
        raw = {**card.raw_data, 'full_model': 'TU2304V', 'property_evidence': []}
        card = replace(card, name='Токарный станок Optimum TU2304V', brand='Optimum',
                       properties=properties, raw_data=raw,
                       categories=(CategoryNode('Токарные станки', '', 0),))
        target = {'sterbrust_product_id': '19800', 'name': card.name, 'model': 'TU2304V',
            'brand': 'Optimum', 'active': 'Y', 'source_category': 'Токарные станки',
            'observed_properties': [{'name': p.name, 'value': p.value, 'unit': 'mm'} for p in properties],
            'properties': {p.name:p.value for p in properties}}
        return card, target

    def test_exact_unique_full_model_with_two_independent_anchors(self):
        card, target = self.exact_case()
        row = propose_card(card, 'saved.capture', SavedCandidateIndex({'19800': target}))
        self.assertEqual(row['classification'], 'EXISTING_CONFIRMED')
        self.assertEqual(row['sterbrust_product_id'], '19800')

    def test_full_model_without_two_anchors_remains_review(self):
        card, target = self.exact_case()
        card = replace(card, properties=card.properties[:1])
        row = propose_card(card, 'saved.capture', SavedCandidateIndex({'19800': target}))
        self.assertEqual(row['classification'], 'REVIEW')

    def test_suffix_mismatch_and_duplicate_targets_never_auto_accept(self):
        card, target = self.exact_case()
        wrong = {**deepcopy(target), 'model': 'TU2304', 'name': 'Токарный станок Optimum TU2304'}
        row = propose_card(card, 'saved.capture', SavedCandidateIndex({'19800': wrong}))
        self.assertEqual(row['classification'], 'REVIEW')
        duplicate = {**deepcopy(target), 'sterbrust_product_id': '19801'}
        row = propose_card(card, 'saved.capture', SavedCandidateIndex({'19800': target, '19801': duplicate}))
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertEqual(row['sterbrust_product_id'], '')

    def test_three_blade_dimensions_from_one_field_are_one_anchor(self):
        card, _ = self.exact_case()
        pair = {'source': {'property_name': 'Размеры полотна, мм'},
                'canonical': {'property_name': 'Размеры полотна, мм'}}
        chosen = {'sterbrust_product_id': '123', 'name': card.name,
                  'evidence': {'typed_characteristics': {'agreements': [pair, pair, pair]}}}
        with patch('universal_supplier.kami_matching.evaluate_row', return_value={
                'confirmed': chosen, 'blocking_reasons': [], 'candidates': []}):
            row = propose_card(card, 'saved.capture', Mock())
        self.assertEqual(row['classification'], 'REVIEW')
        self.assertEqual(row['sterbrust_product_id'], '')
        self.assertIn('TWO_INDEPENDENT_OBSERVED_TYPED_FIELDS_REQUIRED',
                      row['kami_matching_evidence']['blocking_reasons'])
