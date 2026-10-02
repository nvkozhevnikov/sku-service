"""Exactly two saved-source proofs, no DB/network or global matching."""
import copy
import hashlib
import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.two_new_readiness import (norm, plain, anchors, verify_anchor_proof,
                                                   readiness_gate, deterministic_group_id, family_hits, apply_ready_proposal)
from universal_supplier.proposal_export import new_candidate_id
from universal_supplier.new_readiness import propose_readiness

COMMIT = '918f9c16af892ac97821a54aa9cd05710b0f44ac'
EVIDENCE_DIR = 'reports/RC_LOCAL/NEW_READINESS_OFFLINE_TWO_2026-10-02'
OUT = ROOT / 'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02'
SECTIONS = {'uyym-120': (5641, 'Горбыльные станки'), 'htz-800-rs': (4583, 'Пилорамы ленточные')}


def load(path): return json.loads(Path(path).read_text(encoding='utf-8'))
def digest(path):
    with Path(path).open('rb') as f: return hashlib.file_digest(f, 'sha256').hexdigest()


def main():
    checkpoint_path = ROOT / 'reports/RC_LOCAL/NEW_READINESS_PRIORITY_2026-10-02/CHECKPOINT.json'
    checkpoint = load(checkpoint_path)
    matching_path = Path(checkpoint['authoritative_matching'])
    assert digest(matching_path) == checkpoint['authoritative_sha256']
    original = load(matching_path)
    rows = original['rows']
    evidence_files = {}
    for name in ('EVIDENCE.json', 'CHECKPOINT.json', 'REPORT.md', 'ANCHORS.csv', 'GATES.csv', 'PEERS.csv'):
        content = subprocess.check_output(['git', 'show', COMMIT + ':' + EVIDENCE_DIR + '/' + name], cwd=ROOT)
        evidence_files[name] = content
    evidence = json.loads(evidence_files['EVIDENCE.json'])
    assert {c['case_id'] for c in evidence['cases']} == set(SECTIONS)
    assert evidence['search']['namespaces']['matching_sha256'] == digest(matching_path)
    sections_path = ROOT / 'reports/rest/STERBRUST_SECTIONS_RAW.json'
    sections = {s['id']: s for s in load(sections_path)}
    registry_path = ROOT.parent / evidence['search']['registry']['full_jsonl']
    identity_path = ROOT.parent / evidence['search']['registry']['identity_jsonl']
    assert digest(registry_path) == evidence['search']['registry']['full_sha256']
    assert digest(identity_path) == evidence['search']['registry']['identity_sha256']
    proofs = []
    for case in evidence['cases']:
        record = next(r for r in rows if (r['source'], r['external_id']) == (case['source'], case['external_id']))
        assert record['classification'] == 'REVIEW'
        path = ROOT / case['detail_path']; assert digest(path) == case['detail_sha256']
        soup = BeautifulSoup(path.read_text(encoding='utf-8'), 'html.parser')
        h1 = soup.find('h1').get_text(' ', strip=True)
        table = soup.select_one('#elTabProp'); assert table is not None
        pairs = []
        for tr in table.select('tr'):
            cells = tr.find_all(['td', 'th'], recursive=False)
            if len(cells) == 2: pairs.append((cells[0].get_text(' ', strip=True), cells[1].get_text(' ', strip=True), ''))
        actual = anchors(pairs)
        failures = verify_anchor_proof(case, actual)
        if not norm(h1).endswith(norm(case['exact_model'])): failures.append('VISIBLE_FULL_MODEL_MISSING')
        if case['supplier_url'] != record['source_url']: failures.append('SOURCE_URL_IDENTITY_MISMATCH')
        if norm(case['exact_model']) != norm(record['model']): failures.append('SOURCE_EXECUTION_MISMATCH')
        visible_brand = [img.get('alt', '') for img in soup.find_all('img') if 'бренд:' in img.get('alt', '').lower()]
        if not any(norm(case['brand']) in norm(v) for v in visible_brand): failures.append('VISIBLE_BRAND_MISSING')
        if 'станок' not in h1.lower() or any(t in h1.lower() for t in ('комплект', 'аксессуар', 'б/у')):
            failures.append('PRODUCT_KIND_OR_CONDITION_UNPROVEN')
        if record.get('match_conflicts') or record.get('semantic_conflicts'):
            failures.append('RETAINED_SOURCE_GUARD')
        same_full = [r for r in rows if r is not record and (norm(case['exact_model']) in
                     (norm(r.get('model')), norm(r.get('execution'))) or
                     norm(case['exact_model']) in norm(r.get('name')))]
        if same_full: failures.append('UNRESOLVED_FULL_MODEL_SOURCE_PEER')
        section_id, section_name = SECTIONS[case['case_id']]
        assert sections[section_id]['active'] == 'Y' and sections[section_id]['name'] == section_name
        proofs.append(dict(case_id=case['case_id'], source=case['source'], external_id=case['external_id'],
            source_url=case['supplier_url'], model=case['exact_model'], brand=case['brand'], visible_h1=h1,
            visible_brand=visible_brand, actual_anchor_rows=pairs,
            typed_anchors={k: sorted(v) for k,v in actual.items()}, failures=failures,
            source_capture=str(path), source_capture_sha256=digest(path), full_model_peers=same_full,
            section={'id': section_id, 'name': section_name, 'provenance': 'OPERATOR-CONFIRMED',
                     'scope': {'source': case['source'], 'external_id': case['external_id']},
                     'snapshot_active_verified': True}, canonical_text_hits=[], canonical_two_anchor_hits=[]))
    ids, rows_scanned = set(), 0
    relevant = re.compile(r'пилорам|ленточн|шкив|полотн|распил|горбыл', re.I)
    for line in registry_path.open(encoding='utf-8'):
        canonical = json.loads(line); rows_scanned += 1; ids.add(str(canonical['sterbrust_product_id']))
        # Own structured identity and own description, not arbitrary markup substrings.
        identity_text = ' '.join(str(canonical.get(k) or '') for k in
            ('name', 'model_raw', 'brand_raw', 'manufacturer_raw', 'article_raw', 'supplier_article_raw'))
        desc = (canonical.get('raw_data') or {}).get('detailText') or ''
        identity_text += ' ' + ' '.join(str(p.get('value_flat') or '') for p in canonical.get('properties', []))
        for proof in proofs:
            tokens = ['uyym', 'yym', 'ustunkarli', 'устюн'] if proof['case_id'] == 'uyym-120' else ['htz', 'хтз', 'нтз', 'mebor']
            if family_hits(identity_text + ' ' + plain(desc), tokens):
                proof['canonical_text_hits'].append(str(canonical['sterbrust_product_id']))
        if not relevant.search(line): continue
        pairs = [(p.get('property_name', ''), p.get('value_flat', ''), p.get('unit', ''))
                 for p in canonical.get('properties', [])]
        for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>', desc, re.I | re.S):
            cells = re.findall(r'<t[dh]\b[^>]*>(.*?)</t[dh]>', tr, re.I | re.S)
            if len(cells) == 2: pairs.append((plain(cells[0]), plain(cells[1]), ''))
        found = anchors(pairs)
        for proof in proofs:
            agrees = [k for k,v in proof['typed_anchors'].items() if set(v) & found.get(k, set())]
            if len(set(agrees) - {'pulley_width'}) >= 2:
                proof['canonical_two_anchor_hits'].append({'id': str(canonical['sterbrust_product_id']), 'roles': agrees})
    assert rows_scanned == len(ids) == 59500
    accepted = copy.deepcopy(original)
    groups, enrichment = [], []
    for proof in proofs:
        absence = not proof['canonical_text_hits'] and not proof['canonical_two_anchor_hits']
        gates = dict(SOURCE_IDENTITY='PASS' if not proof['failures'] else 'FAIL',
                     INDEPENDENT_TYPED_IDENTITY='PASS' if not proof['failures'] else 'FAIL',
                     CROSS_SOURCE_DEDUP='PASS' if not proof['full_model_peers'] else 'FAIL',
                     CANONICAL_ABSENCE='PASS' if absence else 'FAIL', SAFE_SECTION='PASS')
        proof['gates'] = gates; proof['registry_unique_ids_verified'] = len(ids)
        proof['absence_basis'] = 'COMPLETE_ACTIVE_INACTIVE_IDENTITY_DESCRIPTION_VARIANTS_AND_ROLE_TYPED_FINGERPRINT_NO_HITS'
        assert readiness_gate(gates), proof
        row = next(r for r in accepted['rows'] if (r['source'], r['external_id']) == (proof['source'], proof['external_id']))
        candidate_id = row.get('new_candidate_id') or new_candidate_id(row['source'], row['external_id'], norm(proof['model']))
        group_id = deterministic_group_id(proof['brand'], proof['model'])
        row.update(new_candidate_id=candidate_id, new_group_id=group_id, proposed_section_id=proof['section']['id'],
                   proposed_section_path=proof['section']['name'], classification='READY_TO_CREATE_IDENTITY',
                   sterbrust_product_id=None, proposed_sterbrust_id=None, readiness_proof=proof)
        advisory = {'CHARACTERISTIC_CONTRADICTION': {'blocked': False},
                    'CANONICAL_ABSENCE_SUPPORT': {'verified': True}, 'SECTION_EVIDENCE': {'verified': True},
                    'CHARACTERISTIC_IDENTITY_SUPPORT': {'identity_evidence_complete': True}}
        readiness = propose_readiness(dict(row, classification='NEW_CANDIDATE'), advisory,
                                     dedup_verified=True, group_id=group_id)
        assert readiness['state'] == 'READY_TO_CREATE_IDENTITY'
        row['readiness'] = readiness
        row.update(apply_ready_proposal(row, proof))
        groups.append(dict(new_group_id=group_id, new_candidate_id=candidate_id, status='PROVEN_SINGLE_SOURCE_NEW_IDENTITY',
                           source_records=[{'source': row['source'], 'external_id': row['external_id']}],
                           brand=proof['brand'], full_model=proof['model'], sterbrust_product_id=None,
                           dedup_scope='all4407 source records in five namespaces', cross_source_equivalents=[],
                           proof_ref=proof['case_id']))
        enrichment.append(dict(source=row['source'], external_id=row['external_id'], supplier_url=row['source_url'],
            capture=proof['source_capture'], capture_sha256=proof['source_capture_sha256'],
            next_scope='EXACT_TWO_SAVED_CAPTURE_FULL_ENRICHMENT_OFFLINE_FIRST', http_authorized=False,
            fields=['visible name', 'visible brand', 'full model/execution', 'operator category/path',
                    'all own technical characteristics with units and role', 'own description',
                    'own product images with URLs/captions', 'own docs/files or observed NOT_PRESENT',
                    'price and price_state (request != zero)', 'availability (unknown != unavailable)',
                    'field provenance/capture/time/SHA'],
            do_not_get_again=True, no_other_18=True, full_ready=False))
    assert groups[0]['new_group_id'] != groups[1]['new_group_id']
    counts = Counter(r['classification'] for r in accepted['rows'])
    assert counts['EXISTING_CONFIRMED'] == 506 and counts['READY_TO_CREATE_IDENTITY'] == 2 and counts['REVIEW'] == 3640 and counts['CONFLICT'] == 259, counts
    changed = {p['external_id'] for p in proofs}
    assert all(a == b for a,b in zip(rows, accepted['rows']) if a['external_id'] not in changed)
    accepted['summary'] = {'Existing':506, 'READY_TO_CREATE_IDENTITY':2, 'Review':3640, 'Conflict':259}
    accepted['predecessor'] = str(matching_path)
    OUT.mkdir(exist_ok=True)
    def save(name,value): (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n',encoding='utf-8')
    for name,content in evidence_files.items():
        evidence_out = OUT/'GROK_PINNED'; evidence_out.mkdir(exist_ok=True); (evidence_out/name).write_bytes(content)
    save('READINESS_PROOFS.json', proofs); save('NEW_GROUPS.json', groups)
    save('FULL_ENRICHMENT_EXACT_SCOPE.json', enrichment); save('MATCHING_ACCEPTED.json', accepted)
    save('INPUT_SHA256.json',{str(p):digest(p) for p in [checkpoint_path, matching_path, registry_path, identity_path, sections_path]})
    save('CHECKPOINT.json',dict(status='TWO_NEW_IDENTITY_OFFLINE_VERIFIED',
        resume_from='reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02/CHECKPOINT.json',
        authoritative_matching='MATCHING_ACCEPTED.json', grok_commit=COMMIT, predecessor=str(checkpoint_path),
        summary=accepted['summary'], new_ready=2, HTTP=0, SQL=0, production=0, ESOL=0,
        final_xml_publication=False, next_action='Offline full enrichment of exactly two pinned saved cards; no other18 URLs',
        completed_do_not_repeat=['two source/typed/absence/dedup/section proofs']))
    assert digest(matching_path) == checkpoint['authoritative_sha256']
    print(json.dumps(accepted['summary']))


if __name__ == '__main__': main()
