"""One-time authorized isolated vekprom/intervesp target restore.

Never connects to any other running instance (RC, KAMI).  No reuse, no
overwrites, no initdb against an existing directory.  Secrets remain in a
private LOCALAPPDATA directory and are never printed or committed.
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

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08/DATABASE'
BIN = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
DATA = ROOT.parent / 'universal_supplier_vekprom_intervesp_20261008_pg17_data'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
PORT = 55452
DB = 'universal_supplier_vekprom_intervesp'
ADMIN = 'vekprom_intervesp_admin'
APP = 'vekprom_intervesp_app'
DUMP = ROOT.parent / 'sku-service-kami/release/database/universal_supplier_kami_final_2026-10-07.dump'
DUMP_SHA = '76b4f876001f148b3d01ab02132e772eaa0c552f39aac0b32852859382b401f5'
# Invariants that must survive from the RC baseline through the KAMI release.
NAMESPACE_MINIMUMS = dict(partner_st=1225, optimum=1353, intervesp=1642, beka_mak=109, beka_mak_tr=78)


def run(binary, args, log):
    with (OUT / log).open('wb') as out:
        result = subprocess.run([str(BIN / binary), *map(str, args)], stdout=out, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f'{binary} failed; inspect {log}; no automatic retry/restore')


def verify_restored(evidence, kwargs):
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        conn.execute('BEGIN READ ONLY')
        identity = conn.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
        if identity != evidence['system_identifier']:
            raise RuntimeError('Target system identity changed')
        counts = {t: conn.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(t))).fetchone()[0]
                  for t in ['suppliers', 'source_products', 'offers', 'product_matches', 'catalog_products',
                            'catalog_offer_selection', 'supplier_http_captures', 'offer_commercial_observations',
                            'schema_migrations']}
        suppliers = dict(conn.execute('SELECT s.code,count(p.id) FROM suppliers s '
                                      'LEFT JOIN source_products p ON p.supplier_id=s.id GROUP BY s.code').fetchall())
        database, port = conn.execute('SELECT current_database(), inet_server_port()').fetchone()
        conn.execute('ROLLBACK')
    if suppliers.get('intervesp') != 1642:
        raise RuntimeError('Intervesp baseline of 1642 products is a hard precondition; do not ingest')
    for code, minimum in NAMESPACE_MINIMUMS.items():
        if suppliers.get(code, 0) < minimum:
            raise RuntimeError(f'{code} baseline count regressed below {minimum}; do not ingest')
    if 'kami' not in suppliers or suppliers['kami'] <= 0:
        raise RuntimeError('KAMI namespace missing from the restored release baseline')
    if len(suppliers) != 6:
        raise RuntimeError(f'Expected exactly six namespaces, saw {sorted(suppliers)}')
    if database != DB or port != PORT:
        raise RuntimeError('Restored runtime port/database mismatch')
    evidence.update(status='LIVE_VERIFIED_ISOLATED_BASELINE', counts=counts, namespaces=suppliers,
                    canonical_assignments_written=False, vekprom_ingestion_started=False,
                    intervesp_upgrade_started=False)
    (OUT / 'RESTORE_VERIFICATION.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    summary = {k: evidence[k] for k in ('status', 'port', 'database', 'dump_sha256')}
    summary['namespaces'] = suppliers
    print(json.dumps(summary, ensure_ascii=False, indent=2))


def main():
    if sys.argv[1:] == ['--verify-only']:
        evidence = json.loads((OUT / 'RESTORE_VERIFICATION.json').read_text(encoding='utf-8'))
        if (evidence['database'] != DB or evidence['port'] != PORT
                or evidence['dump_sha256'] != DUMP_SHA or evidence['data_directory'] != str(DATA)):
            raise RuntimeError('Saved exact new-target metadata mismatch')
        password = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
        verify_restored(evidence, dict(host='127.0.0.1', port=PORT, user=ADMIN, password=password,
                                       dbname=DB, connect_timeout=5))
        return
    if sys.argv[1:]:
        raise RuntimeError('Unknown argument')
    version = subprocess.check_output([str(BIN / 'postgres.exe'), '--version']).decode().strip()
    if not version.endswith('17.11'):
        raise RuntimeError('PostgreSQL 17.11 required')
    if hashlib.sha256(DUMP.read_bytes()).hexdigest().lower() != DUMP_SHA:
        raise RuntimeError('Immutable baseline dump digest mismatch')
    if DATA.exists() or PRIVATE.exists():
        raise RuntimeError('Target/private directory exists: no overwrite or reinitialization')
    check = subprocess.run(['powershell', '-NoProfile', '-Command',
                            f"$ErrorActionPreference='Stop'; @(Get-NetTCPConnection -State Listen | Where-Object LocalPort -eq {PORT}).Count"],
                           capture_output=True, text=True)
    if check.returncode or check.stdout.strip() != '0':
        raise RuntimeError('Get-NetTCPConnection cannot establish free target port')
    netstat = subprocess.check_output(['netstat', '-ano', '-p', 'tcp'], text=True)
    if any(f':{PORT} ' in row and 'LISTENING' in row for row in netstat.splitlines()):
        raise RuntimeError('netstat target port occupied')
    OUT.mkdir(parents=True, exist_ok=True); PRIVATE.mkdir(parents=True)
    admin, app = secrets.token_urlsafe(36), secrets.token_urlsafe(36)
    initpw = PRIVATE / 'init-password'; initpw.write_text(admin + '\n', encoding='utf-8')
    run('initdb.exe', ['-D', DATA, '-U', ADMIN, '--encoding=UTF8', '--auth-host=scram-sha-256',
                       '--auth-local=scram-sha-256', '--pwfile', initpw], 'initdb.log')
    config = {'DB_HOST': '127.0.0.1', 'DB_PORT': str(PORT), 'DB_NAME': DB, 'DB_USER': APP,
              'DB_PASSWORD': app, 'DB_SSLMODE': 'disable', 'SCHEDULER_ENABLED': 'NO',
              'STERBRUST_WRITE_ENABLE': 'NO', 'ESOL_WRITE_ENABLE': 'NO'}
    (PRIVATE / 'runtime.json').write_text(json.dumps(config), encoding='utf-8')
    (PRIVATE / 'pgpass.conf').write_text(f'127.0.0.1:{PORT}:*:{ADMIN}:{admin}\n', encoding='utf-8')
    os.environ['PGPASSFILE'] = str(PRIVATE / 'pgpass.conf')
    run('pg_ctl.exe', ['start', '-D', DATA, '-l', OUT / 'postgres.log',
                       '-o', f'-h 127.0.0.1 -p {PORT}', '-w', '-t', '30'], 'start.log')
    kwargs = dict(host='127.0.0.1', port=PORT, user=ADMIN, password=admin, dbname='postgres', connect_timeout=5)
    with psycopg.connect(**kwargs, autocommit=True) as conn:
        identity = conn.execute('SELECT system_identifier::text FROM pg_control_system()').fetchone()[0]
        conn.execute(sql.SQL('CREATE ROLE {} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}')
                     .format(sql.Identifier(APP), sql.Literal(app)))
        conn.execute(sql.SQL('CREATE DATABASE {} OWNER {}').format(sql.Identifier(DB), sql.Identifier(APP)))
    evidence = {'host': '127.0.0.1', 'port': PORT, 'database': DB, 'owner': APP,
                'system_identifier': identity, 'postgres_version': version, 'data_directory': str(DATA),
                'dump_path': str(DUMP), 'dump_sha256': DUMP_SHA, 'status': 'NEW_TARGET_PRE_RESTORE'}
    (OUT / 'RESTORE_VERIFICATION.json').write_text(json.dumps(evidence, indent=2), encoding='utf-8')
    run('pg_restore.exe', ['-h', '127.0.0.1', '-p', PORT, '-U', ADMIN, '-d', DB,
                           '--role', APP, '--no-owner', '--no-privileges', '--exit-on-error',
                           '--single-transaction', DUMP], 'restore.log')
    kwargs['dbname'] = DB
    verify_restored(evidence, kwargs)


if __name__ == '__main__':
    main()
