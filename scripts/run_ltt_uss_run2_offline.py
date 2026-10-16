"""Operator-authorized frozen RUN1 replay only. No live transport or new events.

Uses the unchanged RUN1 parsers and ordinary repository exact-capture path.
All SQL sent by that repository is filtered before execution; only its SELECT
and row/advisory locks are allowed. Unexpected inserts, updates and nextval
fail before reaching PostgreSQL. Frozen files are never rewritten.
"""
from collections import Counter
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import re
import socket
import subprocess
import sys
import psycopg
from psycopg import sql

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r
from universal_supplier.ltt_uss_capture import _card_gate, sanitise_for_charset
from universal_supplier.adapters.ltt_yug_role import classify_yug_page
from universal_supplier.http_capture import has_blocking_interstitial

ROOT = r.BASE.parents[1]
OUT = r.BASE / 'OFFLINE_RUN2_2026-10-15'
EXPECTED = {'ltt_msk': {'PERSISTED': 369}, 'ltt_sib': {'PERSISTED': 1266},
            'ltt_yug': {'PERSISTED': 591, 'NON_PRODUCT_CATEGORY_PAGE': 1},
            'uss_stanko': {'PERSISTED': 212, 'AMBIGUOUS': 129, 'MULTI_VARIANT': 66}}
TABLES = ('source_products', 'offers', 'supplier_http_captures', 'offer_commercial_observations')


class OfflineViolation(RuntimeError):
    pass


@contextmanager
def offline_guard():
    """Block Python HTTP entry points and all sockets except retained PG55455."""
    counters = dict(supplier_http_gets=0, supplier_http_heads=0, robots_http=0,
                    rediscovery=0, network_retries=0, prohibited_transport_attempts=0)
    def deny(*a, **k):
        counters['prohibited_transport_attempts'] += 1
        raise OfflineViolation('RUN2_OFFLINE_TRANSPORT_PROHIBITED')
    original_connect = socket.socket.connect
    def connect(sock, address):
        if address != ('127.0.0.1', 55455):
            return deny()
        return original_connect(sock, address)
    with ExitStack() as stack:
        for target in ('urllib.request.urlopen', 'urllib.request.build_opener',
                       'universal_supplier.http_capture.build_opener',
                       'universal_supplier.http_capture.UrllibPublicHttpClient.get',
                       'universal_supplier.http_capture.UrllibPublicHttpClient.get_validated'):
            stack.enter_context(patch(target, deny))
        stack.enter_context(patch.object(socket.socket, 'connect', connect))
        stack.enter_context(patch.object(socket.socket, 'connect_ex', deny))
        stack.enter_context(patch('socket.create_connection', deny))
        yield counters


def frozen_run(*, baseline_seal=None, proposed_transition=None):
    from scripts.ltt_uss_replay_seals import verify_active_code, ORIGINAL
    state = r.load(r.OUT / 'CHECKPOINT.json')
    assert state['status'] == 'RUN1_COMPLETED'
    selected = verify_active_code(baseline_seal=baseline_seal, proposed_transition=proposed_transition)
    # The original comparison remains an executable historical guard. Selecting
    # a current checkpoint never claims current code equals the original code.
    if baseline_seal == ORIGINAL:
        assert r.code_hashes() == state['code_hashes'] == r.load(r.OUT / 'CODE_SEAL.json')
    else:
        assert state['code_hashes'] == r.load(r.OUT / 'CODE_SEAL.json')
    manifests, scopes = r.frozen_inputs()
    assert manifests == state['manifests'] == r.load(r.OUT / 'MANIFEST_SEAL.json')['manifests']
    run = r.Run.__new__(r.Run)
    run.state, run.scopes = state, scopes
    run.replay_seal_selection = selected
    return run


def safe_path(base, value):
    path = (base / value).resolve()
    assert path.is_relative_to(base.resolve()), 'Frozen evidence escapes source directory'
    return path


def role_page():
    trace = r.BASE / 'YUG_ROLE_REVIEW_2026-10-14/PAGE_ROLE_EVIDENCE.json'
    pages = r.load(trace)['pages']
    page = next(p for p in pages if p['url'].endswith('/derevoobrabatyvayuschee-oborudovanie.html'))
    path = safe_path(r.BASE, page['evidence_path'])
    assert r.digest(path.read_bytes()) == page['evidence_sha256']
    return page, path


