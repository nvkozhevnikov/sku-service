"""One bounded saved-evidence canary, with mandatory own-cluster shutdown."""
from dataclasses import asdict
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
import argparse
import hashlib
import json
import os
import socket
import subprocess
import sys
import time
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.adapters.ltt import parse_ltt_modx_detail
from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail
from universal_supplier.commercial_persistence import HttpCapture, normalize_availability
from universal_supplier.ltt_uss_capture import sanitise_for_charset
from universal_supplier.http_capture import UrllibPublicHttpClient
from universal_supplier.postgres import PostgresConfig, PostgresRepository

BASE = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT = BASE / 'OFFLINE_CANARY_15MIN'
BIN = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
DATA = BASE / 'PRIVATE/pg17'
ALLOWED = {'suppliers', 'source_products', 'offers', 'supplier_http_captures', 'offer_commercial_observations'}


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.partial')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', 'utf-8')
    temporary.replace(path)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True, timeout=5).strip()


def fingerprints(connection):
    names = [r[0] for r in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    result = {}
    for name in names:
        rows = connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} AS t').format(sql.Identifier(name))).fetchall()
        values = sorted(row[0] for row in rows)
        result[name] = dict(count=len(values), sha256=hashlib.sha256('\n'.join(values).encode('utf-8')).hexdigest())
    return result


