"""Read-only exact resume verification. Never repeats ingestion or supplier HTTP."""
from pathlib import Path
import json
import hashlib
import sys
import psycopg
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from run_ltt_uss_offline_canary import fingerprints

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT = BASE / 'RESUME_2026-10-14'


def main():
    saved = json.loads((BASE/'PRIVATE/connection.json').read_text('utf-8'))
    config = saved['runtime']
    assert (config['host'], config['port'], config['dbname']) == ('127.0.0.1',55455,'universal_supplier_ltt_uss')
    old = json.loads((BASE/'OFFLINE_CANARY_15MIN/CHECKPOINT.json').read_text('utf-8'))
    admin = dict(config,user='ltt_uss_admin',password=saved['admin_password'],connect_timeout=5)
    with psycopg.connect(**admin,autocommit=True) as connection:
        connection.execute('SET default_transaction_read_only=on')
        version=connection.execute('SHOW server_version').fetchone()[0]
        system=str(connection.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
        assert version.startswith('17.11') and system=='7696153366151515704'
    with psycopg.connect(**config,autocommit=True) as connection:
        connection.execute('SET default_transaction_read_only=on')
        current=fingerprints(connection)
        assert current==old['db_verification']['after_fingerprints']
        ids=connection.execute("SELECT s.code,p.external_id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id ORDER BY s.code,p.external_id").fetchall()
        assert len(ids)==7 and ('ltt_yug','312') in ids and ('ltt_yug','297') in ids
    manifest_receipts=json.loads((BASE/'MANIFESTS/SHA256.json').read_text('utf-8'))
    manifests={}
    for supplier,receipt in manifest_receipts.items():
        body=(BASE/'MANIFESTS'/(supplier+'.json')).read_bytes()
        assert hashlib.sha256(body).hexdigest()==receipt['sha256']
        rows=json.loads(body)['candidates']
        urls=[r['canonical_url'] for r in rows]
        known=[r['source_internal_id'] for r in rows if r['source_internal_id'] is not None]
        assert len(urls)==len(set(urls))
        manifests[supplier]=dict(total=len(rows),sha256=receipt['sha256'],duplicate_canonical_urls=0,
                                 duplicate_known_stable_ids=len(known)-len(set(known)),known_stable_ids=len(known),
                                 unverified_stable_ids=len(rows)-len(known))
    assert manifests['ltt_yug']['total']==592
    assert json.loads((BASE/'MANIFESTS/ltt_yug.json').read_text('utf-8'))['counts']['suffix_exclusions']==0
    result=dict(status='EXACT_RESUME_VERIFIED',postgres_version=version,system_identifier=system,
                source_products=7,offers=current['offers']['count'],observations=current['offer_commercial_observations']['count'],
                source_ids=ids,all_public_table_fingerprints_unchanged=True,table_fingerprints=current,manifests=manifests,
                supplier_http=0,db_writes=0)
    (OUT/'RESUME_VERIFICATION.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n','utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='table_fingerprints'}))


if __name__=='__main__':
    main()
