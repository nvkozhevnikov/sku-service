"""Fail-closed preflight for the authorized vekprom/intervesp live ingestion.

Verifies the worktree, protected-branch baselines, isolated DB identity and
the restored six-namespace commercial baseline before any HTTP GET or write.
Secrets stay in the private LOCALAPPDATA directory; nothing is printed.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BRANCH = 'feature/universal-supplier-vekprom-intervesp-ingestion'
DEVELOP = 'deefb595868d19ba471b9b943ff779194e231c26'
PRODUCTION = '36924836bb02965e857173773cdb70f302914643'
OUT = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
SYSTEM_ID = '7694223408076967768'
DB = 'universal_supplier_vekprom_intervesp'
PORT = 55452
APP_USER = 'vekprom_intervesp_app'
EXPECTED_NAMESPACES = {'kami': 5264, 'beka_mak': 109, 'intervesp': 1642,
                       'beka_mak_tr': 78, 'optimum': 1353, 'partner_st': 1225}


def git(*args: str) -> str:
    return subprocess.check_output(['git', *args], cwd=ROOT, text=True).strip()


def fail(record: dict, code: str, detail) -> None:
    record['status'] = 'PREFLIGHT_FAILED'
    record['blocker'] = {'code': code, 'detail': detail}
    (OUT / 'PREFLIGHT.json').write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(record['blocker'], ensure_ascii=False), flush=True)
    raise SystemExit(2)


def main() -> None:
    record = {'recorded_at': datetime.now(timezone.utc).isoformat(), 'status': 'PENDING'}
    try:
        branch = git('branch', '--show-current')
        head = git('rev-parse', 'HEAD')
        develop = git('rev-parse', 'develop')
        production = git('rev-parse', 'production')
        dirty = git('status', '--porcelain').splitlines()
    except Exception as error:  # pragma: no cover - environment failure
        fail(record, 'git_unavailable', str(error))
    if branch != BRANCH:
        fail(record, 'wrong_branch', branch)
    if develop != DEVELOP or production != PRODUCTION:
        fail(record, 'protected_branch_moved', {'develop': develop, 'production': production})
    record['git'] = {'branch': branch, 'head': head, 'develop': develop,
                     'production': production, 'dirty_files': dirty}
    runtime = PRIVATE / 'runtime.json'
    if not runtime.is_file():
        fail(record, 'runtime_config_missing', str(runtime))
    config = json.loads(runtime.read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    if (config.get('SCHEDULER_ENABLED'), config.get('STERBRUST_WRITE_ENABLE'),
            config.get('ESOL_WRITE_ENABLE')) != ('NO', 'NO', 'NO'):
        fail(record, 'runtime_flags_unsafe', {k: config.get(k) for k in
                                              ('SCHEDULER_ENABLED', 'STERBRUST_WRITE_ENABLE', 'ESOL_WRITE_ENABLE')})
    from universal_supplier.postgres import PostgresConfig, PostgresRepository
    repository = PostgresRepository(PostgresConfig.from_env())
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
                       current_setting('server_version'),current_user,pg_is_in_recovery(),
                       system_identifier::text FROM pg_control_system()""")
            identity = cursor.fetchone()
            cursor.execute('SELECT rolsuper,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user')
            roles = cursor.fetchone()
            migrations = dict(cursor.execute('SELECT filename,sha256 FROM schema_migrations').fetchall())
            namespaces = dict(cursor.execute('SELECT s.code,count(p.id) FROM suppliers s '
                                             'LEFT JOIN source_products p ON p.supplier_id=s.id '
                                             'GROUP BY s.code').fetchall())
            totals = cursor.execute('SELECT count(*),count(*) FILTER (WHERE catalog_product_id IS NOT NULL) '
                                    'FROM source_products').fetchone()
            linked = dict(cursor.execute('SELECT s.code,count(*) FILTER (WHERE p.catalog_product_id IS NOT NULL) '
                                         'FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id '
                                         'GROUP BY s.code').fetchall())
            intervesp = cursor.execute("""SELECT count(*),count(*) FILTER (WHERE catalog_product_id IS NOT NULL),
                                 count(*) FILTER (WHERE NOT external_id_is_stable)
                                 FROM source_products
                                 WHERE supplier_id=(SELECT id FROM suppliers WHERE code='intervesp')""").fetchone()
            offer_projection = {row[0]: (row[1], row[2]) for row in cursor.execute(
                """SELECT s.code,count(o.id) FILTER (WHERE o.offer_kind='default'),bool_and(o.active)
                   FROM suppliers s LEFT JOIN offers o ON o.supplier_id=s.id GROUP BY s.code""").fetchall()}
            supplier_enabled = dict(cursor.execute('SELECT code,enabled FROM suppliers').fetchall())
    finally:
        repository.close()
    expected = (DB, '127.0.0.1', PORT, '17.11', APP_USER, False, SYSTEM_ID)
    restore = json.loads((OUT / 'DATABASE' / 'RESTORE_VERIFICATION.json').read_text(encoding='utf-8'))
    data_dir_ok = (restore.get('system_identifier') == SYSTEM_ID and restore.get('port') == PORT
                   and restore.get('database') == DB and restore.get('status') == 'LIVE_VERIFIED_ISOLATED_BASELINE'
                   and restore.get('data_directory', '').endswith(
                       'universal_supplier_vekprom_intervesp_20261008_pg17_data'))
    if identity != expected or roles != (False, False, False) or not data_dir_ok:
        fail(record, 'db_identity_mismatch',
             {'identity': identity, 'roles': roles, 'data_dir_ok': bool(data_dir_ok)})
    record['database'] = {'database': identity[0], 'host': identity[1], 'port': identity[2],
                          'server_version': identity[3], 'user': identity[4],
                          'in_recovery': identity[5], 'system_identifier': identity[6],
                          'roles': {'superuser': roles[0], 'createdb': roles[1], 'createrole': roles[2]},
                          'data_directory_verified_via': 'RESTORE_VERIFICATION.json admin record'}
    git_files = subprocess.check_output(['git', 'ls-tree', '-r', '--name-only', 'HEAD', 'migrations/'],
                                        cwd=ROOT, text=True).splitlines()
    canonical = {Path(path).name for path in git_files if path.endswith('.sql')}
    if set(migrations) != canonical:
        fail(record, 'migration_set_mismatch',
             {'db_only': sorted(set(migrations) - canonical), 'git_only': sorted(canonical - set(migrations))})
    if namespaces != EXPECTED_NAMESPACES:
        fail(record, 'namespace_baseline_mismatch', namespaces)
    if 'vekprom' in namespaces or 'vekprom' in supplier_enabled:
        fail(record, 'vekprom_already_present', sorted(namespaces))
    if totals != (9671, 117):
        fail(record, 'source_product_totals_mismatch', list(totals))
    if linked != {'partner_st': 117, 'kami': 0, 'beka_mak': 0, 'beka_mak_tr': 0,
                  'optimum': 0, 'intervesp': 0}:
        fail(record, 'catalog_linked_baseline_mismatch', linked)
    if intervesp != (1642, 0, 0):
        fail(record, 'intervesp_refresh_precondition_failed',
             {'rows': intervesp[0], 'catalog_linked': intervesp[1], 'unstable_external_ids': intervesp[2]})
    for code, count in EXPECTED_NAMESPACES.items():
        if offer_projection.get(code) != (count, False):
            fail(record, 'offer_projection_mismatch', {code: offer_projection.get(code)})
    if any(supplier_enabled.get(code) for code in ('intervesp', 'beka_mak', 'beka_mak_tr', 'kami')):
        fail(record, 'supplier_enabled_flag_mismatch', supplier_enabled)
    record['baseline'] = {'schema_migrations': len(migrations), 'migration_files': sorted(migrations),
                          'namespaces': namespaces, 'source_products': totals[0],
                          'catalog_linked_source_products': totals[1],
                          'catalog_linked_per_supplier': linked,
                          'intervesp_refresh_preconditions': {'rows': intervesp[0],
                                                              'catalog_linked': intervesp[1],
                                                              'unstable_external_ids': intervesp[2]},
                          'default_offers_per_supplier': offer_projection,
                          'suppliers_enabled': supplier_enabled,
                          'vekprom_present': False}
    record['status'] = 'PREFLIGHT_PASSED'
    (OUT / 'PREFLIGHT.json').write_text(json.dumps(record, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps({'status': record['status'], 'head': head, 'namespaces': namespaces,
                      'intervesp_rows': intervesp[0], 'vekprom_present': False,
                      'migrations': len(migrations)}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