def prepared_rows():
    audit = json.loads((BASE / 'AUDIT/SOURCE_AUDIT_SUMMARY.json').read_text('utf-8'))
    rows = []
    for supplier, label, pid, value, availability in [
        ('ltt_msk', 'machine_numeric_price', '65', '2693682', 'InStock'),
        ('ltt_msk', 'spare_accessory', '1813', '2800', 'InStock'),
        ('ltt_sib', 'machine_por_outofstock', '431', None, 'OutOfStock'),
        ('ltt_sib', 'machine_numeric_mb203c', '2271', '221925', 'InStock'),
        ('uss_stanko', 'dmtg_detail', 'uss:dmtg/5-osevye-obrabatyvayushchie-tsentry/vdwa50.htm', None, 'PreOrder'),
    ]:
        entry = next(e for e in audit['ledger'] if e['source'] == supplier and e['label'] == 'sample:' + label)
        raw = (BASE / 'AUDIT/RAW' / (supplier + '__' + label + '.html')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == entry['sha256']
        charset = 'cp1251' if supplier == 'uss_stanko' else 'utf-8'
        evidence = sanitise_for_charset(raw, charset)
        digest = hashlib.sha256(evidence).hexdigest()
        evidence_path = OUT / 'EVIDENCE' / supplier / (digest + '.html')
        evidence_path.parent.mkdir(parents=True, exist_ok=True)
        evidence_path.write_bytes(evidence)
        capture = HttpCapture(entry['url'], entry['final_url'], entry['http_status'], entry['content_type'],
                              datetime.fromisoformat(audit['recorded_at']), entry['sha256'], digest,
                              f"capture://{supplier}/{digest}.html", redirects=tuple(entry['redirects']),
                              diagnostics=('OFFLINE_SAVED_AUDIT', 'time_basis:audit_completion_not_individual_request', 'original_charset:' + charset))
        product = (parse_uss_stanko_detail(evidence.decode('utf-8'), source_url=entry['final_url']) if supplier == 'uss_stanko'
                   else parse_ltt_modx_detail(evidence.decode('utf-8'), supplier=supplier, source_url=entry['final_url']))
        assert product.site_internal_id == pid and product.price.current_price == (Decimal(value) if value else None)
        assert product.availability == availability
        rows.append((product, capture))
    twin = json.loads((BASE / 'TWIN_PREFLIGHT/RESULT.json').read_text('utf-8'))
    for row in twin['rows']:
        assert row['source_id'] in {'312', '297'}
        raw = (BASE / 'TWIN_PREFLIGHT/EVIDENCE/PRIVATE_RAW/ltt_yug' / (row['response_sha256'] + '.bin')).read_bytes()
        evidence = (BASE / 'TWIN_PREFLIGHT/EVIDENCE/ltt_yug' / (row['evidence_sha256'] + '.html')).read_bytes()
        assert hashlib.sha256(raw).hexdigest() == row['response_sha256']
        assert hashlib.sha256(evidence).hexdigest() == row['evidence_sha256']
        product = parse_ltt_yug_detail(evidence.decode('utf-8'), source_url=row['final_url'])
        assert product.site_internal_id == row['source_id']
        assert product.price.current_price == Decimal(row['price']) and product.availability == row['availability']
        capture = HttpCapture(row['requested_url'], row['final_url'], 200, 'text/html; charset=utf-8',
                              datetime.fromisoformat(row['observed_at']), row['response_sha256'], row['evidence_sha256'],
                              row['evidence_ref'], diagnostics=('OFFLINE_SAVED_PREFLIGHT',))
        rows.append((product, capture))
    assert len(rows) == 7 and len({(p.supplier, p.site_internal_id) for p, _ in rows}) == 7
    return rows


def verify(connection, expected, before):
    actual = connection.execute("""SELECT s.code,p.external_id,p.id,o.id,o.price,o.old_price,o.currency,
        o.availability_normalized,b.price_state,b.price,b.availability_normalized,
        c.response_sha256,c.evidence_sha256,p.raw_data->'source_identity'->>'site_internal_id',s.enabled,o.active
        FROM suppliers s JOIN source_products p ON p.supplier_id=s.id JOIN offers o ON o.source_product_id=p.id
        JOIN offer_commercial_observations b ON b.source_product_id=p.id AND b.offer_id=o.id
        JOIN supplier_http_captures c ON c.id=b.capture_id ORDER BY s.code,p.external_id"""
    ).fetchall()
    assert len(actual) == len(expected)
    by_key = {(r[0], r[1]): r for r in actual}
    for product, capture in expected:
        r = by_key[(product.supplier, product.site_internal_id)]
        assert r[4] == product.price.current_price and r[5] == product.price.old_price
        assert (r[6] or '') == (product.price.currency or '')
        assert r[7] == normalize_availability(product.availability)
        assert r[8] == product.price.state.value and r[9] == product.price.current_price and r[10] == r[7]
        assert r[11].strip() == capture.response_sha256 and r[12].strip() == capture.evidence_sha256
        assert r[13] == product.site_internal_id and r[14] is False and r[15] is False
    a, b = by_key[('ltt_yug', '312')], by_key[('ltt_yug', '297')]
    assert a[2] != b[2] and a[3] != b[3] and a[4] == Decimal('4212548.06') and b[4] == Decimal('1905000')
    assert (a[7], b[7]) == ('in_stock', 'out_of_stock')
    duplicates = connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
    assert not duplicates
    after = fingerprints(connection)
    unexpected = [name for name in before if name not in ALLOWED and before[name] != after[name]]
    assert not unexpected
    counts = connection.execute("""SELECT s.code,count(DISTINCT p.id),count(DISTINCT p.external_id),
        count(DISTINCT o.id),count(DISTINCT o.source_product_id),count(DISTINCT b.id)
        FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id LEFT JOIN offers o ON o.source_product_id=p.id
        LEFT JOIN offer_commercial_observations b ON b.source_product_id=p.id GROUP BY s.code ORDER BY s.code"""
    ).fetchall()
    assert {r[0]: r[1] for r in counts} == {'ltt_msk': 2, 'ltt_sib': 2, 'ltt_yug': 2, 'uss_stanko': 1}
    assert all(r[1] == r[2] == r[3] == r[4] == r[5] for r in counts)
    return dict(per_supplier=[dict(supplier=r[0], source_products=r[1], distinct_external_id=r[2], offers=r[3], distinct_source_product_id=r[4], observations=r[5]) for r in counts],
                yug_312_present=True, yug_297_present=True, identity_collapse=False, duplicates=0, unexpected_table_mutations=unexpected,
                exact_db_rows=actual, before_fingerprints=before, after_fingerprints=after)


def main():
    args = argparse.ArgumentParser()
    args.add_argument('--started-at', required=True)
    start = datetime.fromisoformat(args.parse_args().started_at)
    def budget():
        if (datetime.now(timezone.utc) - start).total_seconds() >= 600:
            raise TimeoutError('T+10: stop new work, checkpoint and shutdown only')
    budget()
    OUT.mkdir(parents=True, exist_ok=True)
    if (OUT / 'CHECKPOINT.json').exists():
        raise RuntimeError('Canary checkpoint already exists; inspect before repeating')
    initial_refs = {name: git('rev-parse', 'refs/heads/' + name) for name in ('develop', 'production')}
    state = dict(captured_at=datetime.now(timezone.utc).isoformat(), session_started_at=start.isoformat(), runner_pid=os.getpid(),
                 status='PREFLIGHT', branch=git('branch', '--show-current'), head=git('rev-parse', 'HEAD'),
                 git_status_before=git('status', '--porcelain'), tests_already_known=dict(pass_tests=86, subtests=13, fail=0, error=0, skip=0),
                 supplier_http=0, run1_started=False, run2_started=False, full_qa_started=False,
                 sterbrust_writes=0, esol_writes=0, matching_writes=0, selection_writes=0, safe_to_power_off=False)
    assert state['branch'] == 'feature/universal-supplier-ltt-uss-ingestion' and state['head'] == '45052a6d21dae5e5f56e890f3a6e79466dcce733'
    manifest = json.loads((BASE / 'MANIFESTS/ltt_yug.json').read_text('utf-8'))
    assert len(manifest['candidates']) == 592 and manifest['counts']['suffix_exclusions'] == 0
    saved = json.loads((BASE / 'PRIVATE/connection.json').read_text('utf-8'))
    config = saved['runtime']
    assert (config['host'], config['port'], config['dbname'], config['user']) == ('127.0.0.1', 55455, 'universal_supplier_ltt_uss', 'ltt_uss_runtime')
    admin = dict(config, user='ltt_uss_admin', password=saved['admin_password'], connect_timeout=5, application_name='ltt_uss_15min_safety')
    repo = None
    started = False
    def no_http(*a, **k):
        raise RuntimeError('Supplier HTTP forbidden in offline canary')
    UrllibPublicHttpClient.get = no_http
    UrllibPublicHttpClient.get_validated = no_http
    try:
        expected = prepared_rows()
        budget()
        assert not (DATA / 'postmaster.pid').exists()
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 55455))
        started = True  # cleanup remains mandatory even if startup fails midway
        subprocess.run([str(BIN / 'pg_ctl.exe'), 'start', '-D', str(DATA), '-l', str(OUT / 'postgres.log'),
                        '-o', '-h 127.0.0.1 -p 55455', '-w', '-t', '30'], check=True, timeout=40)
        state['postgres_pid'] = int((DATA / 'postmaster.pid').read_text().splitlines()[0])
        with psycopg.connect(**admin, autocommit=True) as connection:
            connection.execute('SET default_transaction_read_only=on')
            version = connection.execute('SHOW server_version').fetchone()[0]
            system_id = str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
            assert version.startswith('17.11') and system_id == '7696153366151515704'
            assert connection.execute("SELECT count(*) FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND backend_type='client backend'").fetchone()[0] == 0
            state.update(db_system_identifier=system_id, postgres_version=version)
        budget()
        repo = PostgresRepository(PostgresConfig(**config))
        repo.connection.execute("SET statement_timeout='15s'")
        repo.connection.execute("SET lock_timeout='5s'")
        repo.connection.execute("SET application_name='ltt_uss_15min_offline_canary'")
        assert repo.gate()['runtime_role_superuser'] is False
        before = fingerprints(repo.connection)
        assert before['source_products']['count'] == before['offers']['count'] == before['suppliers']['count'] == 0
        state['exact_canary_rows'] = [dict(supplier=p.supplier, external_id=p.site_internal_id, title=p.name, source_url=p.source_url,
                                               price=str(p.price.current_price) if p.price.current_price else None,
                                               price_state=p.price.state.value, availability=p.availability,
                                               response_sha256=c.response_sha256, evidence_sha256=c.evidence_sha256,
                                               observed_at=c.observed_at.isoformat(), capture_diagnostics=c.diagnostics) for p,c in expected]
        save(OUT / 'CHECKPOINT.json', state)
        with repo.connection.transaction():
            for product, capture in expected:
                budget()
                repo.persist_commercial_observation(product, capture)
            state['db_verification'] = verify(repo.connection, expected, before)
            budget()
        # Verification after committed persistence is read-only.
        repo.connection.execute('SET default_transaction_read_only=on')
        state['db_verification'] = verify(repo.connection, expected, before)
        state['offline_canary_status'] = 'OFFLINE_PERSISTENCE_CANARY_VERIFIED'
        state['status'] = 'OFFLINE_PERSISTENCE_CANARY_VERIFIED'
    except Exception as error:
        state['status'] = 'OFFLINE_CANARY_BLOCKED_SAFE_SHUTDOWN_REQUIRED'
        state['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        if repo:
            repo.close()
        state['remaining'] = ['finish parser/kind/model coverage', 'live/full canary if required', 'RUN1', 'RUN2 offline',
                              'risk QA', 'read-only relationship/reconciliation proposals only (matching writes NOT authorized)', 'finalizer', 'feature-only commit/push']
        state['git_status_after'] = git('status', '--porcelain')
        state['refs_unchanged'] = {name:git('rev-parse', 'refs/heads/' + name) == sha for name,sha in initial_refs.items()}
        save(OUT / 'CHECKPOINT.json', state)
        (OUT / 'RECOVERY_NEXT.md').write_text('# Recovery / LTT + USS bounded offline canary\n\n'
            'Resume from CHECKPOINT.json. Do not reinitialize the existing PG17.11 cluster.\n'
            'Only own 127.0.0.1:55455 / universal_supplier_ltt_uss / system7696153366151515704.\n'
            'Seven saved-evidence rows planned: MSK2/SIB2/YUG2/USS1. Use checkpoint exact rows/counts to establish committed outcome.\n'
            'YUG312 and297 must remain independent. USS has only one unambiguous saved card; multi-variant cards stay excluded.\n'
            'Audit timestamp basis is audit completion, not a fabricated per-request capture time. No fresh supplier HTTP.\n'
            'Next: parser/kind/model review, live/full canary if required, RUN1, offlineRUN2, QA, read-only reconciliation proposals, finalizer, feature commit/push.\n'
            'No matching/selection writes, production, DB55452, KAMI, Sterbrust, ESOL, develop merge or scheduling.\n'
            'Do not repeat initial canary blindly: database may contain committed rows. Verify retained checkpoint and DB first.\n', 'utf-8')
        if started and (DATA / 'postmaster.pid').exists():
            with psycopg.connect(**admin, autocommit=True) as connection:
                connection.execute('SET default_transaction_read_only=on')
                others = connection.execute("SELECT pid,state,xact_start FROM pg_stat_activity WHERE pid<>pg_backend_pid() AND backend_type='client backend'").fetchall()
                state['clients_before_shutdown'] = others
                state['open_transactions_before_shutdown'] = sum(r[2] is not None for r in others)
            subprocess.run([str(BIN / 'pg_ctl.exe'), 'stop', '-D', str(DATA), '-m', 'fast', '-w', '-t', '30'], check=True, timeout=40)
            state['postgres_shutdown'] = 'CLEAN_SERVER_STOPPED'
        else:
            state['postgres_shutdown'] = 'NOT_RUNNING'
        with socket.socket() as probe:
            probe.settimeout(1)
            state['port_55455_listening_after'] = probe.connect_ex(('127.0.0.1',55455)) == 0
        state['postmaster_pid_file_after'] = (DATA / 'postmaster.pid').exists()
        control = subprocess.check_output([str(BIN / 'pg_controldata.exe'), str(DATA)], text=True, timeout=5)
        state['pg_controldata_shutdown_verified'] = any('Database cluster state:' in line and 'shut down' in line for line in control.splitlines())
        state['elapsed_seconds'] = round((datetime.now(timezone.utc)-start).total_seconds(),2)
        state['finished_at'] = datetime.now(timezone.utc).isoformat()
        state['safe_to_power_off'] = (not state['port_55455_listening_after'] and not state['postmaster_pid_file_after']
                                      and state['pg_controldata_shutdown_verified'] and state.get('open_transactions_before_shutdown',0)==0)
        save(OUT / 'CHECKPOINT.json', state)
        print(json.dumps({k:state.get(k) for k in ('status','offline_canary_status','db_system_identifier','postgres_shutdown','port_55455_listening_after','elapsed_seconds','safe_to_power_off')}, ensure_ascii=True))
    return 0 if state.get('offline_canary_status') == 'OFFLINE_PERSISTENCE_CANARY_VERIFIED' and state['safe_to_power_off'] else 1


if __name__ == '__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    raise SystemExit(main())
