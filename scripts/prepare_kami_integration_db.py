"""One-time authorized new KAMI target restore. Never connects to original RC.

No reuse, no overwrites, no initdb against an existing directory. Secrets remain
in a private LOCALAPPDATA directory and are never printed or committed.
"""
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import psycopg
from psycopg import sql
from psycopg.adapt import Loader

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/DATABASE'
BIN = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
DATA = ROOT.parent / 'universal_supplier_kami_20261005_pg17_data'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/kami-rc-20261005'
PORT = 55451
DB = 'universal_supplier_kami_rc'
DUMP_SHA = '27337c8a8e0b09924df8a2e160aa622c15e463ec8934614e6743dc2508caf8cc'


class RawPathLoader(Loader):
    def load(self, data):
        return bytes(data)


def run(binary, args, log):
    with (OUT / log).open('wb') as out:
        result = subprocess.run([str(BIN / binary), *map(str, args)], stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{binary} failed; inspect {log}; no automatic retry/restore')


def verify_restored(evidence, kwargs):
    # Windows PostgreSQL can expose a system-path GUC in the OS ANSI encoding
    # even when the database is UTF8. Get raw bytes instead of globally changing
    # client_encoding or loosening path equality.
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute('BEGIN READ ONLY')
        identity = conn.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
        if identity != evidence['system_identifier']:
            raise RuntimeError('Target system identity changed')
        counts = {t: conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]
                  for t in ['suppliers', 'source_products', 'offers', 'product_matches', 'catalog_products',
                            'catalog_offer_selection', 'supplier_http_captures', 'offer_commercial_observations', 'schema_migrations']}
        suppliers = dict(conn.execute('SELECT s.code,count(p.id) FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id GROUP BY s.code').fetchall())
        database, port = conn.execute('SELECT current_database(), inet_server_port()').fetchone()
        with conn.cursor() as path_cursor:
            # Cursor-local loader only. Canonical/source Unicode data continue
            # to use normal UTF8 loaders everywhere else.
            path_cursor.adapters.register_loader('text', RawPathLoader)
            raw_path = path_cursor.execute("SELECT current_setting('data_directory')").fetchone()[0]
        conn.execute('ROLLBACK')
    try:
        actual_path = raw_path.decode('utf-8')
        path_encoding = 'utf-8'
    except UnicodeDecodeError:
        actual_path = raw_path.decode('cp1251')
        path_encoding = 'cp1251_windows_guc'
    expected = dict(suppliers=5, source_products=4407, offers=4407, product_matches=2890,
                    catalog_products=59427, catalog_offer_selection=121, supplier_http_captures=5104,
                    offer_commercial_observations=5104, schema_migrations=15)
    if counts != expected or suppliers != dict(partner_st=1225, optimum=1353, intervesp=1642, beka_mak=109, beka_mak_tr=78):
        raise RuntimeError('Restored baseline counts mismatch; do not ingest')
    if database != DB or port != PORT or Path(actual_path).resolve() != DATA.resolve():
        raise RuntimeError('Restored runtime path/port mismatch')
    if hashlib.sha256((ROOT / 'release/database/universal_supplier_rc_final.dump').read_bytes()).hexdigest() != DUMP_SHA:
        raise RuntimeError('Baseline dump unexpectedly changed')
    from universal_supplier.control_plane.final_release_store import FinalReleaseStore
    baseline = FinalReleaseStore(str(ROOT / 'release/FINAL_RC_2026-10-02'),
                                'e1d20a074ced5783a149ce9eaed1f1e01d33dff80059c251a13683dd93e7dbca').page()['summary']
    evidence.update(status='LIVE_VERIFIED_KAMI_ISOLATED_BASELINE', counts=counts, namespaces=suppliers,
                    accepted_proposal_summary=baseline, data_directory_decoding=path_encoding,
                    canonical_assignments_written=False, kami_ingestion_started=False)
    (OUT / 'RESTORE_VERIFICATION.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    print(json.dumps(evidence, indent=2))


def main():
    if sys.argv[1:] == ['--verify-only']:
        evidence = json.loads((OUT / 'RESTORE_VERIFICATION.json').read_text(encoding='utf-8'))
        if (evidence['database'] != DB or evidence['port'] != PORT
                or evidence['dump_sha256'] != DUMP_SHA or evidence['data_directory'] != str(DATA)):
            raise RuntimeError('Saved exact new-target metadata mismatch')
        password = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
        verify_restored(evidence, dict(host='127.0.0.1', port=PORT, user='kami_admin', password=password, dbname=DB, connect_timeout=5))
        return
    if sys.argv[1:]:
        raise RuntimeError('Unknown argument')
    version = subprocess.check_output([str(BIN / 'postgres.exe'), '--version']).decode().strip()
    if not version.endswith('17.11'):
        raise RuntimeError('PostgreSQL 17.11 required')
    dump = ROOT / 'release/database/universal_supplier_rc_final.dump'
    if hashlib.sha256(dump.read_bytes()).hexdigest() != DUMP_SHA:
        raise RuntimeError('Immutable baseline dump digest mismatch')
    if DATA.exists() or PRIVATE.exists():
        raise RuntimeError('Target/private directory exists: no overwrite or reinitialization')
    check = subprocess.run(['powershell', '-NoProfile', '-Command', f"$ErrorActionPreference='Stop'; @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq {PORT}).Count"], capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != '0':
        raise RuntimeError('Get-NetTCPConnection cannot establish free target port')
    netstat = subprocess.check_output(['netstat', '-ano', '-p', 'tcp'], text=True)
    if any(f':{PORT} ' in row and 'LISTENING' in row for row in netstat.splitlines()):
        raise RuntimeError('netstat target port occupied')
    OUT.mkdir(parents=True, exist_ok=True); PRIVATE.mkdir(parents=True)
    admin, app = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    initpw = PRIVATE / 'init-password'; initpw.write_text(admin + '\n', encoding='utf-8')
    run('initdb.exe', ['-D', DATA, '-U', 'kami_admin', '--encoding=UTF8', '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--pwfile', initpw], 'initdb.log')
    config = {'DB_HOST': '127.0.0.1', 'DB_PORT': str(PORT), 'DB_NAME': DB, 'DB_USER': 'kami_app',
              'DB_PASSWORD': app, 'DB_SSLMODE': 'disable', 'SCHEDULER_ENABLED': 'NO',
              'STERBRUST_WRITE_ENABLE': 'NO', 'ESOL_WRITE_ENABLE': 'NO'}
    (PRIVATE / 'runtime.json').write_text(json.dumps(config), encoding='utf-8')
    (PRIVATE / 'pgpass.conf').write_text(f'127.0.0.1:{PORT}:*:kami_admin:{admin}\n', encoding='utf-8')
    os.environ['PGPASSFILE'] = str(PRIVATE / 'pgpass.conf')
    run('pg_ctl.exe', ['start', '-D', DATA, '-l', OUT / 'postgres.log', '-o', f'-h 127.0.0.1 -p {PORT}', '-w', '-t', '30'], 'start.log')
    kwargs = dict(host='127.0.0.1', port=PORT, user='kami_admin', password=admin, dbname='postgres', connect_timeout=5)
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        identity = conn.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
        if identity in {'7691270601420084116', '7692081906172255244'}:
            raise RuntimeError('Wrong target identity')
        conn.execute(sql.SQL('CREATE ROLE kami_app LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}').format(sql.Literal(app)))
        conn.execute(sql.SQL('CREATE DATABASE {} OWNER kami_app').format(sql.Identifier(DB)))
    evidence = {'host': '127.0.0.1', 'port': PORT, 'database': DB, 'owner': 'kami_app',
                'system_identifier': identity, 'postgres_version': version, 'data_directory': str(DATA),
                'dump_sha256': DUMP_SHA, 'status': 'NEW_TARGET_PRE_RESTORE', 'old_rc_connections': 0}
    (OUT / 'RESTORE_VERIFICATION.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    run('pg_restore.exe', ['-h', '127.0.0.1', '-p', PORT, '-U', 'kami_admin', '-d', DB,
                          '--role', 'kami_app', '--no-owner', '--no-privileges', '--exit-on-error',
                          '--single-transaction', dump], 'restore.log')
    kwargs['dbname'] = DB
    verify_restored(evidence, kwargs)


if __name__ == '__main__':
    main()
