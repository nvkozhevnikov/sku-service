"""Exact readonly baseline, then one authorized failed-row retry. No other GET."""
from datetime import datetime,timezone
from dataclasses import asdict
from pathlib import Path
import json
import hashlib
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.run_ltt_uss_offline_canary import fingerprints,save
from scripts.run_ltt_uss_live_canary import parse,verify_persisted,ALLOWED
from universal_supplier.http_capture import CaptureStatus,public_transport_url
from universal_supplier.ltt_uss_capture import capture_source_html
from universal_supplier.postgres import PostgresConfig,PostgresRepository
ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT=BASE/'UNICODE_RESUME_2026-10-14'


def main():
    original=json.loads((BASE/'RESUME_2026-10-14/LIVE_CANARY.json').read_text('utf-8'))
    failure=original['failure_row']
    assert failure['supplier']=='ltt_sib' and failure['capture_diagnostics']==['network_error:UnicodeEncodeError']
    if (OUT/'RETRY.json').exists():raise RuntimeError('Retry receipt already exists; do not repeat blindly')
    saved=json.loads((BASE/'PRIVATE/connection.json').read_text('utf-8'))
    config=saved['runtime']
    assert (config['host'],config['port'],config['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    admin=dict(config,user='ltt_uss_admin',password=saved['admin_password'],connect_timeout=5)
    with psycopg.connect(**admin,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        system=str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
        version=c.execute('SHOW server_version').fetchone()[0]
        assert system=='7696153366151515704' and version.startswith('17.11')
    repo=PostgresRepository(PostgresConfig(**config))
    try:
        before=fingerprints(repo.connection)
        assert before==json.loads((BASE/'RESUME_2026-10-14/BLOCKED_DB_VERIFICATION.json').read_text('utf-8'))['table_fingerprints']
        assert before['source_products']['count']==39 and before['offers']['count']==39 and before['offer_commercial_observations']['count']==43
        save(OUT/'RESUME_BASELINE.json',dict(status='EXACT_39_39_43_RESUME_VERIFIED',system_identifier=system,postgres_version=version,
            source_products=39,offers=39,observations=43,fingerprints=before,db_writes=0,supplier_http=0))
        url=failure['requested_url']
        result=capture_source_html(url,supplier='ltt_sib',evidence_dir=OUT/'EVIDENCE',timeout_seconds=25)
        row=dict(supplier='ltt_sib',requested_url=url,final_url=result.final_url,transport_url=result.transport_url,
                 expected_transport_url=public_transport_url(url),http_status=result.http_status,charset=result.charset,
                 observed_at=result.observed_at.isoformat(),response_sha256=result.response_sha256,evidence_sha256=result.evidence_sha256,
                 evidence_ref=result.evidence_ref,capture_diagnostics=list(result.diagnostics),new_supplier_attempts=1,
                 outcome=result.status.value,status='FAILED',previous_failure=failure)
        if result.status!=CaptureStatus.SUCCESS:
            save(OUT/'RETRY.json',row)
            print(json.dumps(row,ensure_ascii=True))
            return 1
        assert result.http_status==200 and result.final_url==url and result.transport_url==public_transport_url(url)
        product=parse('ltt_sib',result.evidence_body,result.final_url)
        assert product.name and product.site_internal_id and product.source_url==url
        with repo.connection.transaction():
            repo.persist_commercial_observation(product,result.capture)
            verified=verify_persisted(repo,product,result.capture)
            after=fingerprints(repo.connection)
            assert all(before[n]==after[n] for n in before if n not in ALLOWED)
            assert after['source_products']['count']==40 and after['offers']['count']==40
            assert after['supplier_http_captures']['count']==44 and after['offer_commercial_observations']['count']==44
            assert not repo.connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        row.update(status='PASS',outcome='PERSISTED',external_id=product.site_internal_id,title=product.name,
                   model=product.supplier_model,brand=product.source_brand or None,price_state=product.price.state.value,
                   price=str(product.price.current_price) if product.price.current_price is not None else None,
                   price_raw=product.price.raw_text,availability=product.availability,
                   kind=product.enrichment_evidence['product_kind']['product_kind'],category=product.source_category,
                   parser_diagnostics=list(product.diagnostics),capture=asdict(result.capture),db_verification=verified,
                   source_identity_unchanged=True,duplicate_external_ids=0,protected_delta=0,after_fingerprints=after)
        save(OUT/'RETRY.json',row)
        print(json.dumps({k:row[k] for k in ('status','external_id','http_status','price','availability','requested_url','transport_url','source_identity_unchanged')},ensure_ascii=True))
        return 0
    finally:
        repo.close()


if __name__=='__main__':
    raise SystemExit(main())
