"""Offline status/hash checkpoint, no HTTP/SQL and no release publication."""
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05'


def main():
    files = [
        'reports/KAMI_SOURCE_AUDIT_2026-10-05/CATALOG_DISCOVERY/CHECKPOINT.json',
        'reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION_PLAN.json',
        'reports/KAMI_INTEGRATION_2026-10-05/DATABASE/RESTORE_VERIFICATION.json',
        'reports/KAMI_INTEGRATION_2026-10-05/CANARY_VERIFIED.json',
        'reports/KAMI_INTEGRATION_2026-10-05/VARIANT_CANARY_VERIFIED.json',
        'reports/KAMI_INTEGRATION_2026-10-05/PANEL_DATA_VERIFIED.json',
        'reports/KAMI_INTEGRATION_2026-10-05/FOCUSED_TESTS.xml',
        'tests/fixtures/kami/manifest.json',
        'reports/KAMI_INTEGRATION_2026-10-05/KAMI_FOCUSED_CURRENT.xml',
        'scripts/collect_kami_products.py',
        'scripts/verify_kami_reconciliation.py',
        'scripts/persist_kami_full_run1.py',
        'universal_supplier/adapters/kami.py',
        'universal_supplier/kami_matching.py',
        'universal_supplier/kami_persistence.py',
        'universal_supplier/postgres.py',
        'universal_supplier/kami_checkpoint.py',
        'scripts/run2_kami_products.py',
        'reports/KAMI_INTEGRATION_2026-10-05/CANONICAL_CURRENT/VERIFIED.json',
        'reports/KAMI_INTEGRATION_2026-10-05/COLLECTOR_FILE_LOCK_RECOVERY.json',
        'universal_supplier/kami_overlap.py',
        'scripts/match_kami_verified.py',
        'scripts/audit_kami_robots_blocker.py',
        'reports/KAMI_INTEGRATION_2026-10-05/ROBOTS_SCOPE_BLOCKER.json',
        'reports/KAMI_INTEGRATION_2026-10-05/REPORT.md',
    ]
    hashes = {p: hashlib.sha256((ROOT / p).read_bytes()).hexdigest() for p in files}
    collection_path = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION/CHECKPOINT.json'
    collection_bytes = collection_path.read_bytes()
    collection = json.loads(collection_bytes)
    report = {
        'recorded_at': datetime.now(timezone.utc).isoformat(), 'status': 'KAMI_INTEGRATION_IN_PROGRESS_NOT_RELEASE_READY',
        'branch': 'feature/universal-supplier-kami-rc1',
        'baseline_develop': 'deefb595868d19ba471b9b943ff779194e231c26',
        'approved_scope': 'www.stanki.ru equipment/machines; zip.stanki.ru future separate namespace',
        'baseline_accepted_counts': {'existing': 506, 'full': 2, 'review': 3640, 'conflict': 259},
        'kami_canary_rows': 4, 'supplier_disabled': True, 'offers_inactive': True, 'canonical_links': 0,
        'focused_tests': {'passed': 180, 'subtests_passed': 8},
        'latest_KAMI_and_export_focused_tests': {'passed': 76, 'subtests_passed': 4},
        'prepared_not_executed': ['scripts/verify_kami_reconciliation.py',
                                 'scripts/persist_kami_full_run1.py',
                                 'scripts/run2_kami_products.py', 'scripts/match_kami_verified.py'],
        'previous_full_suite': {'passed': 1049, 'failed': 89, 'errors': 6, 'latest_tree_full_suite': False},
        'collection_snapshot': collection['summary'], 'collection_status': collection['status'],
        'collection_snapshot_sha256': hashlib.sha256(collection_bytes).hexdigest(),
        'resume_from': str(collection_path),
        'resume_command_after_current_process_finishes_only': 'uv run --offline --with-requirements requirements.txt python -X utf8 scripts/collect_kami_products.py --coverage-resume',
        'coverage_resume_maximum': 1,
        'do_not_launch_second_collector_while_first_running': True,
        'full_ingestion_RUN1': 'NOT_STARTED', 'full_live_RUN2': 'NOT_STARTED',
        'new_matching_selection_exports': 'NOT_STARTED', 'original_RC_or_production_writes': 0,
        'hashes': hashes,
        'next_after_collection': 'Reconcile redirects/non-products/errors/identity collisions and verify coverage before full isolated persistence. Preserve accepted4407 predecessor.',
        'deep_category_coverage_followup': 'Collector gained saved-category pagination replay while existing process was running. After current process exits, resume current code once to queue missing links from saved non-product pages without repeating their GET. Do not claim full discovery from first process alone.',
        'current_collector_session': 71333,
        'first_pass_recovery': 'Transient Windows checkpoint lock fixed; previous process exited. Pending captured URL reused, one recorded GET. Coverage resume count still zero.',
        'fresh_canonical_snapshot': 'VERIFIED 59500 unique IDs/739 sections/920 properties; identity delta0; GET1233/retries0/write0; seven SHA-verified files. No old registry overwrite.',
    }
    blocker_path = OUT / 'ROBOTS_SCOPE_BLOCKER.json'
    if collection['status'] == 'STOPPED_DISCOVERY_INCOMPLETE' and blocker_path.exists():
        blocker = json.loads(blocker_path.read_bytes())
        report.update(status='BLOCKED_SOURCE_ROBOTS_DISCOVERY_INCOMPLETE', current_collector_session=None,
            blocker_ref=str(blocker_path), coverage_resume_used=0, collector_running=False,
            resume_command_after_current_process_finishes_only=None,
            collector_only_not_PostgreSQL_applied=blocker['collector_only_not_PostgreSQL_applied'],
            operator_decision_required=blocker['decision_required'],
            next_after_collection='Do not resume blindly. Resolve robots-excluded scope/evidence contract; preserve stopped queue and saved captures.')
    (OUT / 'CHECKPOINT.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'status': report['status'], 'collection': report['collection_snapshot'], 'verified_hashes': len(hashes)}))


if __name__ == '__main__':
    main()