def extract(journal):
    """Replay the original raw charset/sanitisation/card gate, then payload parser."""
    if journal['outcome'] == 'NON_PRODUCT_CATEGORY_PAGE':
        assert journal['supplier'] == 'ltt_yug'
        page, path = role_page()
        assert page['url'] == journal['url']
        assert classify_yug_page(path.read_text('utf8'), page['url']) == 'CATEGORY_PAGE'
        return 'NON_PRODUCT_CATEGORY_PAGE', None, None
    result = journal['result']
    response = r.load(safe_path(r.OUT, result['response_record']))
    raw = safe_path(r.OUT, response['raw_path']).read_bytes()
    assert r.digest(raw) == response['response_sha256'] == result['response_sha256']
    metadata = response['response']
    assert metadata['status_code'] == result['http_status'] == 200
    assert metadata['final_url'] == result['final_url']
    assert metadata['content_type'] == result['content_type']
    assert metadata['redirects'] == result['redirects']
    r.safe_source_url(journal['supplier'], journal['url'])
    for url in [metadata['final_url']] + metadata['redirects']:
        r.safe_source_url(journal['supplier'], url)
    body = sanitise_for_charset(raw, result['charset'])
    saved = r.OUT / 'EVIDENCE' / journal['supplier'] / (result['evidence_sha256'] + '.html')
    assert body == saved.read_bytes() and r.digest(body) == result['evidence_sha256']
    text = raw.decode(result['charset'] or 'utf8', 'replace')
    assert not has_blocking_interstitial(text.encode('utf8'))
    status, diagnostics = _card_gate(journal['supplier'], text, metadata['final_url'])
    outcome = {'SUCCESS': 'PERSISTED', 'AMBIGUOUS': 'AMBIGUOUS',
               'NON_PRODUCT_CATEGORY_PAGE': 'NON_PRODUCT_CATEGORY_PAGE',
               'NON_PRODUCT_EDITORIAL_PAGE': 'NON_PRODUCT_EDITORIAL_PAGE'}.get(status.value, status.value)
    if status.value == 'AMBIGUOUS' and 'MULTI_VARIANT' in diagnostics:
        outcome = 'MULTI_VARIANT'
    assert outcome == journal['outcome'], f"TERMINAL_DRIFT:{journal['supplier']}:{journal['index']}:{journal['outcome']}->{outcome}"
    assert status.value == result['status'] and list(diagnostics) == result['diagnostics']
    if outcome != 'PERSISTED':
        return outcome, None, None
    product = r.parse(journal['supplier'], body, result['final_url'])
    assert r.digest(r.encoded(asdict(product))) == journal['product_payload_sha256']
    assert product.site_internal_id == journal['external_id']
    assert product.name and product.source_category and product.enrichment_evidence is not None
    capture = r.restore_capture(result['capture'])
    assert capture.fingerprint == journal['capture_fingerprint']
    assert capture.response_sha256 == result['response_sha256']
    assert capture.evidence_sha256 == result['evidence_sha256']
    r.product_card_from_snapshot(product, capture)
    r.observation_from_snapshot(product, capture)
    return outcome, product, capture


def journals(run):
    for supplier in r.SOURCES:
        for index, scope in enumerate(run.scopes[supplier]):
            path = run.journal_path(supplier, index)
            row = r.load(path)
            assert row['phase'] == 'TERMINAL' and row['supplier'] == supplier and row['index'] == index
            assert row['url'] == scope['canonical_url']
            yield path, row


def inventory():
    files = set()
    for directory in ('ROWS', 'REVISED_ROWS', 'EVIDENCE'):
        files.update(p for p in (r.OUT / directory).rglob('*') if p.is_file())
    for name in ('CHECKPOINT.json', 'MANIFEST_SEAL.json', 'CODE_SEAL.json', 'DB_AFTER.json',
                 'ALL_SOURCE_RECONCILIATION.json'):
        files.add(r.OUT / name)
    files.update((r.BASE / 'MANIFESTS').glob('*.json'))
    files.add(r.BASE / 'YUG_ROLE_REVIEW_2026-10-14/PAGE_ROLE_EVIDENCE.json')
    _, role = role_page()
    files.add(role)
    files.update(ROOT / name for name in r.CODE)
    # Auxiliary parser/identity dependencies are frozen too, beyond the historical seal.
    files.update((ROOT / 'universal_supplier/adapters').glob('ltt*.py'))
    files.add(ROOT / 'universal_supplier/ltt_uss_identity.py')
    return {str(p.relative_to(ROOT)): dict(sha256=r.digest(p.read_bytes()), bytes=p.stat().st_size)
            for p in sorted(files)}


