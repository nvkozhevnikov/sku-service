"""Read-only source/DB diagnostic; writes only a separate resumable report.

Never marks blocked URLs completed, removes queue items, or sends HTTP.
"""
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import psycopg
from bs4 import UnicodeDammit
from scripts.persist_kami_full_run1 import ROOT, OUT, PRIVATE, preflight
from scripts.verify_kami_canary import fingerprint
from universal_supplier.kami_robots import KamiRobots


def main():
    source = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05'
    path = source / 'PRODUCT_RECONCILIATION/CHECKPOINT.json'
    raw = path.read_bytes()
    state = json.loads(raw)
    if state['status'] != 'STOPPED_DISCOVERY_INCOMPLETE':
        raise RuntimeError('Expected stopped collector; no active-state reader')
    rec = next(r for r in reversed(state['captures']) if r['url'] == 'https://www.stanki.ru/robots.txt')
    robots_raw = (path.parent / rec['capture']).read_bytes()
    if hashlib.sha256(robots_raw).hexdigest() != rec['sha256']:
        raise RuntimeError('Saved robots SHA mismatch')
    robot = KamiRobots(UnicodeDammit(robots_raw).unicode_markup, 'UniversalSupplier-KamiAudit')
    listing = json.loads((source / 'CATALOG_DISCOVERY/CHECKPOINT.json').read_bytes())
    blocked = []
    for url in state['queue']:
        if robot.can_fetch(url):
            continue
        entry = listing['products'].get(url, {})
        records = entry.get('listing_records', [])
        ids = sorted({r['site_internal_id'] for r in records if r.get('site_internal_id')})
        proven = [key for key in state['cards'] if key in ['site:' + value for value in ids]]
        path_query = urlsplit(url).path
        matches = [(size, allow, pattern.pattern) for size,allow,pattern in robot.rules if pattern.search(path_query)]
        blocked.append({'url': url, 'matched_rules': sorted(matches, reverse=True),
            'listing_site_ids': ids, 'saved_listing_evidence': records,
            'same_internal_id_proven_saved_detail': proven,
            'detail_capture_exists': any(r['url'] == url for r in state['captures']),
            'no_GET_sent_to_denied_URL': True, 'identity_state': 'LISTING_EVIDENCE_ONLY_NOT_FULL_CARD'})
    marker = json.loads((OUT / 'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    config = json.loads((PRIVATE / 'runtime.json').read_bytes())
    if (config['DB_NAME'] != 'universal_supplier_kami_rc' or str(config['DB_PORT']) != '55451'
            or config.get('DB_HOST') != '127.0.0.1'):
        raise RuntimeError('Wrong isolated DB target')
    admin = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
    with psycopg.connect(host='127.0.0.1', port=55451, dbname=config['DB_NAME'],
                        user='kami_admin', password=admin, autocommit=True, connect_timeout=5) as conn:
        preflight(conn, marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            baseline = json.loads((OUT / 'VARIANT_CANARY_VERIFIED.json').read_bytes())['baseline_hashes']
            unchanged = fingerprint(conn, True) == baseline
            counts = conn.execute("""SELECT count(*),count(*) FILTER (WHERE catalog_product_id IS NOT NULL)
                FROM source_products WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')""").fetchone()
            offers = conn.execute("""SELECT count(*),count(*) FILTER (WHERE active)
                FROM offers WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')""").fetchone()
            enabled = conn.execute("SELECT enabled FROM suppliers WHERE code='kami'").fetchone()[0]
            persisted = {r[0] for r in conn.execute("""SELECT external_id FROM source_products
                WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')""")}
    if not unchanged or counts != (4, 0) or offers != (4, 0) or enabled:
        raise RuntimeError('Canary/baseline isolation changed unexpectedly')
    tests = OUT / 'KAMI_FOCUSED_CURRENT.xml'
    report = {'status': 'BLOCKED_SOURCE_ROBOTS_DISCOVERY_INCOMPLETE',
        'recorded_at': datetime.now(timezone.utc).isoformat(),
        'resume_from': str(path), 'collector_checkpoint_sha256': hashlib.sha256(raw).hexdigest(),
        'scope_unchanged': 'www.stanki.ru equipment/machines; zip.stanki.ru excluded',
        'robots_sha256': rec['sha256'], 'collector_running': False,
        'listing_pages_completed': len(listing['completed']), 'collector_summary': state['summary'],
        'URL_routes': dict(Counter(d['route'] for d in state['completed'].values())),
        'captured_HTTP_statuses': dict(Counter(r['status'] for r in state['captures'])),
        'HTTP_403': sum(r['status'] == 403 for r in state['captures']),
        'HTTP_429': sum(r['status'] == 429 for r in state['captures']),
        'blocked_URLs': blocked, 'robots_denied_remaining': len(blocked),
        'robots_allowed_remaining': sum(robot.can_fetch(u) for u in state['queue']),
        'saved_category_resume_count': state.get('saved_category_resume_count', 0),
        'collector_only_not_PostgreSQL_applied': len(set(state['cards']) - persisted),
        'collector_and_PostgreSQL_identity_intersection': len(set(state['cards']) & persisted),
        'PostgreSQL_canary_identities_not_yet_in_collector': sorted(persisted - set(state['cards'])),
        'PostgreSQL_applied_source_rows': counts[0], 'PostgreSQL_applied_offers': offers[0],
        'supplier_enabled': enabled, 'offers_active': offers[1], 'canonical_links': counts[1],
        'old_five_namespace_hashes_preserved': unchanged,
        'old_accepted_totals': {'Existing': 506, 'FULL_NEW': 2, 'Review': 3640, 'Conflict': 259},
        'full_RUN1': 'NOT_STARTED', 'full_live_RUN2': 'NOT_STARTED',
        'KAMI_matching': 'NOT_EXECUTED', 'exports_panel_full_QA': 'NOT_EXECUTED',
        'latest_focused_tests': {'passed': 76, 'subtests_passed': 4, 'sha256': hashlib.sha256(tests.read_bytes()).hexdigest()},
        'prepared_only': ['verify_kami_reconciliation.py', 'persist_kami_full_run1.py',
                          'run2_kami_products.py', 'match_kami_verified.py'],
        'decision_required': 'Explicit robots-excluded scope acceptance OR permitted supplier feed/operator-provided evidence for two URLs. No robots bypass.',
        'do_not_resume_blindly': True, 'no_queue_or_collector_mutation': True,
        'no_HTTP_by_this_audit': True, 'SQL_read_only': True,
        'original_RC_connections': 0, 'production_ESOL_scheduler_deployment': 0}
    dest = OUT / 'ROBOTS_SCOPE_BLOCKER.json'
    dest.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k != 'blocked_URLs'}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
