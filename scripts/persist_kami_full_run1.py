"""Explicit isolated FULL RUN1 after verified reconciliation; no HTTP/matching.

Every persisted capture can be replayed exactly. This is NOT live RUN2.
"""
import hashlib
import json
import os
import argparse
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
import psycopg
from psycopg import sql

from scripts.verify_kami_canary import fingerprint, digest
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.models import ProductCard
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.kami_checkpoint import write_checkpoint

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05'
INPUT = OUT / 'RECONCILED'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/kami-rc-20261005'


def preflight(conn, marker):
    with conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        actual = conn.execute("""SELECT current_database(),inet_server_port(),
            system_identifier::text,current_setting('server_version') FROM pg_control_system()""").fetchone()
        if actual != ('universal_supplier_kami_rc', 55451, marker['system_identifier'], '17.11'):
            raise RuntimeError('Fresh isolated DB identity mismatch')
        # Unicode-safe raw pg data path validation used in restore verifier.
        from psycopg.types.string import TextLoader
        class BytesLoader(TextLoader):
            def load(self, data):
                return bytes(data)
        with conn.cursor() as cur:
            cur.adapters.register_loader('text', BytesLoader)
            cur.execute("SELECT current_setting('data_directory')")
            raw = cur.fetchone()[0]
        paths = []
        for encoding in ('utf-8', 'cp1251'):
            try:
                paths.append(Path(raw.decode(encoding)).resolve())
            except UnicodeDecodeError:
                pass
        if Path(marker['data_directory']).resolve() not in paths:
            raise RuntimeError('Approved KAMI data path mismatch')


def runtime_preflight(repo):
    """Check the actual writer connection, not only a separate admin session."""
    with repo.connection.transaction():
        repo.connection.execute('SET TRANSACTION READ ONLY')
        actual = repo.connection.execute("""SELECT current_database(),host(inet_server_addr()),
            inet_server_port(),current_setting('server_version'),current_user,
            (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)""").fetchone()
        if actual != ('universal_supplier_kami_rc', '127.0.0.1', 55451, '17.11', 'kami_app', False):
            raise RuntimeError('Actual writer target/role mismatch')


def complete_fingerprint(conn):
    """All persisted content tables and sequences, read-only exact replay proof."""
    hashes = fingerprint(conn)
    for table in ('product_properties', 'product_media', 'source_product_options',
                  'product_category_paths', 'source_product_categories'):
        query = sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table))
        hashes[table] = digest([r[0] for r in conn.execute(query)])
    hashes['sequences'] = digest(conn.execute("""SELECT schemaname,sequencename,last_value
        FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename""").fetchall())
    return hashes


def budget_available(started, max_seconds, *, now=None):
    return max_seconds is None or (time.monotonic() if now is None else now) - started < max_seconds - 90


