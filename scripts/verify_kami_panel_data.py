"""Read-only functional store verification, not browser/visual QA."""
import json
import os
from pathlib import Path
import psycopg
from universal_supplier.postgres import PostgresConfig
from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters

ROOT = Path(__file__).resolve().parents[1]
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/kami-rc-20261005'


def main():
    config = json.loads((PRIVATE / 'runtime.json').read_bytes())
    marker = json.loads((ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    if config['DB_NAME'] != 'universal_supplier_kami_rc' or config['DB_PORT'] != '55451':
        raise RuntimeError('Isolated target only')
    password = (PRIVATE / 'pgpass.conf').read_text(encoding='utf-8').strip().split(':', 4)[4]
    with psycopg.connect(host='127.0.0.1', port=55451, dbname=config['DB_NAME'], user='kami_admin', password=password, connect_timeout=5, autocommit=True) as conn:
        conn.execute('BEGIN READ ONLY')
        actual = conn.execute('SELECT current_database(),inet_server_port(),system_identifier::text FROM pg_control_system()').fetchone()
        conn.execute('ROLLBACK')
        assert actual == (config['DB_NAME'], 55451, marker['system_identifier'])
    os.environ.update(config)
    store = PostgresControlPlaneStore(PostgresConfig.from_env())
    options = store.filter_options()
    assert any(s['code'] == 'kami' for s in options['suppliers'])
    rows = store.products(ProductFilters(supplier='kami', page_size=25))
    assert rows['total'] == 4
    variant = store.product('kami', 'variant:9833201')
    assert variant and variant['brand_raw'] == 'SPECTR' and len(variant['properties']) == 32
    history = store.product_history('kami', 'variant:9833201')
    assert any(row.get('field_group') == 'commercial_observation' for row in history)
    report = {'status': 'LIVE_VERIFIED_READ_ONLY_PANEL_STORE', 'port': 55451,
              'supplier_visible': True, 'kami_total': rows['total'], 'variant_own_properties': len(variant['properties']),
              'commercial_history_visible': True, 'browser_visual_qa': 'NOT_PERFORMED',
              'old_panel_configuration_changed': False, 'sql_writes': 0}
    (ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/PANEL_DATA_VERIFIED.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
