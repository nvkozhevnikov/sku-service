"""Pinned saved-listing ingestion only. No HTTP, matching, selection or scheduler."""
from __future__ import annotations
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.intervesp_full_preflight import read_source_state
from scripts.run_full_supplier_qa import _preflight_rc
from universal_supplier.commercial_persistence import HttpCapture, CommercialObservationLedger
from universal_supplier.intervesp_listing import listing_snapshot
from universal_supplier.postgres import PostgresConfig, PostgresRepository

PREP = ROOT / 'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01'
SOURCE = ROOT.parent / 'sku-service-full-discovery-rc1'


def digest(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''): h.update(block)
    return h.hexdigest()


def saved_pairs():
    assessment = json.loads((PREP / 'OFFLINE_ASSESSMENT.json').read_text(encoding='utf-8'))
    for name, expected in assessment['input_sha256'].items():
        if digest(Path(name)) != expected: raise RuntimeError('Pinned input changed: ' + name)
    folder = SOURCE / 'full_discovery/evidence/intervesp'
    rows = json.loads((folder / 'INTERVESP_LISTING_FIELD_MATRIX.json').read_text(encoding='utf-8'))['products']
    index = json.loads((folder / 'LISTING_EVIDENCE_INDEX.json').read_text(encoding='utf-8'))
    pages = {page['url']: page for page in index['pages']}
    for page in pages.values():
        if digest(SOURCE / page['local_evidence_path']) != page['sha256']:
            raise RuntimeError('Saved listing HTML hash changed')
    pairs = []
    for row in rows:
        available = [pages[url] for url in row['listing_pages'].split(' | ')]
        if set(page['sha256'] for page in available) != set(row['evidence_sha256'].split(' | ')):
            raise RuntimeError('Listing evidence index/row mismatch')
        page = max(available, key=lambda item: item['fetched_at'])
        capture = HttpCapture(requested_url=page['url'], final_url=page['final_url'],
            http_status=page['http_status'], content_type=page['content_type'],
            observed_at=datetime.fromisoformat(page['fetched_at'].replace('Z', '+00:00')),
            response_sha256=page['sha256'], evidence_sha256=page['sha256'],
            evidence_ref=str(SOURCE / page['local_evidence_path']), capture_scope='intervesp_listing',
            response_hash_basis='sanitized_archive',
            diagnostics=('saved_listing_replay_no_new_http', 'original_response_digest_unavailable',
                         'digest_of_sanitized_saved_body', 'model_is_candidate_not_confirmed_identity'))
        pairs.append((row, listing_snapshot(row), capture))
    if len(pairs) != 1642 or len({p.source_url for _, p, _ in pairs}) != 1642:
        raise RuntimeError('Manifest cardinality/duplicates changed')
    return pairs


def write_report(path, report):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    temporary.replace(path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-write', action='store_true')
    parser.add_argument('--limit', type=int)
    parser.add_argument('--verify-db-replay', action='store_true')
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    if args.limit is not None and not 1 <= args.limit <= 1642: raise ValueError('Invalid limit')
    if args.report.exists(): raise ValueError('Report already exists; preserve checkpoint')
    pairs = saved_pairs()
    ledger = CommercialObservationLedger()
    for _, product, capture in pairs: ledger.ingest(product, capture)
    if any(ledger.ingest(product, capture).observation_created for _, product, capture in pairs):
        raise RuntimeError('Offline replay is not exact no-op')
    before = read_source_state()
    old_ids = {str(row[1]): row[0] for row in before['intervesp_identities']}
    old_offers = None
    report = {'started_at': datetime.now(timezone.utc).isoformat(), 'before': before,
              'offline_replay': {'products': len(ledger.products), 'offers': len(ledger.offers),
                                 'observations': len(ledger.observations), 'second_pass_new_observations': 0},
              'http_gets': 0, 'stage': 'DRY_RUN', 'rows': [], 'replay_rows': [],
              'provenance': 'Grok sanitized listing archive, original raw response digest unavailable'}
    args.report.parent.mkdir(parents=True, exist_ok=True)
    write_report(args.report, report)
    if not args.apply_write: print(json.dumps({k:v for k,v in report.items() if k != 'before'})); return
    passfile = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/rc-local/pgpass.conf'
    os.environ['PGPASSFILE'] = str(passfile)
    repository = PostgresRepository(PostgresConfig('127.0.0.1', 55449, 'universal_supplier_server', 'rc_runtime', None, 'disable'))
    try:
        _preflight_rc(repository)
        with repository.connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended('intervesp-listing-archive-job',0))")
            if not cursor.fetchone()[0]: raise RuntimeError('Another listing archive job is running; STOP')
        with repository.connection.cursor() as cursor:
            cursor.execute("SELECT o.id,o.source_product_id FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='intervesp'")
            old_offers = set(cursor.fetchall())
        # One fresh read-only logical backup before this applied batch; never restore it here.
        backup = args.report.with_suffix('.before.dump')
        if backup.exists(): raise RuntimeError('Backup path exists')
        pgdump = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
        subprocess.run([str(pgdump), '-h', '127.0.0.1', '-p', '55449', '-U', 'rc_admin',
            '-d', 'universal_supplier_server', '-Fc', '--no-owner', '-f', str(backup)], check=True)
        report['backup'] = {'path': str(backup), 'sha256': digest(backup), 'bytes': backup.stat().st_size}
        selected = pairs[:args.limit] if args.limit else pairs
        report['stage'] = 'INGESTING_LISTING_CANDIDATES'
        for number, (row, product, capture) in enumerate(selected, 1):
            if number % 25 == 1: _preflight_rc(repository)
            result = repository.persist_commercial_observation(product, capture)
            report['rows'].append({'url': row['product_url'], 'detail_required': row['detail_get_reason'],
                **{key:value for key,value in result.items() if key != 'ingest_counts'}})
            write_report(args.report, report)
            if number % 100 == 0: print('saved listing candidates:', number, flush=True)
        if args.verify_db_replay:
            for row, product, capture in selected:
                result = repository.persist_commercial_observation(product, capture)
                if not result['exact_noop']: raise RuntimeError('Database replay is not exact no-op')
                report['replay_rows'].append({'url': row['product_url'], 'exact_noop': True})
        after = read_source_state()
        current_ids = {str(row[1]): row[0] for row in after['intervesp_identities']}
        if any(current_ids.get(external) != pk for external, pk in old_ids.items()):
            raise RuntimeError('Legacy Intervesp source PK/identity changed')
        with repository.connection.cursor() as cursor:
            cursor.execute("SELECT o.id,o.source_product_id FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='intervesp'")
            if not old_offers.issubset(set(cursor.fetchall())): raise RuntimeError('Legacy offer ID changed')
        if after['table_counts']['product_matches'] != before['table_counts']['product_matches'] or after['table_counts']['catalog_products'] != before['table_counts']['catalog_products']:
            raise RuntimeError('Canonical/matching state unexpectedly changed')
        report.update(stage='LISTING_INGEST_VERIFIED_DETAIL_PENDING', after=after,
                      legacy_source_offer_ids_retained=True, finished_at=datetime.now(timezone.utc).isoformat())
        write_report(args.report, report)
        print(json.dumps({'stage': report['stage'], 'rows': len(report['rows']), 'exact_noop_replay': len(report['replay_rows']), 'after': after['table_counts']}))
    except Exception as error:
        report.update(stage='STOPPED_ERROR', error=str(error))
        write_report(args.report, report)
        raise
    finally: repository.close()


if __name__ == '__main__': main()
