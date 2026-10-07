"""Read-only functional store verification, not browser/visual QA."""
import json
import os
import hashlib
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
    assert rows['total'] == 5264
    variant = store.product('kami', 'variant:9833201')
    assert variant and variant['brand_raw'] == 'SPECTR' and len(variant['properties']) == 32
    history = store.product_history('kami', 'variant:9833201')
    assert any(row.get('field_group') == 'commercial_observation' for row in history)
    receipt=json.loads((ROOT/'reports/KAMI_INTEGRATION_2026-10-05/SELECTION_RECONCILIATION_2026-10-06/SELECTION_PERSISTED.json').read_bytes())
    assert receipt['status']=='LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION'
    summary=store.xml_canonical_summary()
    assert summary['selected']==len(receipt['selected'])
    snapshot=store.xml_canonical_snapshot()
    assert len(snapshot.selected_rows)==len(receipt['selected'])
    assert store.xml_canonical_summary('kami')['selected']==0
    release=ROOT/'release/KAMI_FEATURE_RC_2026-10-06'
    os.environ['FINAL_RC_ARTIFACTS_DIR']=str(release)
    os.environ['FINAL_RC_MANIFEST_SHA256']=hashlib.sha256((release/'SHA256_MANIFEST.json').read_bytes()).hexdigest()
    from universal_supplier.control_plane.web import create_app
    from fastapi.testclient import TestClient
    routes=['/health','/','/products?supplier=kami','/rc-final?supplier=kami&classification=REVIEW','/rc-final?classification=READY_TO_CREATE_FULL']
    with TestClient(create_app(store=store,auth_required=False,session_secret='local-offline-test-session-key-32chars')) as client:
        for route in routes: assert client.get(route).status_code==200
    report = {'status': 'LIVE_VERIFIED_READ_ONLY_PANEL_STORE', 'port': 55451,
              'supplier_visible': True, 'kami_total': rows['total'], 'variant_own_properties': len(variant['properties']),
              'commercial_history_visible': True, 'browser_visual_qa': 'NOT_PERFORMED',
              'visual_status':'DEFERRED_BY_OPERATOR','selected_from_SQL':summary['selected'],
              'selected_KAMI':0,'feed_selected_equals_committed_SQL':True,'GET_testclient_routes':routes,
              'old_panel_configuration_changed': False, 'sql_writes': 0}
    (ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/SELECTION_RECONCILIATION_2026-10-06/PANEL_DATA_VERIFIED.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report))


if __name__ == '__main__':
    main()