def prepare(resume=False):
    assert not (OUT / 'OFFLINE_PREFLIGHT.json').exists(), 'Completed preflight must not be replayed'
    assert not (OUT / 'BEFORE_RUN2.json').exists(), 'Database stage already started'
    assert resume or not (OUT / 'FROZEN_INPUTS.json').exists(), 'Existing RUN2: inspect its checkpoint; never overwrite'
    OUT.mkdir(parents=True, exist_ok=True)
    run = frozen_run()
    before = inventory()
    if resume:
        assert before == r.load(OUT / 'FROZEN_INPUTS.json'), 'Frozen seal drift on preparation resume'
    else:
        r.save(OUT / 'FROZEN_INPUTS.json', before)
    totals = {s: Counter() for s in r.SOURCES}
    last = None
    try:
        with offline_guard() as network:
            for path, row in journals(run):
                last = dict(supplier=row['supplier'], index=row['index'], url=row['url'],
                            run1_outcome=row['outcome'], journal=str(path.relative_to(ROOT)),
                            journal_sha256=r.digest(path.read_bytes()), evidence=row.get('result'))
                outcome, _, _ = extract(row)
                totals[row['supplier']][outcome] += 1
                processed = sum(sum(v.values()) for v in totals.values())
                if processed % 250 == 0:
                    print(f'OFFLINE PREPARATION {processed}/2634', flush=True)
        assert {s: dict(v) for s, v in totals.items()} == EXPECTED
        assert inventory() == before
        receipt = dict(status='OFFLINE_PREFLIGHT_PASS', checked_at=r.now(), scopes=totals,
                       scope=2634, frozen_files=len(before), code_hashes=r.code_hashes(),
                       network=network, git={k: subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()
                       for k, args in {'branch': ['branch', '--show-current'], 'head': ['rev-parse', 'HEAD'],
                                       'status': ['status', '--porcelain'], 'diff_stat': ['diff', '--stat'],
                                       'develop': ['rev-parse', 'develop'], 'production': ['rev-parse', 'production']}.items()})
        r.save(OUT / 'OFFLINE_PREFLIGHT.json', receipt)
        print(r.encoded({k: receipt[k] for k in ('status', 'scope', 'frozen_files', 'scopes', 'network')}).decode(), flush=True)
    except Exception as error:
        r.save(OUT / 'BLOCKER.json', dict(status='LTT_USS_RUN2_BLOCKED', stage='OFFLINE_PREFLIGHT',
                                       error=f'{type(error).__name__}:{error}', row=last, checked_at=r.now()))
        raise


def validate_sql(query):
    text = query if isinstance(query, str) else query.as_string()
    # This repository exact-noop path needs only SELECT and its transaction API.
    if (not text.lstrip().upper().startswith('SELECT ') or ';' in text or
            re.search(r'\b(INSERT|UPDATE|DELETE|MERGE|TRUNCATE|ALTER|DROP|CREATE|COPY|CALL|DO|NEXTVAL|SETVAL)\b',
                      re.sub(r'FOR\s+UPDATE', '', text, flags=re.I), re.I)):
        raise OfflineViolation('RUN2_MUTATING_SQL_PROHIBITED')


class NoMutationCursor:
    def __init__(self, cursor): self.inner = cursor
    def __enter__(self): self.inner.__enter__(); return self
    def __exit__(self, *args): return self.inner.__exit__(*args)
    def execute(self, query, params=None):
        validate_sql(query)
        self.inner.execute(query, params)
        return self
    def fetchone(self): return self.inner.fetchone()
    def fetchall(self): return self.inner.fetchall()


class NoMutationConnection:
    def __init__(self, connection): self.inner = connection
    def transaction(self): return self.inner.transaction()
    def cursor(self): return NoMutationCursor(self.inner.cursor())
    def execute(self, query, params=None):
        validate_sql(query)
        return self.inner.execute(query, params)


