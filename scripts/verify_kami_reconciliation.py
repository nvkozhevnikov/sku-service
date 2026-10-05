"""Offline final coverage proof. Never sends HTTP or mutates collector state/DB.

Requires both the initial pass and exactly one saved-category replay to finish.
Payloads are independently re-parsed from SHA-verified captures with current code.
"""
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urljoin

from bs4 import BeautifulSoup, UnicodeDammit
from universal_supplier.adapters.kami import KamiAdapter, parse_listing
from universal_supplier.models import FetchRecord
from sterbrust_matching.product_identity import classify_product_kind
from scripts.kami_reconciliation_contract import validate_exclusions, TERMINAL_ROUTES

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'
SOURCE = BASE / 'PRODUCT_RECONCILIATION'
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/RECONCILED'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def main():
    checkpoint_bytes = (SOURCE / 'CHECKPOINT.json').read_bytes()
    state = json.loads(checkpoint_bytes)
    if (state['status'] != 'CAPTURES_COMPLETE_RECONCILIATION_REQUIRED' or state['queue']
            or state.get('saved_category_resume_count') != 1):
        raise RuntimeError('Both collector passes must finish; no premature FULL verification')
    if state['parse_errors'] or state['identity_conflicts']:
        raise RuntimeError('Unresolved parser/identity ledger requires explicit reconciliation')
    exclusions = validate_exclusions(BASE, state)
    if any(state['completed'].get(u, {}).get('route') != 'ROBOTS_EXCLUDED' for u in exclusions['rows']):
        raise RuntimeError('Authorized exclusions not terminally reconciled')
    listing = json.loads((BASE / 'CATALOG_DISCOVERY/CHECKPOINT.json').read_bytes())
    plan = json.loads((BASE / 'PRODUCT_RECONCILIATION_PLAN.json').read_bytes())
    if sha((BASE / 'PRODUCT_RECONCILIATION_PLAN.json').read_bytes()) != state['plan_sha256']:
        raise RuntimeError('Pinned plan SHA mismatch')
    missing = set(plan['candidate_detail_urls']) - set(state['completed'])
    if missing:
        raise RuntimeError(f'{len(missing)} plan URLs not accounted for')
    cards, capture_hashes, coverage_missing = [], {}, set()
    for external, meta in state['cards'].items():
        payload_bytes = (SOURCE / meta['payload_file']).read_bytes()
        if sha(payload_bytes) != meta['payload_sha256']:
            raise RuntimeError('Collector payload SHA mismatch')
        payload = json.loads(payload_bytes)
        rec = payload['capture']
        evidence = Path(payload['evidence_ref'])
        raw = evidence.read_bytes()
        if sha(raw) != rec['sha256']:
            raise RuntimeError('Product capture SHA mismatch')
        capture_hashes[str(evidence.resolve().relative_to(BASE.resolve().parents[1]))] = rec['sha256']
        card = KamiAdapter().parse_product(FetchRecord(rec['url'], rec['url'], 200, (), 1,
                    rec.get('elapsed_ms', 0), UnicodeDammit(raw, is_html=True).unicode_markup))
        if card.external_id != external or card.name != meta['name'] or card.raw_data['full_model'] != meta['full_model']:
            raise RuntimeError('Current parser changed source identity; explicit reconciliation required')
        cards.append({'card': card.as_jsonable(), 'capture': rec,
                      'evidence_ref': str(evidence.resolve().relative_to(BASE.resolve().parents[1])),
                      'collector_payload_sha256': meta['payload_sha256']})
    # Independently prove every saved deep-category card/pagination link was
    # actually accounted for. Saved category pages are never downloaded again.
    for url, decision in state['completed'].items():
        if decision['route'] == 'REDIRECT_ALIAS':
            if decision['target'] not in state['completed']:
                coverage_missing.add(decision['target'])
        if decision['route'] != 'NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION':
            continue
        rec = decision['capture']
        candidates = [SOURCE / rec['capture']]
        candidates += [p / rec['capture'] for p in BASE.iterdir() if p.is_dir() and p != SOURCE]
        evidence = next((p for p in candidates if p.is_file() and sha(p.read_bytes()) == rec['sha256']), None)
        if evidence is None:
            raise RuntimeError('Saved category capture unavailable or SHA mismatch')
        text = UnicodeDammit(evidence.read_bytes(), is_html=True).unicode_markup
        soup = BeautifulSoup(text, 'html.parser')
        targets = [p['url'] for p in parse_listing(text, url)]
        targets += [urljoin(url, a['href']).split('#', 1)[0]
                    for a in soup.select('a[data-pagination-button][href]')]
        coverage_missing.update(set(targets) - set(state['completed']) - set(listing['completed']))
    if coverage_missing:
        raise RuntimeError(f'{len(coverage_missing)} saved-category links unaccounted for')
    routes = Counter(v['route'] for v in state['completed'].values())
    allowed = TERMINAL_ROUTES
    if set(routes) - allowed:
        raise RuntimeError('Unreconciled URL route')
    kinds = Counter(c['card']['raw_data']['card_kind'] for c in cards)
    product_kinds = Counter(classify_product_kind(c['card']['name'],
        c['card']['categories'][0]['name'] if c['card']['categories'] else '').product_kind for c in cards)
    prices = Counter(c['card']['raw_data']['price_state'] for c in cards)
    statuses = Counter(c['status'] for c in state['captures'])
    if statuses[403] or statuses[429]:
        raise RuntimeError('Access-stop capture prevents FULL verdict')
    OUT.mkdir(parents=True, exist_ok=True)
    manifest = {'status': 'REPOSITORY_VERIFIED_FULL_RECONCILIATION',
        'source_completion': 'COMPLETE_WITH_DECLARED_EXCLUSIONS',
        'robots_excluded': routes['ROBOTS_EXCLUDED'],
        'exclusion_ledger_sha256': exclusions['sha256'],
        'excluded_detail_GET': 0, 'unresolved': 0,
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'collector_checkpoint_sha256': sha(checkpoint_bytes), 'plan_sha256': state['plan_sha256'],
        'scope': state['scope'], 'saved_category_resume_count': 1,
        'candidate_urls': len(state['completed']), 'initial_candidate_urls': len(plan['candidate_detail_urls']),
        'initial_listing_pages': len(listing['completed']),
        'saved_deep_category_pages': routes['NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION'],
        'listing_pages': len(set(listing['completed']) | {u for u,d in state['completed'].items()
                        if d['route'] == 'NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION'}),
        'candidate_url_count_includes_later_classified_categories': True,
        'observed_product_detail_urls': routes['PRODUCT_OBSERVED'], 'url_routes': dict(routes),
        'proven_source_identities': len(cards), 'card_kinds': dict(kinds), 'price_states': dict(prices),
        'product_kinds': dict(product_kinds),
        'group_or_series_identities': sum(v for k,v in kinds.items()
                        if k in {'MULTI_VARIANT_WITH_EXPLICIT_IDENTITIES','SERIES_OR_GROUP_CARD'}),
        'from_price': sum(c['card']['raw_data']['price_basis'] == 'from_price' for c in cards),
        'parse_errors': 0, 'identity_collisions': 0, 'HTTP_statuses': dict(statuses),
        'HTTP_403': statuses[403], 'HTTP_429': statuses[429],
        'collector_only_NOT_POSTGRES_APPLIED': True, 'capture_hashes': capture_hashes}
    data = ''.join(json.dumps(c, ensure_ascii=False) + '\n' for c in sorted(cards, key=lambda c:c['card']['external_id']))
    (OUT / 'CARDS.jsonl').write_text(data, encoding='utf-8')
    manifest['cards_sha256'] = sha((OUT / 'CARDS.jsonl').read_bytes())
    (OUT / 'FINAL_DISCOVERY_VERIFIED.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in manifest.items() if k != 'capture_hashes'}, ensure_ascii=False))


if __name__ == '__main__':
    main()
