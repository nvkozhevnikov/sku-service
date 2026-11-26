"""Create only a fresh loopback LTT/USS PG17.11 cluster; no production settings."""
import hashlib
import json
from pathlib import Path
import secrets
import socket
import subprocess
import os
import sys
import psycopg

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'
BIN = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
DATA = OUT / 'PRIVATE/pg17'
STATE = OUT / 'PRIVATE/connection.json'


def main():
    version = subprocess.check_output([str(BIN / 'postgres.exe'), '--version'], text=True).strip()
    if not version.endswith('17.11'):
        raise RuntimeError('Pinned PostgreSQL 17.11 required')
    resume_schema = '--resume-schema' in sys.argv
    resume = '--resume-bootstrap' in sys.argv or resume_schema
    if (DATA.exists() or STATE.exists()) and not resume:
        raise RuntimeError('Cluster already exists; inspect retained state, never overwrite')
    if resume and (not DATA.exists() or not STATE.exists() or (OUT / 'DB_RECEIPT.json').exists()):
        raise RuntimeError('Resume only the incomplete dedicated bootstrap')
    port = 55455
    if not resume_schema:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', port))
    OUT.mkdir(parents=True, exist_ok=True)
    STATE.parent.mkdir(parents=True, exist_ok=True)
    if resume:
        saved = json.loads(STATE.read_text('utf-8'))
        config, admin_password = saved['runtime'], saved['admin_password']
        runtime_password = config['password']
        if (config['host'], config['port'], config['dbname'], config['user']) != ('127.0.0.1', port, 'universal_supplier_ltt_uss', 'ltt_uss_runtime'):
            raise RuntimeError('Retained target does not match isolated scope')
    else:
        admin_password, runtime_password = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        password_file = STATE.parent / 'bootstrap-password.tmp'
        password_file.write_text(admin_password + '\n', 'utf-8')
        try:
            subprocess.run([str(BIN / 'initdb.exe'), '-D', str(DATA), '-U', 'ltt_uss_admin', '--encoding=UTF8',
                            '--auth-host=scram-sha-256', '--auth-local=scram-sha-256', '--pwfile', str(password_file)], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        finally:
            password_file.unlink(missing_ok=True)
        config = dict(host='127.0.0.1', port=port, dbname='universal_supplier_ltt_uss', user='ltt_uss_runtime', password=runtime_password, sslmode='disable')
        # Secrets are saved only under the ignored private runtime directory.
        STATE.write_text(json.dumps(dict(runtime=config, admin_password=admin_password)), 'utf-8')
    if not resume_schema:
        subprocess.run([str(BIN / 'pg_ctl.exe'), 'start', '-D', str(DATA), '-l', str(STATE.parent / 'postgres.log'),
                        '-o', f'-h 127.0.0.1 -p {port}', '-w', '-t', '30'], check=True)
    admin = dict(config, dbname='postgres', user='ltt_uss_admin', password=admin_password)
    with psycopg.connect(**admin, autocommit=True) as connection:
        from psycopg import sql
        if not resume_schema:
            connection.execute(sql.SQL('CREATE ROLE ltt_uss_runtime LOGIN PASSWORD {}').format(sql.Literal(runtime_password)))
            connection.execute('CREATE DATABASE universal_supplier_ltt_uss OWNER ltt_uss_runtime')
        from psycopg.adapt import Loader
        from psycopg.types.string import TextLoader
        class PathBytesLoader(Loader):
            def load(self, data):
                return bytes(data)
        connection.adapters.register_loader('text', PathBytesLoader)
        path_bytes = connection.execute('SHOW data_directory').fetchone()[0]
        connection.adapters.register_loader('text', TextLoader)
        try:
            data_directory = path_bytes.decode('utf-8')
        except UnicodeDecodeError:
            # Windows startup path is supplied by the platform ANSI API.
            data_directory = path_bytes.decode('cp1251')
        if Path(data_directory).resolve() != DATA.resolve():
            raise RuntimeError('Connected cluster is not the dedicated directory')
        system_id = str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
    expected = json.loads((ROOT / 'stage6e_migration_baseline_sha256.json').read_text('utf-8'))['files']
    migrations = sorted((ROOT / 'migrations').glob('[0-9][0-9][0-9]_*.sql'))
    checkout_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in migrations}
    actual = {path.name: hashlib.sha256(path.read_bytes().replace(b'\r\n', b'\n')).hexdigest() for path in migrations}
    # Git object bytes must independently equal the LF baseline; no migration rewrite.
    for name, digest in expected.items():
        blob = subprocess.check_output(['git', 'show', 'HEAD:migrations/' + name], cwd=ROOT)
        if hashlib.sha256(blob).hexdigest() != digest:
            raise RuntimeError('Git migration baseline mismatch: ' + name)
    if any(actual.get(name) != digest for name, digest in expected.items()):
        raise RuntimeError('Immutable migration mismatch')
    with psycopg.connect(**config, autocommit=True) as connection:
        if connection.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'").fetchone()[0] != 0:
            raise RuntimeError('Only empty isolated database may receive initial schema')
        connection.execute('CREATE TABLE schema_migrations(filename text PRIMARY KEY, sha256 char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())')
        for path in migrations:
            with connection.transaction():
                connection.execute(path.read_text('utf-8'))
                connection.execute('INSERT INTO schema_migrations(filename,sha256) VALUES (%s,%s)', (path.name, actual[path.name]))
        assert connection.execute('SELECT count(*) FROM source_products').fetchone()[0] == 0
    receipt = dict(host=config['host'], port=port, dbname=config['dbname'], user=config['user'],
                   version=version, system_identifier=system_id, migrations=actual, checkout_hashes=checkout_hashes,
                   migration_hash_basis='LF bytes independently verified against Git HEAD objects', initial_source_products=0,
                   evidence_class='LIVE-VERIFIED', isolation='fresh dedicated cluster; loopback only')
    (OUT / 'DB_RECEIPT.json').write_text(json.dumps(receipt, indent=2) + '\n', 'utf-8')
    print(json.dumps({k: v for k, v in receipt.items() if k != 'migrations'}))


if __name__ == '__main__':
    main()