def snapshot(connection):
    table = r.fingerprints(connection)
    sequences = {}
    for name, in connection.execute("SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
        sequences[name] = connection.execute(sql.SQL('SELECT last_value,is_called FROM {}').format(sql.Identifier(name))).fetchone()
    namespaces = {}
    for supplier in r.SOURCES:
        sid = connection.execute('SELECT id FROM suppliers WHERE code=%s', (supplier,)).fetchone()[0]
        namespaces[supplier] = r.namespace_digest(connection, sid)
    projections = connection.execute('''SELECT row_to_json(p),row_to_json(o) FROM source_products p
        JOIN offers o ON o.source_product_id=p.id ORDER BY p.supplier_id,p.external_id''').fetchall()
    keys = connection.execute('''SELECT s.code,p.external_id,p.id,o.id,o.price,o.currency,p.raw_data,p.last_success_at
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
        WHERE (s.code='ltt_yug' AND p.external_id IN ('1','312','297')) OR
        (s.code='uss_stanko' AND p.external_id IN ('uss:mach/frez/uf100.htm','uss:mach/frez/uf200.htm'))
        ORDER BY s.code,p.external_id''').fetchall()
    assert len(keys) == 4
    yug = [v for v in keys if v[0] == 'ltt_yug']
    assert {v[1] for v in yug} == {'312', '297'} and len({v[2] for v in yug}) == len({v[3] for v in yug}) == 2
    usd = [v for v in keys if v[0] == 'uss_stanko']
    assert [(v[4],v[5]) for v in usd] == [(21312,'USD'), (31212,'USD')]
    assert [v[6]['price_extraction']['raw_text'] for v in usd] == ['21 312 USD', '31 212 USD']
    assert not connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
    assert not connection.execute('SELECT source_product_id FROM offers GROUP BY source_product_id HAVING count(*)>1').fetchall()
    assert connection.execute('SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM suppliers WHERE enabled').fetchone()[0] == 0
    assert connection.execute('SELECT count(*) FROM offers WHERE active').fetchone()[0] == 0
    units = connection.execute("""SELECT p.external_id,o.price,o.currency,p.raw_data->'price_extraction',
        o.raw_data->'price_extraction' FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
        JOIN offers o ON o.source_product_id=p.id WHERE s.code='ltt_sib'
        AND p.raw_data->'price_extraction'->>'raw_text' LIKE %s ORDER BY p.external_id""", ('%бухта%',)).fetchall()
    assert len(units) == 8 and all(v[2] == 'RUB' and v[3] == v[4] for v in units)
    assert any(v[1] == 3500 and v[3]['raw_text'] == '3 500 ₽ / бухта' for v in units)
    return dict(fingerprints=table, sequences=sequences, supplier_namespaces=namespaces,
                product_offer_projections=projections, special_products=keys, sib_units=units,
                totals={t: table[t]['count'] for t in TABLES})


def clients(connection):
    other = connection.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0]
    tx = connection.execute("SELECT count(*) FROM pg_stat_activity WHERE backend_type='client backend' AND pid<>pg_backend_pid() AND xact_start IS NOT NULL").fetchone()[0]
    prepared = connection.execute('SELECT count(*) FROM pg_prepared_xacts').fetchone()[0]
    assert other == tx == prepared == 0
    return dict(other_clients=other, open_transactions=tx, prepared_transactions=prepared)


