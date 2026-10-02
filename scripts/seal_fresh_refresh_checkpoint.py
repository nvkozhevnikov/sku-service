"""Seal saved proposals and read-only postflight; no ingestion or matcher call."""
from pathlib import Path
import sys, json, hashlib
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.apply_fresh_refresh_rc import OUT, connect, load, state
from scripts.intervesp_full_preflight import read_source_state


def save(name, value):
    path = OUT / name
    body = json.dumps(value, ensure_ascii=False, indent=2, default=str).encode('utf-8')
    if path.exists():
        assert path.read_bytes() == body, f'Existing sealed artifact differs: {name}'
    else:
        path.write_bytes(body)


def main():
    assert not (OUT/'CHECKPOINT.json').exists(), 'Already sealed; resume checkpoint'
    applied = load(OUT/'APPLIED.json')
    matching = load(OUT/'FINAL_HOLD_RECONCILED/MATCHING_PROPOSALS.json')
    audits = load(OUT/'FINAL_HOLD_RECONCILED/BEKA_RETAINED_HOLDS_AUDIT.json')
    predecessor = load(ROOT/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED/MATCHING_AFTER.json')
    original = {(r['source'], str(r['external_id'])): r for r in predecessor['rows']}
    rows = {(r['source'], str(r['external_id'])): r for r in matching['rows']}
    assert len(rows) == len(matching['rows']) == 4407
    for key, previous in original.items():
        if previous['classification'] == 'EXISTING_CONFIRMED':
            assert rows[key] == previous
    blockers = Counter()
    for audit in audits:
        key = audit['source'], str(audit['external_id'])
        assert rows[key]['classification'] != 'EXISTING_CONFIRMED'
        rows[key]['historical_hold_resolution'] = audit
        rows[key]['next_evidence_route'] = (
            'canonical_side_evidence' if audit['hold_verdict'].startswith('CANONICAL_SIDE')
            else 'retained_execution_blocker')
        rows[key]['human_required_proven'] = False
        blockers.update(audit['blocking_reasons'])
    pre = read_source_state()
    with connect(True) as connection:
        current = state(connection)
        assert json.loads(json.dumps(current, default=str)) == load(OUT/'AFTER_SQL.json'), 'RC changed since completed refresh'
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM product_matches WHERE is_current AND NOT auto_accepted AND status IN ('EXACT_MATCH','EXISTING_CONFIRMED','CONFIRMED','ACCEPTED','MANUAL_CONFIRMED')")
            manual_confirmed = cursor.fetchone()[0]
            assert manual_confirmed == 0, 'Manual confirmed lineage requires explicit reconciliation'
            cursor.execute('SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products GROUP BY 1,2 HAVING count(*)>1) duplicates')
            assert cursor.fetchone()[0] == 0
            cursor.execute('SELECT count(*) FROM (SELECT source_product_id,offer_kind FROM offers GROUP BY 1,2 HAVING count(*)>1) duplicates')
            assert cursor.fetchone()[0] == 0
    save('POSTFLIGHT_VERIFIED.json', {
        'evidence_level': 'LIVE-VERIFIED', 'identity': pre['identity'],
        'data_directory': pre['data_directory'], 'source_counts': current['source_counts'],
        'totals': current['totals'], 'safety_counts': pre['safety_counts'],
        'state_equals_completed_refresh': True, 'duplicate_products': 0,
        'duplicate_default_offers': 0, 'manual_confirmed_current_links': manual_confirmed,
        'sql_matching_untouched': True,
        'automatic_legacy_links_not_promoted_to_current_truth': True})
    save('ACCEPTED_MATCHING_PROPOSALS.json', matching)
    save('HOLD_REASON_STATISTICS.json', dict(blockers))
    save('FOLLOWUP_PENDING.json', {
        'original_accepted_commit': 'a54c139e728e60aff2e7ae2e44a81c66cb9fa70b',
        'observed_unaccepted_followup_commit': 'ba2b3f2cf01520febfe8e328312b972f9661569b',
        'status': 'AVAILABLE_NOT_YET_VALIDATED_OR_APPLIED',
        'optimum_current_snapshot': 1353, 'provisional': True,
        'next_action': 'Verify follow-up against pinned evidence and reconcile eight URL dispositions without repeating ingestion/crawl'})
    checks = {p.relative_to(OUT).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in OUT.rglob('*') if p.is_file() and p.suffix in ('.json', '.md')
              and p.name not in ('CHECKPOINT.json', 'ARTIFACT_SHA256.json')}
    save('ARTIFACT_SHA256.json', checks)
    save('CHECKPOINT.json', {
        'status': 'PROVISIONAL_REFRESH_APPLIED_MATCHING_PROPOSALS_VERIFIED',
        'resume_from': 'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02/CHECKPOINT.json',
        'authoritative_matching': 'ACCEPTED_MATCHING_PROPOSALS.json',
        'summary': matching['summary'], 'applied': True,
        'replay_exact_noop_rows': len(applied['replay']),
        'predecessor_existing_preserved': 346, 'completed_scope_do_not_repeat': [
            'Partner-ST/Optimum pinned refresh ingestion', '2578 row exact-noop replay',
            '24 Beka fresh-registry audit', '80 retained-hold audit', 'accepted consolidated matcher pass'],
        'next_step': 'Validate Grok Optimum eight-URL follow-up; retain canonical-side blockers; no final exports/enrichment until identity counts stabilized',
        'sql_matching_written': False, 'final_exports_published': False,
        'full_live_run2_started': False, 'http_requests': 0,
        'limitations': ['Optimum follow-up acceptance pending', 'Matching is proposal-only, not persisted canonical links',
                        'RC catalog_products stays 59427; matching uses verified 59500 external snapshot',
                        'UI/browser QA not run in this scope', 'No complete strict NEW readiness proof'],
        'accepted_global_passes': 1, 'actual_global_evaluations': 2,
        'discarded_postprocessing_attempts': 2,
        'tests': 'TESTS_VERIFIED.json', 'artifact_hashes': 'ARTIFACT_SHA256.json'})
    print(json.dumps({'sealed': True, 'summary': matching['summary'], 'hold_blockers': dict(blockers)}))


if __name__ == '__main__':
    main()
