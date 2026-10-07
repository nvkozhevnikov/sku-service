"""Approved selection-only transaction. No crawl, ingestion or matcher invocation."""
import argparse
import copy
import hashlib
import json
import os
import subprocess
from dataclasses import asdict
from datetime import datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg import sql
from scripts.persist_kami_full_run1 import preflight, runtime_preflight
from scripts.run2_kami_offline import verify_migrations, full_state_hashes
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.offer_selection import OfferCandidate
from universal_supplier.proposed_offer_selection import proposed_selection, source_key
from universal_supplier.selection_reconciliation import selection_diff, delta_counts, apply_diff

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
OUT = BASE/'SELECTION_RECONCILIATION_2026-10-06'
PRIVATE = Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'


def connection():
    password = (PRIVATE/'pgpass.conf').read_text().strip().split(':', 4)[4]
    return psycopg.connect(host='127.0.0.1', port=55451, dbname='universal_supplier_kami_rc',
                           user='kami_admin', password=password, connect_timeout=5, autocommit=True)


def save(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def current_rows(conn):
    return [r[0] for r in conn.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY catalog_product_id')]


def recompute(conn):
    rows = copy.deepcopy(json.loads((BASE/'MATCHING/MATCHING_ACCEPTED.json').read_bytes())['rows'])
    targets = dict(conn.execute('SELECT sterbrust_product_id,catalog_product_id FROM sterbrust_products'))
    records = conn.execute("""SELECT s.code,p.external_id,p.id,p.supplier_id,p.catalog_product_id,p.active,p.missed_crawls,p.last_success_at,
        s.enabled,o.id,o.supplier_id,o.active,o.offer_kind,o.missed_crawls,o.last_success_at,o.price,o.currency,
        o.availability_normalized,o.availability_raw,o.quantity,latest.started_at
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
        LEFT JOIN LATERAL (SELECT started_at FROM crawl_runs WHERE supplier_id=s.id AND crawl_type='full'
        AND status='succeeded' ORDER BY started_at DESC LIMIT 1) latest ON true""").fetchall()
    lookup = {}
    for record in records:
        key = record[0], str(record[1])
        if key in lookup:
            raise ValueError('Multiple persisted offers per source identity require explicit handling')
        lookup[key] = record
    authoritative = {source_key(r): r for r in rows}
    if set(lookup) != set(authoritative):
        raise ValueError('Persisted/accepted source scope changed')
    for row in rows:
        if row['classification'] != 'EXISTING_CONFIRMED':
            continue
        rec = lookup[source_key(row)]
        local = targets.get(str(row['sterbrust_product_id']))
        (_, _, pid, sid, linked, source_active, source_misses, source_success, enabled, oid,
         offer_sid, active, kind, offer_misses, offer_success, price, currency, availability, raw, quantity, latest) = rec
        if local is None or linked != local:
            continue
        if price != (Decimal(str(row['price'])) if row.get('price') is not None else None) or (row.get('currency') and currency != row['currency']) or (row.get('availability') and availability != row['availability']):
            continue
        candidate = OfferCandidate(local, oid, pid, sid, offer_sid, row['source'], enabled, linked,
            source_active, source_misses, source_success, active, kind, offer_misses, offer_success,
            latest, price, currency, availability, raw, quantity)
        data = asdict(candidate)
        for key, value in data.items():
            if isinstance(value, datetime): data[key] = value.isoformat()
            elif isinstance(value, Decimal): data[key] = str(value)
        row.update(source_product_id=pid, offer_id=oid, supplier_enabled=enabled, offer_active=active,
                   selection_context={'canonical_identity_verified': True, 'sterbrust_product_id': row['sterbrust_product_id'], 'candidate': data})
    state = proposed_selection(rows)
    expected = {d['catalog_product_id']: d for d in state['decisions'] if d['selected_offer_id'] is not None}
    scope = {rec[4] for rec in records if rec[4] is not None}
    old = current_rows(conn)
    # Every current winner must be attributable; orphans are a STOP, not blind cleanup.
    by_offer = {rec[9]: rec for rec in records}
    diff = selection_diff(old, expected, scope)
    for item in diff:
        rec = by_offer.get(item['persisted_offer_id'] or item['expected_offer_id'])
        if item['persisted_offer_id'] and rec is None:
            raise ValueError('Orphan persisted selection')
        if rec:
            match = authoritative[(rec[0], str(rec[1]))]
            item.update(supplier=rec[0], source_identity=str(rec[1]), current_match_state=match['classification'],
                        supplier_enabled=rec[8], offer_active=rec[11], canonical_link=rec[4],
                        current_offer_eligibility='PASS' if item['expected_offer_id']==rec[9] else 'NOT_CURRENT_SHARED_POLICY_WINNER')
            if match['classification'] in ('REVIEW', 'CONFLICT') and item['persisted_offer_id']:
                if item['action'] != 'DELETE': raise ValueError('Unconfirmed persisted winner not deleted')
    return rows, state, expected, diff


def verify_set(conn, expected):
    selected = dict(conn.execute("SELECT catalog_product_id,selected_offer_id FROM catalog_offer_selection WHERE selection_status='selected'"))
    assert selected == {k: d['selected_offer_id'] for k, d in expected.items()}
    assert not conn.execute("SELECT 1 FROM catalog_offer_selection x LEFT JOIN offers o ON o.id=x.selected_offer_id LEFT JOIN suppliers s ON s.id=o.supplier_id WHERE x.selection_status='selected' AND (o.id IS NULL OR NOT o.active OR NOT s.enabled)").fetchone()


def main():
    raise RuntimeError('STOP_P2_SELECTION_LIFECYCLE_CONTRACT: DELETE-based runner superseded by operator GLM correction; no further mutation. See GLM_PRE_PHASE2_SELECTION_CORRECTION_2026-10-06/CHECKPOINT.json')
    parser = argparse.ArgumentParser(); parser.add_argument('--apply', action='store_true'); args = parser.parse_args()
    assert subprocess.check_output(['git','branch','--show-current'], cwd=ROOT, text=True).strip()=='feature/universal-supplier-kami-rc1'
    assert subprocess.check_output(['git','rev-parse','HEAD'], cwd=ROOT, text=True).strip()=='292bd0cf27f2fc8f6f4577560cbb1d824b0880ae'
    marker = json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    config = json.loads((PRIVATE/'runtime.json').read_bytes()); os.environ.update(config)
    repo = PostgresRepository(PostgresConfig.from_env())
    try: runtime_preflight(repo)
    finally: repo.close()
    proposal = json.loads((BASE/'SELECTION/SELECTION_VERIFIED.json').read_bytes())
    with connection() as conn:
        preflight(conn, marker); verify_migrations(conn)
        with conn.transaction():
            if args.apply:
                conn.execute("SET LOCAL lock_timeout='5s'")
                tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
                for table in tables:
                    mode = 'EXCLUSIVE' if table=='catalog_offer_selection' else 'SHARE'
                    conn.execute(sql.SQL('LOCK TABLE {} IN '+mode+' MODE').format(sql.Identifier(table)))
            else: conn.execute('SET TRANSACTION READ ONLY')
            before = full_state_hashes(conn); prior = current_rows(conn)
            rows, state, expected, diff = recompute(conn)
            assert state['selected']==proposal['selected'], 'Independent shared-policy result differs from verified proposal'
            assert state['decisions']==proposal['decisions'], 'Independent physical winner proof differs'
            save('PLAN.json', {'status':'VERIFIED_EXACT_DIFF', 'counts':delta_counts(diff), 'diff':diff,
                 'before_hashes':before,'expected':expected,'supplier_HTTP':0})
            if not args.apply:
                print(json.dumps({'status':'READ_ONLY_PLAN_PASS','counts':delta_counts(diff),'expected_winners':len(expected)})); return
            if (OUT/'VERIFIED.json').exists(): raise ValueError('Applied receipt exists; no repeat mutation')
            apply_diff(conn, diff, expected)
            verify_set(conn, expected)
            inside = full_state_hashes(conn)
            assert all(inside[k]==v for k,v in before.items() if k!='catalog_offer_selection'), 'Unrelated table/sequence drift'
            kept = {d['catalog_product_id'] for d in diff if d['action']=='KEEP'}
            assert [r for r in prior if r['catalog_product_id'] in kept]==[r for r in current_rows(conn) if r['catalog_product_id'] in kept]
            assert all(d['action']=='KEEP' for d in recompute(conn)[3]), 'Repeat diff not exact no-op'
    with connection() as conn:
        preflight(conn, marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            fresh_rows, fresh_state, fresh_expected, repeat = recompute(conn)
            assert fresh_expected==expected; verify_set(conn, expected)
            after = full_state_hashes(conn); assert after==inside
            assert all(d['action']=='KEEP' for d in repeat)
            persisted = current_rows(conn)
    # Export receipt is derived from the committed SQL rows, not proposal-selected keys.
    winning_offers = {r['selected_offer_id'] for r in persisted if r['selection_status']=='selected'}
    fresh_state['selected'] = sorted([list(source_key(r)) for r in fresh_rows if r.get('offer_id') in winning_offers])
    fresh_state.update(status='LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION', persisted_table_sha256=after['catalog_offer_selection'],
                       SQL_writes=delta_counts(diff)['DELETE']+delta_counts(diff)['INSERT']+delta_counts(diff)['UPDATE'])
    save('EXPORT_INPUT_ROWS.json', {'rows':fresh_rows})
    save('SELECTION_PERSISTED.json', fresh_state)
    save('VERIFIED.json', {'status':'LIVE_VERIFIED_TRANSACTIONAL_SELECTION_RECONCILIATION',
        'before_hashes':before,'after_hashes':after,'counts':delta_counts(diff),'repeat_counts':delta_counts(repeat),
        'persisted_winners':len(winning_offers),'independent_connection':True,'unrelated_tables_sequences_unchanged':True,
        'SQL_tables_written':['catalog_offer_selection'],'supplier_HTTP':0})
    print(json.dumps({'status':'RECONCILIATION_VERIFIED','counts':delta_counts(diff),'winners':len(winning_offers)}))


if __name__=='__main__': main()
