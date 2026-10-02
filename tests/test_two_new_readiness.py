import copy
import json
from pathlib import Path
import unittest
from universal_supplier.two_new_readiness import anchors, verify_anchor_proof, readiness_gate, deterministic_group_id, plain, family_hits, apply_ready_proposal
from universal_supplier.new_readiness import propose_readiness


class TwoReadinessTests(unittest.TestCase):
    def test_iframe_markup_not_brand_evidence(self):
        self.assertEqual(plain('&lt;iframe frameborder="0"&gt;&lt;/iframe&gt;'), '')

    def test_search_does_not_merge_words(self):
        self.assertFalse(family_hits('инструмент зубчатого вида frameborder', ['нтз', 'mebor']))
        self.assertEqual(family_hits('HTZ-800 RS', ['htz']), ['htz'])
        self.assertEqual(family_hits('ÜSTÜNKARLI UYYM-120', ['ustunkarli', 'uyym']), ['ustunkarli', 'uyym'])

    def test_roles_not_collapsed(self):
        result = anchors([('Ширина шкивов, мм', '80', ''), ('Ширина ленточной пилы, мм', '100', '')])
        self.assertEqual(result, {'pulley_width': {80.0}, 'blade_width': {100.0}})

    def test_units_unknown_not_assumed_mm(self):
        self.assertFalse(anchors([('Диаметр шкивов', '800', '')]))

    def test_visible_contradiction_blocks(self):
        actual = anchors([('Диаметр шкивов, мм', '800', ''), ('Диаметр шкивов, мм', '1200', ''),
                          ('Ширина ленточной пилы, мм', '100', '')])
        case = {'anchors': [{'role': 'pulley_diameter', 'numeric_mm': 800}]}
        self.assertTrue(verify_anchor_proof(case, actual))

    def test_one_anchor_insufficient(self):
        self.assertIn('INDEPENDENT_TYPED_ANCHORS_MISSING', verify_anchor_proof({'anchors': []}, {'blade_length': {5300}}))

    def test_missing_section_blocks(self):
        gates = dict.fromkeys(['SOURCE_IDENTITY', 'INDEPENDENT_TYPED_IDENTITY', 'CROSS_SOURCE_DEDUP', 'CANONICAL_ABSENCE'], 'PASS')
        self.assertFalse(readiness_gate(gates))

    def test_any_guard_failure_blocks(self):
        gates = dict.fromkeys(['SOURCE_IDENTITY', 'INDEPENDENT_TYPED_IDENTITY', 'CROSS_SOURCE_DEDUP', 'CANONICAL_ABSENCE', 'SAFE_SECTION'], 'PASS')
        self.assertTrue(readiness_gate(gates))
        for k in gates:
            bad = dict(gates, **{k: 'FAIL'})
            self.assertFalse(readiness_gate(bad))

    def test_group_deterministic_and_execution_preserved(self):
        a = deterministic_group_id('MEBOR', 'HTZ-800 RS')
        self.assertEqual(a, deterministic_group_id('Mebor', 'HTZ 800 RS'))
        self.assertNotEqual(a, deterministic_group_id('MEBOR', 'HTZ-800'))
        self.assertNotEqual(a, deterministic_group_id('ÜSTÜNKARLI', 'UYYM-120'))

    def test_identity_not_full_and_no_sterbrust_id(self):
        row = {'classification': 'NEW_CANDIDATE', 'new_candidate_id': 'NEW-source'}
        advisory = {'CHARACTERISTIC_CONTRADICTION': {'blocked': False},
                    'CANONICAL_ABSENCE_SUPPORT': {'verified': True}, 'SECTION_EVIDENCE': {'verified': True},
                    'CHARACTERISTIC_IDENTITY_SUPPORT': {'identity_evidence_complete': True}}
        before = copy.deepcopy(row)
        out = propose_readiness(row, advisory, dedup_verified=True, group_id='NEWG-local')
        self.assertEqual(out['state'], 'READY_TO_CREATE_IDENTITY')
        self.assertFalse(out['full_ready']); self.assertIsNone(out['sterbrust_product_id'])
        self.assertEqual(row, before)
        self.assertEqual(out, propose_readiness(row, advisory, dedup_verified=True, group_id='NEWG-local'))

    def test_scoped_artifact_preserves_4405_other_rows(self):
        root = Path(__file__).resolve().parents[1]
        out = root / 'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02'
        if not out.exists(): self.skipTest('Accepted artifact not generated yet')
        old = json.loads((root/'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02/ACCEPTED/MATCHING_ACCEPTED.json').read_text(encoding='utf-8'))['rows']
        new = json.loads((out/'MATCHING_ACCEPTED.json').read_text(encoding='utf-8'))['rows']
        changes = [(a,b) for a,b in zip(old,new) if a != b]
        self.assertEqual(len(changes), 2)
        for a,b in changes:
            self.assertEqual(a['classification'], 'REVIEW')
            self.assertEqual(b['classification'], 'READY_TO_CREATE_IDENTITY')
            self.assertIsNone(b['sterbrust_product_id'])
            self.assertFalse(b['readiness']['full_ready'])
            for field in ('source','external_id','source_product_id','offer_id','model','execution','properties','observed_properties'):
                self.assertEqual(a.get(field), b.get(field))

    def test_operator_section_scopes_exact_two_not_category_mapping(self):
        root = Path(__file__).resolve().parents[1]
        path = root/'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02/READINESS_PROOFS.json'
        if not path.exists(): self.skipTest('Accepted artifact not generated yet')
        proofs = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual({(p['model'], p['section']['id']) for p in proofs}, {('UYYM-120',5641), ('HTZ-800 RS',4583)})
        for p in proofs:
            self.assertEqual(p['section']['scope'], {'source':p['source'],'external_id':p['external_id']})
            self.assertEqual(p['section']['provenance'], 'OPERATOR-CONFIRMED')

    def test_real_proposal_replay_exact_noop(self):
        root = Path(__file__).resolve().parents[1]
        path = root/'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02/READINESS_PROOFS.json'
        if not path.exists(): self.skipTest('Accepted artifact not generated yet')
        proofs = json.loads(path.read_text(encoding='utf-8'))
        rows = json.loads((root/'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02/ACCEPTED/MATCHING_ACCEPTED.json').read_text(encoding='utf-8'))['rows']
        for p in proofs:
            row = next(r for r in rows if r['external_id'] == p['external_id'] and r['source'] == p['source'])
            accepted = apply_ready_proposal(row, p)
            self.assertEqual(accepted, apply_ready_proposal(accepted, p))
            self.assertEqual(row['classification'], 'REVIEW')