def main(argv=()):
    parser = argparse.ArgumentParser()
    parser.add_argument('--max-seconds', type=float, default=None,
                        help='Stop between committed cards, reserving 90 seconds for verification/checkpoint')
    args = parser.parse_args(argv)
    if args.max_seconds is not None and args.max_seconds <= 90:
        parser.error('--max-seconds must exceed the 90-second shutdown reserve')
    started = time.monotonic()

    proof = json.loads((INPUT / 'FINAL_DISCOVERY_VERIFIED.json').read_bytes())
    raw_cards = (INPUT / 'CARDS.jsonl').read_bytes()
    if (proof['status'] != 'REPOSITORY_VERIFIED_FULL_RECONCILIATION'
            or hashlib.sha256(raw_cards).hexdigest() != proof['cards_sha256']):
        raise RuntimeError('Verified full source manifest/SHA required')
    cards = [json.loads(line) for line in raw_cards.decode('utf-8').splitlines()]
    if len(cards) != proof['proven_source_identities']:
        raise RuntimeError('Reconciled identity count mismatch')
    marker = json.loads((OUT / 'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    config = json.loads((PRIVATE / 'runtime.json').read_bytes())
    if (config.get('DB_NAME') != 'universal_supplier_kami_rc' or str(config.get('DB_PORT')) != '55451'
            or config.get('DB_HOST') != '127.0.0.1' or config.get('DB_USER') != 'kami_app'):
        raise RuntimeError('Runtime target mismatch; original RC forbidden')
    admin = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
    expected_baseline = json.loads((OUT / 'VARIANT_CANARY_VERIFIED.json').read_bytes())['baseline_hashes']
    ledger_path = OUT / 'FULL_RUN1_CHECKPOINT.json'
    ledger = json.loads(ledger_path.read_bytes()) if ledger_path.exists() else {
        'cards_sha256': proof['cards_sha256'], 'status': 'RUN1_IN_PROGRESS', 'applied': {}}
    if ledger['cards_sha256'] != proof['cards_sha256']:
        raise RuntimeError('RUN1 input changed; explicit reconciliation required')
    os.environ.update(config)
    repo = PostgresRepository(PostgresConfig.from_env())
    with psycopg.connect(host='127.0.0.1', port=55451, dbname=config['DB_NAME'],
                        user='kami_admin', password=admin, connect_timeout=5, autocommit=True) as conn:
        try:
            preflight(conn, marker)
            if fingerprint(conn, True) != expected_baseline:
                raise RuntimeError('Old namespace baseline drift; STOP before writes')
            repo.gate()
            runtime_preflight(repo)
            for payload in cards:
                if not budget_available(started, args.max_seconds):
                    preflight(conn, marker)
                    if fingerprint(conn, True) != expected_baseline:
                        raise RuntimeError('Old namespace drift at bounded stop')
                    ledger.update(status='RUN1_SAFE_CHECKPOINT_TIME_BUDGET',
                                  baseline_preserved=True, full_live_RUN2='NOT_STARTED',
                                  stopped_between_committed_cards=True)
                    write_checkpoint(ledger_path, ledger)
                    print(json.dumps({'status': ledger['status'], 'verified_cards': len(ledger['applied'])}), flush=True)
                    return
                card = ProductCard.from_jsonable(payload['card'])
                rec = payload['capture']
                evidence = ROOT / payload['evidence_ref']
                if hashlib.sha256(evidence.read_bytes()).hexdigest() != rec['sha256']:
                    raise RuntimeError('Capture SHA mismatch before write')
                capture = HttpCapture(rec['url'], rec['url'], 200, rec['content_type'],
                    datetime.fromisoformat(rec['observed_at']), rec['sha256'], rec['sha256'], payload['evidence_ref'])
                # Even resume must verify the actual connection/cluster before
                # any source write, not trust a stale startup-only preflight.
                preflight(conn, marker)
                runtime_preflight(repo)
                result = repo.persist_kami_observation(card, capture)
                materialized = repo.materialize_kami_saved_content(card, capture)
                replay = repo.persist_kami_observation(card, capture)
                if not replay['exact_noop'] or repo.materialize_kami_saved_content(card, capture):
                    raise RuntimeError('Exact persisted-capture replay changed content')
                ledger['applied'][card.external_id] = {'source_product_id': result['source_product_id'],
                    'capture_id': result['capture_id'], 'exact_replay_noop': True,
                    'saved_content_materialized': materialized}
                ledger['updated_at'] = datetime.now(timezone.utc).isoformat()
                write_checkpoint(ledger_path, ledger)
                if len(ledger['applied']) % 100 == 0:
                    print(json.dumps({'committed_verified_cards': len(ledger['applied']),
                                      'elapsed_seconds': round(time.monotonic()-started, 1)}), flush=True)
            # A full saved-capture replay proves the whole persisted state,
            # including content and sequences, not just a return-value flag.
            preflight(conn, marker)
            replay_before = complete_fingerprint(conn)
            for payload in cards:
                if not budget_available(started, args.max_seconds):
                    preflight(conn, marker)
                    if fingerprint(conn, True) != expected_baseline:
                        raise RuntimeError('Old namespace drift at replay stop')
                    ledger.update(status='RUN1_APPLIED_FULL_REPLAY_PENDING_TIME_BUDGET',
                                  baseline_preserved=True, full_live_RUN2='NOT_STARTED',
                                  stopped_between_committed_cards=True)
                    write_checkpoint(ledger_path, ledger)
                    print(json.dumps({'status': ledger['status'], 'verified_cards': len(ledger['applied'])}), flush=True)
                    return
                card = ProductCard.from_jsonable(payload['card'])
                rec = payload['capture']
                capture = HttpCapture(rec['url'], rec['url'], 200, rec['content_type'],
                    datetime.fromisoformat(rec['observed_at']), rec['sha256'], rec['sha256'], payload['evidence_ref'])
                preflight(conn, marker)
                runtime_preflight(repo)
                if not repo.persist_kami_observation(card, capture)['exact_noop']:
                    raise RuntimeError('Whole RUN1 saved replay changed state')
            if complete_fingerprint(conn) != replay_before:
                raise RuntimeError('Whole RUN1 replay table/sequence hashes changed')
            preflight(conn, marker)
            after = fingerprint(conn, True)
            if after != expected_baseline:
                raise RuntimeError('Old five namespaces changed')
            counts = conn.execute("""SELECT count(*),count(*) FILTER (WHERE catalog_product_id IS NOT NULL)
                FROM source_products WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami')""").fetchone()
            status = conn.execute("""SELECT s.enabled,bool_or(o.active),count(o.id)
                FROM suppliers s JOIN offers o ON o.supplier_id=s.id WHERE s.code='kami' GROUP BY s.enabled""").fetchone()
            duplicates = conn.execute("""SELECT count(*) FROM (SELECT external_id FROM source_products
                WHERE supplier_id=(SELECT id FROM suppliers WHERE code='kami') GROUP BY external_id HAVING count(*)>1) t""").fetchone()[0]
            if counts != (len(cards), 0) or status != (False, False, len(cards)) or duplicates:
                raise RuntimeError('KAMI isolation/count/duplicate invariant failed')
            ledger.update(status='LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY',
                applied_source_rows=counts[0], baseline_preserved=True, duplicates=0,
                supplier_disabled=True, offers_inactive=True, canonical_links=0,
                full_live_RUN2='NOT_STARTED', original_RC_connections=0)
            ledger.update(full_saved_replay_rows=len(cards), full_saved_replay_hashes=replay_before,
                          full_saved_replay_exact_noop=True)
            write_checkpoint(ledger_path, ledger)
            print(json.dumps({k:v for k,v in ledger.items() if k != 'applied'}))
        finally:
            repo.close()


if __name__ == '__main__':
    main(None)
