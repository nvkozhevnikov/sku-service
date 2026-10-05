"""KAMI-only terminal discovery exclusions; never creates actionable cards.

Operator-confirmed 2026-10-05. This is not a global supplier/matching policy.
"""
import hashlib
import json
from pathlib import Path

from bs4 import UnicodeDammit
from universal_supplier.adapters.kami import parse_listing
from universal_supplier.kami_robots import KamiRobots

EXCLUSIONS = {
    'https://www.stanki.ru/catalog/tortsevaya_prisadka_s_chpu_new/'
    'avtomaticheskiy_sverlilno_prisadochnyy_tsentr_s_chpu_filato_drillmatic_sd3/': '9967380',
    'https://www.stanki.ru/catalog/tortsevaya_prisadka_s_chpu_new/'
    'tortsevoy_prisadochnyy_stanok_nanxing_ncb2806d/': '9527937',
}
ROBOTS_SHA = 'd56f78af8d33bdbca001d65a56927d016e08178e0645c19d31d40ba38c1dee86'
RULE = 'Disallow: /catalog/tortsevaya_prisadka_s_chpu_new/'
TERMINAL_ROUTES = frozenset({'PRODUCT_OBSERVED', 'REDIRECT_ALIAS', 'SOURCE_STALE_URL',
    'NON_PRODUCT_REQUIRES_COVERAGE_RECONCILIATION', 'ROBOTS_EXCLUDED'})


def _checked_bytes(base, relative, digest):
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()):
        raise RuntimeError('Exclusion evidence escapes source directory')
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise RuntimeError('Exclusion evidence SHA mismatch')
    return raw


def validate_exclusions(base, state):
    """Validate exact authorized URLs, original robots and visible listing proof.

    Every HTTP attempt (including redirect-to-exclusion) and identity leakage
    fails closed. No GET or SQL is performed by this validator.
    """
    base = Path(base).resolve()
    ledger_bytes = (base / 'ROBOTS_EXCLUSIONS.json').read_bytes()
    ledger = json.loads(ledger_bytes)
    if (ledger.get('status') != 'OPERATOR_CONFIRMED_ROBOTS_EXCLUSIONS'
            or not ledger.get('authorization') or ledger.get('robots_sha256') != ROBOTS_SHA
            or ledger.get('reason') != RULE or ledger.get('detail_GET_performed') is not False
            or ledger.get('excluded_from_proven_detail_product_identities') is not True):
        raise RuntimeError('Missing operator exclusion provenance/robots contract')
    rows = ledger.get('rows', [])
    if len(rows) != 2 or {r.get('url'): r.get('site_id') for r in rows} != EXCLUSIONS:
        raise RuntimeError('Exactly two authorized KAMI exclusions required')
    robot_files = sorted(base.glob('*/' + ROBOTS_SHA + '.capture'))
    if not robot_files:
        raise RuntimeError('Original robots evidence missing')
    raw_robot = _checked_bytes(base, robot_files[0].relative_to(base), ROBOTS_SHA)
    robot_text = UnicodeDammit(raw_robot).unicode_markup
    if RULE not in robot_text or any(KamiRobots(robot_text).can_fetch(u) for u in EXCLUSIONS):
        raise RuntimeError('Explicit original robots exclusion rule not proven')
    listings = []
    for evidence in ledger.get('shared_listing_evidence', []):
        if not evidence.get('category_page') or not evidence.get('observed_at'):
            raise RuntimeError('Missing listing category/time provenance')
        raw = _checked_bytes(base, evidence['capture'], evidence['sha256'])
        listings.append(parse_listing(UnicodeDammit(raw, is_html=True).unicode_markup,
                                      evidence['category_page']))
    for row in rows:
        if row.get('terminal_status') != 'ROBOTS_EXCLUDED' or row.get('detail_identity_proven') is not False:
            raise RuntimeError('Exclusion cannot be an actionable product')
        indexes = row.get('listing_evidence_indexes', [])
        if not row.get('name') or not indexes:
            raise RuntimeError('Missing exclusion listing provenance')
        for index in indexes:
            if not isinstance(index, int) or not 0 <= index < len(listings):
                raise RuntimeError('Invalid listing provenance index')
            if not any(p['url'] == row['url'] and p['site_internal_id'] == row['site_id']
                       and p['name'] == row['name'] for p in listings[index]):
                raise RuntimeError('Listing does not prove exclusion URL/site ID/name')
    for rec in state.get('captures', []):
        if rec.get('url') in EXCLUSIONS or rec.get('redirect_target') in EXCLUSIONS:
            raise RuntimeError('Detail GET/redirect evidence for ROBOTS_EXCLUDED URL')
    for external, meta in state.get('cards', {}).items():
        if external in {'site:' + v for v in EXCLUSIONS.values()} or meta.get('canonical_url') in EXCLUSIONS:
            raise RuntimeError('Excluded identity leaked into actionable cards')
    digest = hashlib.sha256(ledger_bytes).hexdigest()
    for url, decision in state.get('completed', {}).items():
        if url in EXCLUSIONS or decision.get('route') == 'ROBOTS_EXCLUDED':
            if (url not in EXCLUSIONS or decision.get('route') != 'ROBOTS_EXCLUDED'
                    or decision.get('exclusion_ledger_sha256') != digest
                    or decision.get('site_id') != EXCLUSIONS[url]
                    or decision.get('capture') or decision.get('external_id')):
                raise RuntimeError('Invalid terminal exclusion projection')
    return {'rows': {r['url']: r for r in rows}, 'sha256': digest, 'ledger': ledger}


def project_exclusions(state, proof):
    """Only authorized terminal decisions, no detail IDs/card objects added."""
    for url, row in proof['rows'].items():
        state['completed'][url] = {'route': 'ROBOTS_EXCLUDED', 'site_id': row['site_id'],
            'name': row['name'], 'exclusion_ledger_sha256': proof['sha256'],
            'evidence_ref': 'ROBOTS_EXCLUSIONS.json', 'robots_sha256': ROBOTS_SHA,
            'robots_rule': RULE, 'detail_GET': 0, 'downstream_actionable': False}
    state['queue'] = [url for url in state['queue'] if url not in proof['rows']]


def actionable_completed(state):
    """Discovery export projection: matching/readiness/persistence get only cards."""
    return {url: d for url, d in state['completed'].items() if d['route'] == 'PRODUCT_OBSERVED'}
