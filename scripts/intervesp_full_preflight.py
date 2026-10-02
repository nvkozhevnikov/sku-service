"""Read-only identity/before-state for full listing ingestion. Never starts DB."""
import json
import os
from pathlib import Path
import psycopg

ROOT = Path(__file__).resolve().parents[1]


def read_source_state():
    passfile = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/rc-local/pgpass.conf'
    # rc_readonly deliberately cannot read data_directory. Attest the path in a
    # separate strictly read-only admin transaction; do not broaden its grants.
    with psycopg.connect(host='127.0.0.1', port=55449, dbname='universal_supplier_server',
                         user='rc_admin', passfile=str(passfile), connect_timeout=5,
                         options='-c default_transaction_read_only=on', autocommit=True) as admin:
        path_identity = admin.execute("SELECT current_database(),current_setting('server_version'),"
                                      "pg_is_in_recovery(),system_identifier,encode(pg_catalog.textsend(current_setting('data_directory')),'hex'),"
                                      "current_setting('port') FROM pg_control_system()").fetchone()
        expected_path = str(ROOT.parent / 'universal_supplier_rc_pg17_data').replace('\\', '/').lower()
        path_bytes = bytes.fromhex(path_identity[4])
        decoded_paths = []
        for encoding in ('utf-8', 'cp1251'):
            try: decoded_paths.append(path_bytes.decode(encoding).replace('\\', '/'))
            except UnicodeDecodeError: pass
        actual_path = next((path for path in decoded_paths if path.lower() == expected_path), None)
        if (path_identity[:4] != ('universal_supplier_server', '17.11', False, 7691270601420084116)
                or actual_path is None or path_identity[5] != '55449'):
            raise RuntimeError('RC path/identity mismatch')
    with psycopg.connect(host='127.0.0.1', port=55449, dbname='universal_supplier_server',
                         user='rc_readonly', passfile=str(passfile), connect_timeout=5,
                         options='-c default_transaction_read_only=on', autocommit=True) as connection:
        identity = connection.execute("SELECT current_database(),current_user,current_setting('server_version'),"
                                      "pg_is_in_recovery(),system_identifier FROM pg_control_system()").fetchone()
        expected = ('universal_supplier_server', 'rc_readonly', '17.11', False, 7691270601420084116)
        if identity[:5] != expected:
            raise RuntimeError('RC database identity mismatch')
        counts = connection.execute("SELECT s.code,s.enabled,count(sp.id) FROM suppliers s "
                                    "LEFT JOIN source_products sp ON sp.supplier_id=s.id "
                                    "GROUP BY s.code,s.enabled ORDER BY s.code").fetchall()
        rows = connection.execute("SELECT sp.id,sp.external_id,sp.source_url,sp.canonical_url "
                                  "FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id "
                                  "WHERE s.code='intervesp' ORDER BY sp.id").fetchall()
        totals = {table: connection.execute(f'SELECT count(*) FROM {table}').fetchone()[0] for table in
                  ('suppliers', 'source_products', 'offers', 'product_matches', 'catalog_products',
                   'supplier_http_captures', 'offer_commercial_observations')}
        safety = connection.execute("SELECT (SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL "
            "AND supplier_id IN (SELECT id FROM suppliers WHERE code IN ('intervesp','beka_mak','beka_mak_tr'))),"
            "(SELECT count(*) FROM offers WHERE active AND supplier_id IN "
            "(SELECT id FROM suppliers WHERE code IN ('intervesp','beka_mak','beka_mak_tr'))),"
            "(SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1) d),"
            "(SELECT count(*) FROM (SELECT supplier_id,source_product_id FROM offers GROUP BY supplier_id,source_product_id HAVING count(*)>1) d)").fetchone()
        if any(safety): raise RuntimeError('RC commercial safety/duplicate guard failed')
        return {'identity': identity, 'data_directory': actual_path, 'port': path_identity[5],
                'counts': counts, 'table_counts': totals, 'safety_counts': safety, 'intervesp_identities': rows}


if __name__ == '__main__':
    try:
        print(json.dumps(read_source_state(), default=str))
    except psycopg.OperationalError as error:
        print(json.dumps({'status': 'DB_UNAVAILABLE', 'error': str(error)}))
        raise SystemExit(2)