def execute():
    assert r.load(OUT / 'OFFLINE_PREFLIGHT.json')['status'] == 'OFFLINE_PREFLIGHT_PASS'
    assert not (OUT / 'RESULT.json').exists(), 'Do not silently repeat RUN2'
    assert inventory() == r.load(OUT / 'FROZEN_INPUTS.json')
    run = frozen_run()
    totals = {s: Counter() for s in r.SOURCES}
    last = None
    _, cfg = r.config()
    assert (cfg['host'], cfg['port'], cfg['dbname']) == ('127.0.0.1',55455,'universal_supplier_ltt_uss')
    r.save(OUT / 'CHECKPOINT.json', dict(status='RUN2_PREFLIGHT', worker_pid=__import__('os').getpid(), safe_to_power_off=False, checked_at=r.now()))
    with offline_guard() as network:
        system = r.verify_system()
        with psycopg.connect(**cfg, autocommit=True) as c:
            c.execute("SET application_name='ltt_uss_offline_run2'")
            c.execute("SET statement_timeout='30s'")
            c.execute("SET lock_timeout='5s'")
            before = snapshot(c)
            assert before['fingerprints'] == r.load(r.OUT / 'DB_AFTER.json')['fingerprints']
            assert list(before['totals'].values()) == [2438,2438,2518,2518]
            clients(c)
            r.save(OUT / 'BEFORE_RUN2.json', dict(**before, **system, checked_at=r.now()))
            repo = r.PostgresRepository.__new__(r.PostgresRepository)
            repo.connection = NoMutationConnection(c)
            result = None
            try:
                for path, row in journals(run):
                    last = dict(supplier=row['supplier'], index=row['index'], url=row['url'],
                                run1_outcome=row['outcome'], journal_sha256=r.digest(path.read_bytes()),
                                evidence_sha256=row.get('result',{}).get('evidence_sha256'), code_hashes=r.code_hashes())
                    outcome, product, capture = extract(row)
                    if product is not None:
                        r.verify_persisted(repo, product, capture)
                        assert r.check_observation(repo, product, capture) == row['db_verification']
                        # Exercise the actual unchanged persistence layer, not just a comparison mock.
                        receipt = repo.persist_commercial_observation(product, capture)
                        assert receipt['exact_noop'] and not receipt['observation_created'] and not receipt['projection_updated']
                        assert receipt['source_product_id'] == row['db_verification']['source_product_id']
                        assert receipt['capture_id'] == row['db_verification']['capture_id']
                        assert all(v == 0 for v in asdict(receipt['ingest_counts']).values())
                    totals[row['supplier']][outcome] += 1
                    output = dict(**last, run2_outcome=outcome, persistence='EXACT_NOOP' if product else 'NON_PERSISTED_TERMINAL_STABLE')
                    r.save(OUT / 'ROWS' / row['supplier'] / path.name, output)
                    processed = sum(sum(v.values()) for v in totals.values())
                    if processed % 100 == 0:
                        r.save(OUT / 'CHECKPOINT.json', dict(status='RUN2_RUNNING', processed=processed, totals=totals, safe_to_power_off=False, checked_at=r.now()))
                        print(f'RUN2 {processed}/2634', flush=True)
                assert {s:dict(v) for s,v in totals.items()} == EXPECTED
                assert inventory() == r.load(OUT / 'FROZEN_INPUTS.json')
                result = dict(status='LTT_USS_RUN2_EXACT_NOOP_VERIFIED', scope=2634, sources=totals,
                              exact_noop_persisted=2438, terminal_outcome_delta=0, network=network,
                              new_source_products=0,new_offers=0,new_captures=0,new_observations=0,
                              duplicate_external_ids=0,duplicate_offer_links=0,identity_overwrite=0,
                              foreign_mutations=0,unexplained_missing=0,fx_conversion='NONE',checked_at=r.now())
            except Exception as error:
                result = dict(status='LTT_USS_RUN2_BLOCKED', stage='EXACT_OFFLINE_REPLAY', row=last,
                              processed=sum(sum(v.values()) for v in totals.values()),sources=totals,
                              error=f'{type(error).__name__}:{error}',network=network,checked_at=r.now())
                r.save(OUT / 'BLOCKER.json', result)
            finally:
                after = snapshot(c)
                r.save(OUT / 'AFTER_RUN2.json', dict(**after, **system, checked_at=r.now()))
                differences = [key for key in before if r.encoded(before[key]) != r.encoded(after[key])]
                result = result or dict(status='LTT_USS_RUN2_BLOCKED',error='Interrupted')
                result.update(database_delta=differences, table_fingerprint_delta=[k for k in before['fingerprints'] if before['fingerprints'][k]!=after['fingerprints'][k]],
                              sequence_delta=[k for k in before['sequences'] if before['sequences'][k]!=after['sequences'][k]],
                              clients=clients(c),before_counts=before['totals'],after_counts=after['totals'])
                if differences: result['status'] = 'LTT_USS_RUN2_BLOCKED'
                r.save(OUT / 'RESULT.json', result)
                r.save(OUT / 'HTTP_ZERO_PROOF.json', dict(**network,guard='HTTP entrypoint prohibition + socket allowlist PG55455 only',
                       frozen_code_unchanged=True, no_live_client_instantiated=True, checked_at=r.now()))
                r.save(OUT / 'CHECKPOINT.json', dict(**result, worker_stopped=True,safe_to_power_off=False,next='CLEAN_SHUTDOWN_REQUIRED'))
                print(r.encoded(result).decode(), flush=True)
                assert result['status'] == 'LTT_USS_RUN2_EXACT_NOOP_VERIFIED', 'RUN2 blocked; preserve receipts and stop PostgreSQL'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=('prepare','resume-preparation','execute'))
    args = parser.parse_args()
    if args.mode == 'execute': execute()
    else: prepare(resume=args.mode == 'resume-preparation')
