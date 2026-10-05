"""One KAMI-only proposal pass after verified full RUN1/RUN2; no SQL or HTTP.

The frozen five-source decisions are copied byte-equivalently as JSON values,
never passed back through a matcher. An absent exact model is not NEW proof.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from scripts.build_characteristic_evidence import section_evidence, sha256
from universal_supplier.beka_canonical_offline import SavedCandidateIndex, canonical_view
from universal_supplier.characteristic_evidence import build_evidence, evaluate_candidate, equipment_scope
from universal_supplier.kami_matching import propose_card, attach_observation, merge_preserving_predecessor
from universal_supplier.kami_overlap import cross_supplier_overlaps
from universal_supplier.models import ProductCard
from universal_supplier.new_group_proposals import propose_new_groups
from universal_supplier.new_readiness import propose_readiness
from universal_supplier.proposal_export import new_candidate_id
from sterbrust_matching.normalization import normalize_model, extract_model, model_tokens

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05'
OUT = BASE / 'MATCHING'
PREDECESSOR = ROOT / 'release/FINAL_RC_2026-10-02/accepted/MATCHING_ACCEPTED.json'
EXPECTED = {'EXISTING_CONFIRMED': 506, 'READY_TO_CREATE_FULL': 2, 'REVIEW': 3640, 'CONFLICT': 259}


def counters(rows):
    return dict(sorted(Counter(r['classification'] for r in rows).items()))


def load_inputs():
    run1_path, run2_path = BASE / 'FULL_RUN1_CHECKPOINT.json', BASE / 'LIVE_RUN2/CHECKPOINT.json'
    run1, run2 = (json.loads(p.read_bytes()) for p in (run1_path, run2_path))
    if (run1['status'] != 'LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY'
            or run2['status'] != 'LIVE_RUN2_COMPLETE' or not run2.get('baseline_preserved')
            or not run1.get('full_saved_replay_exact_noop') or not run2.get('full_saved_replay_exact_noop')):
        raise RuntimeError('Verified full RUN1/RUN2 and exact replay required')
    cards_path = BASE / 'RECONCILED/CARDS.jsonl'
    if sha256(cards_path) != run1['cards_sha256'] or run2['cards_sha256'] != run1['cards_sha256']:
        raise RuntimeError('Pinned RUN1/RUN2 input SHA mismatch')
    predecessor_bytes = PREDECESSOR.read_bytes()
    predecessor = json.loads(predecessor_bytes)['rows']
    if len(predecessor) != 4407 or counters(predecessor) != EXPECTED:
        raise RuntimeError('Wrong authoritative five-source predecessor')
    source = [json.loads(line) for line in cards_path.read_text(encoding='utf-8').splitlines()]
    if set(run2['completed']) != {c['card']['external_id'] for c in source}:
        raise RuntimeError('RUN2 did not account for every source identity')
    return predecessor, source, run1, run2, hashlib.sha256(predecessor_bytes).hexdigest()


def main():
    predecessor, source, run1, run2, predecessor_sha = load_inputs()
    snapshot = BASE / 'CANONICAL_CURRENT'
    verified = json.loads((snapshot / 'VERIFIED.json').read_bytes())
    if verified['status'] != 'VERIFIED_FRESH_GET_ONLY_CANONICAL_SNAPSHOT' or verified['products'] != 59500:
        raise RuntimeError('Fresh complete canonical snapshot required')
    for name, expected in verified['sha256'].items():
        if sha256(snapshot / name) != expected:
            raise RuntimeError('Fresh canonical evidence SHA changed: ' + name)
    targets, models, families = {}, defaultdict(set), defaultdict(set)
    with (snapshot / 'STERBRUST_REGISTRY.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            raw = json.loads(line)
            pid = str(raw['sterbrust_product_id'])
            if pid in targets:
                raise RuntimeError('Duplicate canonical ID')
            targets[pid] = canonical_view(raw)
            # Active AND inactive canonical identities contribute to absence
            # support; aliases/name tokens retrieve only, never auto-confirm.
            keys = {normalize_model(raw.get('model_raw'), raw.get('brand_raw')),
                    normalize_model(extract_model(raw['name'])),
                    *[normalize_model(t) for t in model_tokens(raw['name'])]} - {''}
            for key in keys:
                models[key].add(pid)
                family = re.match(r'[a-zа-я]+\d+', key)
                families[family.group(0) if family else key].add(pid)
    if len(targets) != 59500:
        raise RuntimeError('Canonical completeness mismatch')
    index = SavedCandidateIndex(targets)
    kami_rows, cards_by_id = [], {}
    for item in source:
        external = item['card']['external_id']
        result = run2['completed'][external]
        capture = result['capture']
        if result['route'] == 'OBSERVED_PERSISTED':
            card_path = BASE / 'LIVE_RUN2' / result['card_file']
            if sha256(card_path) != result['card_sha256']:
                raise RuntimeError('Latest RUN2 card SHA changed')
            card = ProductCard.from_jsonable(json.loads(card_path.read_bytes()))
            row = propose_card(card, result['evidence_ref'], index)
        else:
            card = ProductCard.from_jsonable(item['card'])
            row = propose_card(card, item['evidence_ref'], SavedCandidateIndex({}))
            row.update(classification='REVIEW', sterbrust_product_id='', full_model_confirmed=False,
                       current_run2_hold=result, kami_matching_evidence={
                           'confirmed': None, 'candidates': [],
                           'blocking_reasons': ['LATEST_RUN2_' + result['route']]})
        cards_by_id[external] = card
        row = attach_observation(row, capture,
            source_product_id=run1['applied'][external]['source_product_id'])
        kami_rows.append(row)
        if len(kami_rows) % 100 == 0:
            print(json.dumps({'kami_matching_rows': len(kami_rows), 'total': len(source)}), flush=True)
    sections = {str(r['id']): r for r in json.loads((snapshot / 'rest/STERBRUST_SECTIONS_RAW.json').read_bytes())}
    pairs = []
    for row in kami_rows:
        if row['classification'] != 'EXISTING_CONFIRMED':
            continue
        target = targets[row['sterbrust_product_id']]
        pairs.append({**row, 'equipment_type': equipment_scope(row['name'], row['source_category']),
            'source_properties': row['observed_properties'],
            'sterbrust_properties': target['observed_properties'],
            'sections': section_evidence(target['category_ids'], sections)})
    learned = build_evidence(pairs)
    absence = {'complete': True, 'sha256': verified['sha256']['STERBRUST_REGISTRY.jsonl'],
               'models': {k: sorted(v) for k,v in models.items()},
               'families': {k: sorted(v) for k,v in families.items()}}
    overlaps = cross_supplier_overlaps(kami_rows, predecessor)
    # Only real NEW_CANDIDATE rows enter grouping. REVIEW is not silently
    # relabelled NEW to manufacture a group/absence/readiness proof.
    grouping = propose_new_groups([r for r in predecessor + kami_rows
                                  if r['classification'] == 'NEW_CANDIDATE'],
                                 allowed_sources={'partner_st', 'optimum', 'intervesp', 'beka_mak', 'beka_mak_tr', 'kami'})
    for row in kami_rows:
        if row['classification'] != 'REVIEW':
            continue
        row['new_candidate_id'] = new_candidate_id('kami', row['external_id'], normalize_model(row['model']))
        diagnostic = evaluate_candidate({**row, 'properties': row['observed_properties']}, learned,
                                        registry_index=absence)
        row['readiness_advisory'] = diagnostic
        row['readiness'] = propose_readiness(row, diagnostic, dedup_verified=False, group_id=None)
        row['new_candidate_id_is_technical_not_canonical'] = True
    merged = merge_preserving_predecessor(predecessor, kami_rows)
    if merged[:4407] != predecessor or sha256(PREDECESSOR) != predecessor_sha:
        raise RuntimeError('Frozen predecessor changed')
    OUT.mkdir(exist_ok=True)
    def write(name, value):
        (OUT / name).write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    hashes = {'predecessor': predecessor_sha, 'RUN1': sha256(BASE / 'FULL_RUN1_CHECKPOINT.json'),
              'RUN2': sha256(BASE / 'LIVE_RUN2/CHECKPOINT.json'),
              'canonical': verified['sha256']['STERBRUST_REGISTRY.jsonl']}
    write('KAMI_MATCHING_PROPOSALS.json', {'rows': kami_rows, 'summary': counters(kami_rows), 'inputs': hashes})
    write('MATCHING_ACCEPTED.json', {'rows': merged, 'summary': counters(merged),
                                    'predecessor': hashes, 'sql_canonical_links_written': 0})
    write('SCOPED_CHARACTERISTIC_SECTION_EVIDENCE.json', learned)
    write('CROSS_SUPPLIER_OVERLAPS.json', {'rows': overlaps, 'auto_canonical_assignments': 0})
    write('NEW_GROUP_READINESS.json', {'proposals': grouping,
        'retained_authoritative_FULL_groups': [r['new_group_id'] for r in predecessor if r['classification'] == 'READY_TO_CREATE_FULL'],
        'canonical_absence_from_exact_model_miss': False})
    reasons = Counter(reason for r in kami_rows for reason in
                      set(r['kami_matching_evidence'].get('blocking_reasons', [])))
    cards = list(cards_by_id.values())
    report = {'status': 'REPOSITORY_VERIFIED_KAMI_ONLY_MATCHING_PROPOSALS',
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'kami': counters(kami_rows),
        'overall': counters(merged), 'old_4407_unchanged': True, 'inputs': hashes,
        'reason_clusters': dict(reasons.most_common()), 'cross_supplier_overlap_candidates': len(overlaps),
        'cross_supplier_proven_equivalence': sum(r['proof']['status'] == 'PROVEN_SOURCE_EQUIVALENCE' for r in overlaps),
        'cross_supplier_overlap_does_not_assign_canonical_ID': True,
        'numeric_POR_missing': dict(Counter(c.raw_data['price_state'] for c in cards)),
        'FROM_PRICE': sum(c.raw_data['price_basis'] == 'from_price' for c in cards),
        'characteristics': sum(bool(c.properties) for c in cards),
        'images': sum(any(m.media_type == 'image' for m in c.media) for c in cards),
        'description_states': dict(Counter(c.raw_data['description_state'] for c in cards)),
        'document_link_coverage': sum(any(m.media_type == 'document' for m in c.media) for c in cards),
        'documents_states': dict(Counter(c.raw_data.get('documents_state', 'UNKNOWN') for c in cards)),
        'documents_downloaded': 0,
        'SQL_writes': 0, 'HTTP': 0,
        'artifact_hashes': {p.name: sha256(p) for p in OUT.glob('*.json')}}
    write('VERIFIED.json', report)
    print(json.dumps({k:v for k,v in report.items() if k not in {'artifact_hashes', 'reason_clusters'}}, ensure_ascii=False))


if __name__ == '__main__':
    main()
