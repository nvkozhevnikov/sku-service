import copy
import unittest
from universal_supplier.new_priority_cohort import select_priority


class PriorityTests(unittest.TestCase):
    def row(self):
        return dict(source='s', external_id='1', classification='REVIEW', model='LA 6-23',
                    brand='Winner', model_role='OWN_MODEL', product_kind='machine',
                    readiness_advisory={'CANONICAL_ABSENCE_SUPPORT': {'verified': True,
                        'registry_sha256': 'pinned', 'exact_candidate_ids': [], 'plausible_family_ids': []},
                        'SECTION_EVIDENCE': {'verified': True, 'mapping': {'verdict': 'SAFE'}},
                        'CHARACTERISTIC_CONTRADICTION': {'blocked': False}})

    def test_no_mutation_or_automatic_ready(self):
        r = self.row(); original = copy.deepcopy(r)
        out = select_priority([r])
        self.assertEqual(r, original)
        self.assertFalse(out['priority'][0]['identity_complete'])
        self.assertEqual(out['actual_ready_promotions'], 0)

    def test_absence_search_miss_alone_insufficient(self):
        r = self.row(); r['readiness_advisory']['CANONICAL_ABSENCE_SUPPORT']['verified'] = False
        self.assertEqual(select_priority([r])['eligible_count'], 0)

    def test_exact_candidate_blocks(self):
        r = self.row(); r['readiness_advisory']['CANONICAL_ABSENCE_SUPPORT']['exact_candidate_ids'] = ['12']
        self.assertEqual(select_priority([r])['eligible_count'], 0)

    def test_accessory_excluded(self):
        r = self.row(); r['product_kind'] = 'controller'
        self.assertEqual(select_priority([r])['eligible_count'], 0)

    def test_existing_not_reopened(self):
        r = self.row(); r['classification'] = 'EXISTING_CONFIRMED'
        self.assertEqual(select_priority([r])['eligible_count'], 0)

    def test_conflicts_excluded(self):
        r = self.row(); r['match_conflicts'] = {'execution': True}
        self.assertEqual(select_priority([r])['eligible_count'], 0)

    def test_all_namespaces_checked_and_execution_not_collapsed(self):
        a = self.row(); b = self.row(); b.update(source='optimum', external_id='2', model='LA6-23')
        c = self.row(); c.update(source='partner_st', external_id='3', model='LA6-23V')
        out = select_priority([a, b, c])
        actual = next(p for p in out['priority'] if p['source'] == 's' and p['external_id'] == '1')
        self.assertEqual(len(actual['exact_model_source_peers']), 1)
        self.assertEqual(actual['exact_model_source_peers'][0]['source'], 'optimum')

    def test_bounded_batch(self):
        rows = [dict(self.row(), external_id=str(i)) for i in range(40)]
        self.assertEqual(len(select_priority(rows)['priority']), 30)

    def test_section_not_silently_inferred(self):
        r = self.row(); r['readiness_advisory']['SECTION_EVIDENCE']['verified'] = False
        self.assertEqual(select_priority([r])['eligible_count'], 0)
        out = select_priority([r], allow_section_pending=True)
        self.assertIn('SAFE_SECTION_EVIDENCE', out['priority'][0]['missing_gates'])

    def test_series_alias_retrieval_only_keeps_suffix(self):
        from scripts.prepare_new_readiness_priority import aliases
        self.assertEqual(aliases('Laser LA 6-23'), {'laserla623', 'la623'})
        self.assertNotIn('la623', aliases('Laser LA 6-23SP'))
