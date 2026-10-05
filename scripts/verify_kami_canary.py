"""Explicit new-target canary + exact saved-capture replay, not full RUN1/RUN2."""
import hashlib
import json
import os
import argparse
from datetime import datetime
from pathlib import Path
import psycopg
from psycopg import sql
from universal_supplier.adapters.kami import KamiAdapter
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.models import FetchRecord
from universal_supplier.postgres import PostgresConfig, PostgresRepository

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05'
CAPTURES = ROOT / 'reports/KAMI_SOURCE_AUDIT_2026-10-05/CONTROLLED_SAMPLE'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/kami-rc-20261005'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def fingerprint(conn, previous_only=False):
    hashes = {}
    for table in ('suppliers', 'source_products', 'offers', 'product_matches', 'catalog_offer_selection',
                  'supplier_http_captures', 'offer_commercial_observations'):
        filter_sql = ''
        if previous_only:
            if table == 'suppliers':
                filter_sql = " WHERE t.code <> 'kami'"
            elif table == 'product_matches':
                filter_sql = " WHERE t.source_product_id IN (SELECT p.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code <> 'kami')"
            elif table not in {'catalog_offer_selection'}:
                filter_sql = " WHERE t.supplier_id IN (SELECT id FROM suppliers WHERE code <> 'kami')"
        query = sql.SQL('SELECT to_jsonb(t) FROM {} t' + filter_sql + ' ORDER BY t.id').format(sql.Identifier(table))
        # catalog_offer_selection might have composite primary key rather than id.
        if table == 'catalog_offer_selection':
            query = sql.SQL('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY catalog_product_id')
        hashes[table] = digest([r[0] for r in conn.execute(query)])
    return hashes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--variants', action='store_true')
    args = parser.parse_args()
    capture_dir = CAPTURES.parent / 'SPECTR_VARIANT_SAMPLE' if args.variants else CAPTURES
    marker = json.loads((OUT / 'DATABASE/RESTORE_VERIFICATION.json').read_text(encoding='utf-8'))
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    if marker['status'] != 'LIVE_VERIFIED_KAMI_ISOLATED_BASELINE' or config['DB_NAME'] != 'universal_supplier_kami_rc' or config['DB_PORT'] != '55451':
        raise RuntimeError('Exact isolated target metadata required')
    admin = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
    with psycopg.connect(host='127.0.0.1', port=55451, dbname=config['DB_NAME'], user='kami_admin', password=admin, connect_timeout=5, autocommit=True) as conn:
        conn.execute('BEGIN READ ONLY')
        actual = conn.execute("SELECT current_database(), inet_server_port(), system_identifier::text, current_setting('server_version') FROM pg_control_system()").fetchone()
        if actual != ('universal_supplier_kami_rc', 55451, marker['system_identifier'], '17.11'):
            raise RuntimeError('Fresh target identity mismatch')
        before = fingerprint(conn, True)
        conn.execute('ROLLBACK')
        os.environ.update(config)
        repo = PostgresRepository(PostgresConfig.from_env())
        results = []
        try:
            repo.gate()
            ledger = json.loads((capture_dir / 'HTTP_AUDIT.json').read_text(encoding='utf-8'))
            for rec in ledger:
                if 'html' not in rec.get('content_type', '') or '?' in rec['url']:
                    continue
                if args.variants and not any(x in rec['url'] for x in ('/g5_42y_standart_2/', '/g5_42ys_protivoshpindel_2/')):
                    continue
                raw = (capture_dir / rec['capture']).read_bytes()
                assert hashlib.sha256(raw).hexdigest() == rec['sha256']
                card = KamiAdapter().parse_product(FetchRecord(rec['url'], rec['url'], 200, (), 1, 0, raw.decode('cp1251')))
                capture = HttpCapture(rec['url'], rec['url'], 200, rec['content_type'],
                                      datetime.fromisoformat(rec['observed_at']), rec['sha256'], rec['sha256'],
                                      str((capture_dir / rec['capture']).relative_to(ROOT)))
                first = repo.persist_kami_observation(card, capture)
                conn.execute('BEGIN READ ONLY'); pre_replay = fingerprint(conn); conn.execute('ROLLBACK')
                def content_fingerprint():
                    conn.execute('BEGIN READ ONLY')
                    values = {}
                    for table in ('product_properties', 'product_media', 'source_product_options', 'product_category_paths', 'source_product_categories'):
                        rows = conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t WHERE source_product_id=%s ORDER BY to_jsonb(t)::text').format(sql.Identifier(table)), (first['source_product_id'],)).fetchall()
                        values[table] = digest([r[0] for r in rows])
                    conn.execute('ROLLBACK')
                    return values
                content_before = content_fingerprint()
                replay = repo.persist_kami_observation(card, capture)
                conn.execute('BEGIN READ ONLY'); after_replay = fingerprint(conn); conn.execute('ROLLBACK')
                assert replay['exact_noop'] and pre_replay == after_replay and content_before == content_fingerprint()
                if args.variants:
                    conn.execute('BEGIN READ ONLY')
                    props = conn.execute('SELECT count(*) FROM product_properties WHERE source_product_id=%s', (first['source_product_id'],)).fetchone()[0]
                    stored_brand = conn.execute('SELECT brand_raw FROM source_products WHERE id=%s', (first['source_product_id'],)).fetchone()[0]
                    conn.execute('ROLLBACK')
                    assert props == len(card.properties) and stored_brand == card.brand
                results.append({'external_id': card.external_id, 'source_product_id': first['source_product_id'],
                                'first_was_noop': first.get('exact_noop'), 'replay_exact_noop': True,
                                'all_table_row_hashes_unchanged_by_replay': True})
            conn.execute('BEGIN READ ONLY'); after = fingerprint(conn, True)
            count = conn.execute("SELECT count(*), count(*) FILTER (WHERE p.catalog_product_id IS NOT NULL) FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='kami'").fetchone()
            active = conn.execute("SELECT s.enabled, bool_or(o.active) FROM suppliers s JOIN offers o ON o.supplier_id=s.id WHERE s.code='kami' GROUP BY s.enabled").fetchone()
            conn.execute('ROLLBACK')
            assert before == after and count == (4 if args.variants else 2, 0) and active == (False, False)
        finally:
            repo.close()
    report = {'status': 'LIVE_VERIFIED_TWO_CARD_CANARY_ONLY', 'database': config['DB_NAME'],
              'port': 55451, 'system_identifier': marker['system_identifier'], 'results': results,
              'baseline_namespace_hashes_preserved': before == after, 'baseline_hashes': before,
              'kami_source_rows': count[0], 'canonical_links': 0, 'supplier_enabled': False, 'offers_active': False,
              'full_RUN1': 'NOT_STARTED', 'full_RUN2': 'NOT_STARTED'}
    (OUT / ('VARIANT_CANARY_VERIFIED.json' if args.variants else 'CANARY_VERIFIED.json')).write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
