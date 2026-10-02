"""Replay Grok evidence and prepare offline ingestion/advisory matching.

No listing crawl, HTTP, database connection/write, or readiness promotion.
The resulting matching is explicitly PRE_ENRICHMENT, never the final release.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.intervesp_listing import listing_card, identity_index, bounded_rate_plan, present
from universal_supplier.matching_service import load_registry_from_csv, match_cards
from universal_supplier.proposal_export import new_candidate_id
from universal_supplier.new_group_proposals import propose_new_groups
from universal_supplier.characteristic_evidence import evaluate_candidate
from sterbrust_matching.normalization import normalize_model, model_tokens
from sterbrust_matching.product_identity import classify_model_role, classify_product_kind

HEAD = '613000e0f2962c6ba108bbe6903e415fb716d635'
REGISTRY_SHA = 'aac0d70fea301468972bb652af7d264c690f25e2d5d0b72862925d19ae1402d0'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as file:
        for block in iter(lambda: file.read(1024 * 1024), b''): digest.update(block)
    return digest.hexdigest()


def read_json(path): return json.loads(path.read_text(encoding='utf-8'))


def write_json(path, body): path.write_text(json.dumps(body, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def prepare(source, output, *, do_matching=False):
    if output.exists(): raise ValueError('output already exists; do not overwrite checkpoint')
    if subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip() != HEAD:
        raise ValueError('Grok source HEAD changed')
    subprocess.run(['git', '-C', str(source), 'diff', '--exit-code', 'HEAD', '--', 'full_discovery'], check=True, capture_output=True)
    folder = source / 'full_discovery/evidence/intervesp'
    pins = {str(p): sha(p) for p in [folder / 'LISTING_EVIDENCE_INDEX.json',
        folder / 'INTERVESP_LISTING_FIELD_MATRIX.json', folder / 'INTERVESP_LISTING_FIELD_MATRIX.csv',
        folder / 'INTERVESP_DETAIL_GET_PLAN.json', source / 'full_discovery/FULL_MANIFEST_INTERVESP.txt',
        source / 'full_discovery/listing_evidence.py', ROOT / 'reports/RC_LOCAL/MATCHING_FINAL.json',
        ROOT / 'reports/RC_LOCAL/RUN2_ALL/report.json']}
    spec = importlib.util.spec_from_file_location('verified_grok_listing_offline', source / 'full_discovery/listing_evidence.py')
    module = importlib.util.module_from_spec(spec); sys.modules[spec.name] = module; spec.loader.exec_module(module)
    print('Replaying saved 295 listing pages; no HTTP', flush=True)
    rows, plan = module.build_from_saved(source)
    if rows != read_json(folder / 'INTERVESP_LISTING_FIELD_MATRIX.json')['products'] or plan != read_json(folder / 'INTERVESP_DETAIL_GET_PLAN.json'):
        raise ValueError('Grok matrix/plan does not equal saved HTML replay')
    if len(rows) != 1642 or len({r['product_url'] for r in rows}) != 1642:
        raise ValueError('manifest cardinality/duplicates mismatch')
    prior = read_json(ROOT / 'reports/RC_LOCAL/MATCHING_FINAL.json')['rows']
    prior_index = identity_index(prior)
    cards = [listing_card(row, prior_index) for row in rows]
    if len({card.external_id for card in cards}) != len(cards): raise ValueError('external identity collision')
    previous_report = read_json(ROOT / 'reports/RC_LOCAL/RUN2_ALL/report.json')
    captures = {r['url']: r for r in previous_report['rows'] if r['source'] == 'intervesp' and r['status'] == 'PERSISTED'}
    requested_details = [r for r in rows if r['detail_get_reason'] != 'none']
    reuse, details = [], []
    for row in requested_details:
        old = captures.get(row['product_url'])
        if old:
            ref = old['evidence_ref'].removeprefix('capture://')
            path = ROOT / 'reports/RC_LOCAL/RUN2_ALL/evidence/intervesp' / ref
            if sha(path) != path.stem: raise ValueError('old detail SHA mismatch')
            # Both actual overlaps have newer UNKNOWN/conflicting listing prices.
            # Older prices cannot resolve that evidence conflict automatically.
            reuse.append({'url': row['product_url'], 'evidence_ref': old['evidence_ref'],
                         'saved_detail_http_status': old['http_status'], 'scope': 'identity/characteristics only',
                         'cannot_resolve_newer_listing_price_unknown': row['price_state'] == 'UNKNOWN'})
        details.append({'url': row['product_url'], 'reason': row['detail_get_reason'],
                        'old_detail_available': bool(old), 'mandatory_current_price_check': row['price_state'] == 'UNKNOWN'})
    base = {'stage': 'OFFLINE_PREPARED_NOT_INGESTED', 'source_head': HEAD, 'input_sha256': pins,
            'listing_replay': 'PASS', 'listing_pages': 295, 'manifest_products': 1642,
            'listing_only_products': len(rows) - len(details), 'detail_get_required': len(details),
            'old_detail_field_reuse': reuse, 'detail_get_plan': details,
            'old_identities_retained': len({r['external_id'] for r in prior if r['source'] == 'intervesp'}),
            'old_identities_in_manifest': len({card.external_id for card in cards} & {str(r['external_id']) for r in prior if r['source'] == 'intervesp'}),
            'old_outside_manifest_retained_no_deactivation': [r for r in prior if r['source'] == 'intervesp' and r['source_url'] not in {c.canonical_url for c in cards}],
            'price_states': dict(Counter(c.raw_data['price_state'] for c in cards)),
            'availability': dict(Counter(c.availability_normalized for c in cards)),
            'rate_plan': bounded_rate_plan(20), 'robots_provenance': 'saved Grok coverage, fresh robots check required before HTTP',
            'minimum_detail_minutes_at_saved_robots_delay': len(details) * 20 / 60,
            'full_live_run2': 'DEFERRED', 'http_gets_this_run': 0, 'db_writes_this_run': 0,
            'ready_to_create_real_records': 0}
    matching_rows = []
    if do_matching:
        if sha(ROOT / 'reports/STERBRUST_REGISTRY.csv') != REGISTRY_SHA: raise ValueError('registry SHA changed')
        registry = load_registry_from_csv(ROOT / 'reports/STERBRUST_REGISTRY.csv')
        # Hydrate only actual available canonical properties, not inferred labels.
        from universal_supplier.matching_service import _source, _candidates
        target_ids = {p['sterbrust_product_id'] for card in cards for p in _candidates(_source(card), registry)}
        with (ROOT / 'reports/STERBRUST_REGISTRY.jsonl').open(encoding='utf-8') as file:
            for line in file:
                value = json.loads(line); sid = str(value['sterbrust_product_id'])
                if sid in target_ids:
                    registry.products[sid]['properties'] = {p['property_name']: p['value_flat'] for p in value['properties']
                        if p.get('property_name') and p.get('value_flat') not in ('', None)}
        print('Hydrated actual canonical candidate properties; matching pre-enrichment', flush=True)
        frozen = {r['source_url']: r for r in prior if r['source'] == 'intervesp' and r['classification'] in ('EXISTING_CONFIRMED', 'CONFLICT')}
        for n, (listing, card) in enumerate(zip(rows, cards)):
            result = match_cards([card], registry)[0]
            target = registry.products.get(result['sterbrust_product_id'])
            model = present(listing['model_candidate']); normalized = normalize_model(model, card.brand)
            confirmed_full = bool(target and normalized and normalized in
                                  ({normalize_model(target['model'], target['brand'])} | set(model_tokens(target['name']))))
            if result['status'] == 'CONFLICT': classification = 'CONFLICT'
            elif result['auto_accepted'] == 'TRUE' and confirmed_full and listing['matching_evidence'] == 'SUFFICIENT': classification = 'EXISTING_CONFIRMED'
            elif result['status'] == 'NEW_CANDIDATE' and listing['matching_evidence'] == 'SUFFICIENT': classification = 'NEW_CANDIDATE'
            else: classification = 'REVIEW'
            existing_id = result['sterbrust_product_id'] if classification == 'EXISTING_CONFIRMED' else ''
            frozen_row = frozen.get(card.canonical_url)
            if frozen_row:
                classification, existing_id = frozen_row['classification'], frozen_row['sterbrust_product_id']
            matching_rows.append({'source': 'intervesp', 'external_id': card.external_id, 'source_url': card.canonical_url,
                'name': card.name, 'brand': card.brand, 'model': model, 'execution': model,
                'source_category': present(listing['category']), 'classification': classification,
                'sterbrust_product_id': existing_id, 'sterbrust_name': registry.products.get(existing_id, {}).get('name', ''),
                'new_candidate_id': new_candidate_id('intervesp', card.external_id, normalized) if classification == 'NEW_CANDIDATE' and normalized else '',
                'model_role': classify_model_role(card.name, present(listing['category']), model).role,
                'product_kind': classify_product_kind(card.name, present(listing['category'])).product_kind,
                'properties': {p.name: p.value for p in card.properties},
                'evidence_ref': 'listing://' + listing['evidence_sha256'], 'match_evidence': result,
                'prior_confirmed_decision_retained': bool(frozen_row), 'enrichment_pending': listing['detail_get_reason'] != 'none',
                'price_state': card.raw_data['price_state'], 'price': str(card.price) if card.price is not None else None,
                'currency': card.currency or None, 'availability': card.availability_normalized,
                'readiness_final': False})
            if n % 200 == 0: print(f'Offline matching {n}/{len(cards)}', flush=True)
        evidence_dir = ROOT / 'reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED'
        checks = read_json(evidence_dir / 'BUILD_VERIFIED.json')['output_sha256']
        if any(sha(evidence_dir / filename) != digest for filename, digest in checks.items()): raise ValueError('typed evidence SHA changed')
        learned = read_json(evidence_dir / 'SCOPED_CHARACTERISTIC_EVIDENCE.json')
        absence = read_json(evidence_dir / 'CANONICAL_ABSENCE_INDEX.json')
        new_rows = [r for r in matching_rows if r['classification'] == 'NEW_CANDIDATE']
        # Other-source proposals remain diagnostic; missing field provenance is not invented.
        new_rows += [r for r in prior if r['source'] != 'intervesp' and r['classification'] == 'NEW_CANDIDATE']
        grouping = propose_new_groups(new_rows)
        membership = {(r['source'], r['external_id']): g for g in grouping['groups'] for r in g['source_records']}
        for row in matching_rows:
            if row['classification'] not in ('NEW_CANDIDATE', 'REVIEW'): continue
            advisory = evaluate_candidate({**row, 'properties': [{'name': k, 'value': v} for k,v in row['properties'].items()]},
                learned, registry_index=absence, new_group=membership.get((row['source'], row['external_id'])))
            row['readiness_advisory'] = advisory
        base['pre_enrichment_matching_counts'] = dict(Counter(r['classification'] for r in matching_rows))
        base['final_matching'] = 'NOT RUN: RC availability and detail enrichment are blocked'
        base['new_group_proposals'] = grouping
    if any(sha(Path(path)) != digest for path,digest in pins.items()): raise ValueError('inputs changed during run')
    output.mkdir(parents=True)
    write_json(output / 'LISTING_INGEST_PREPARED.json', [c.as_jsonable() for c in cards])
    write_json(output / 'INTERVESP_PRE_ENRICHMENT_MATCHING.json', matching_rows)
    write_json(output / 'OFFLINE_ASSESSMENT.json', base)
    print(json.dumps({k:v for k,v in base.items() if k not in ('input_sha256','detail_get_plan','old_outside_manifest_retained_no_deactivation','new_group_proposals')},ensure_ascii=False,indent=2),flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-root', type=Path, default=ROOT.parent / 'sku-service-full-discovery-rc1')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--offline-matching', action='store_true')
    args = parser.parse_args(); prepare(args.source_root, args.output, do_matching=args.offline_matching)
