from copy import deepcopy
import unittest
from universal_supplier.kami_overlap import equivalence, cross_supplier_overlaps


class KamiOverlapTests(unittest.TestCase):
    def row(self, source='kami', model='TU2304V'):
        props = {'Расстояние между центрами, мм': '400', 'Диаметр отверстия шпинделя, мм': '21'}
        return {'source': source, 'external_id': 'site:1' if source == 'kami' else '1',
            'brand': 'Optimum', 'model': model, 'name': 'Токарный станок Optimum ' + model,
            'model_role': 'OWN_MODEL', 'source_category': 'Токарные станки', 'properties': props,
            'observed_properties': [{'name': k, 'value': v, 'unit': 'mm'} for k,v in props.items()],
            'classification': 'REVIEW', 'evidence_ref': 'saved.capture'}

    def test_strict_positive_equivalence_does_not_assign_canonical_id(self):
        row, peer = self.row(), self.row('optimum')
        peer.update(classification='EXISTING_CONFIRMED', sterbrust_product_id='19800')
        old = deepcopy(peer)
        result = cross_supplier_overlaps([row], [peer])
        self.assertEqual(result[0]['proof']['status'], 'PROVEN_SOURCE_EQUIVALENCE')
        self.assertFalse(result[0]['proof']['canonical_assignment_performed'])
        self.assertNotIn('sterbrust_product_id', row)
        self.assertEqual(peer, old)

    def test_model_only_is_not_equivalence(self):
        a, b = self.row(), self.row('optimum')
        a.update(properties={}, observed_properties=[])
        self.assertEqual(equivalence(a,b)['status'], 'RETRIEVAL_ONLY_REVIEW')

    def test_suffix_mismatch_is_not_retrieved_or_accepted(self):
        a, b = self.row(), self.row('optimum', 'TU2304')
        self.assertEqual(cross_supplier_overlaps([a], [b]), [])
        self.assertIn('BRAND_FULL_EXECUTION_NOT_EXACT', equivalence(a,b)['blocking_reasons'])

    def test_conflict_and_role_holds_are_not_erased_by_shared_model(self):
        a, b = self.row(), self.row('optimum')
        for changed in ({'classification': 'CONFLICT'}, {'typed_role_holds': ['Мощность']}):
            with self.subTest(changed=changed):
                self.assertIn('SOURCE_BLOCKER_RETAINED', equivalence({**a, **changed}, b)['blocking_reasons'])

    def test_used_condition_remains_a_guard(self):
        a, b = self.row(), self.row('optimum')
        a['name'] += ' Б/У'
        self.assertIn('CONDITION_EXECUTION_GUARD_BLOCKED', equivalence(a,b)['blocking_reasons'])
