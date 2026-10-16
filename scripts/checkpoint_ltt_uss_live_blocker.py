"""Read-only post-stop verification and exact blocked checkpoint; no supplier I/O."""
from datetime import datetime,timezone
from pathlib import Path
import json
import hashlib
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.run_ltt_uss_offline_canary import fingerprints, save, git
from scripts.run_ltt_uss_live_canary import verify_persisted,parse,gate,ALLOWED
from universal_supplier.commercial_persistence import HttpCapture

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT=BASE/'RESUME_2026-10-14'


def main():
    state=json.loads((OUT/'LIVE_CANARY.json').read_text('utf-8'))
    failures=[r for r in state['rows'] if r['outcome']!='PERSISTED']
    assert len(failures)==1 and failures[0]['supplier']=='ltt_sib'
    assert failures[0]['capture_diagnostics']==['network_error:UnicodeEncodeError']
    config=json.loads((BASE/'PRIVATE/connection.json').read_text('utf-8'))['runtime']
    assert (config['host'],config['port'],config['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    connection=psycopg.connect(**config,autocommit=True)
    connection.execute('SET default_transaction_read_only=on')
    class ReadOnlyRepo:
        pass
    repo=ReadOnlyRepo();repo.connection=connection
    try:
        for row in state['rows']:
            if row['outcome']!='PERSISTED':continue
            body=(OUT/'EVIDENCE'/row['supplier']/(row['evidence_sha256']+'.html')).read_bytes()
            assert hashlib.sha256(body).hexdigest()==row['evidence_sha256']
            product=parse(row['supplier'],body,row['final_url'])
            metadata=dict(row['capture'])
            metadata['observed_at']=datetime.fromisoformat(metadata['observed_at'])
            metadata['redirects']=tuple(metadata['redirects']);metadata['diagnostics']=tuple(metadata['diagnostics'])
            capture=HttpCapture(**metadata)
            assert verify_persisted(repo,product,capture)==row['db_verification']
        before=json.loads((OUT/'DB_BEFORE_LIVE_CANARY.json').read_text('utf-8'))
        after=fingerprints(connection)
        delta=[name for name in before if name not in ALLOWED and before[name]!=after[name]]
        assert not delta
        duplicates=connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        assert not duplicates
        counts=connection.execute("""SELECT s.code,count(DISTINCT p.id),count(DISTINCT p.external_id),count(DISTINCT o.id),
            count(DISTINCT o.source_product_id),count(DISTINCT c.id),count(DISTINCT b.id)
            FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id
            LEFT JOIN offers o ON o.source_product_id=p.id LEFT JOIN supplier_http_captures c ON c.source_product_id=p.id
            LEFT JOIN offer_commercial_observations b ON b.capture_id=c.id GROUP BY s.code ORDER BY s.code""").fetchall()
        yug=connection.execute("""SELECT p.external_id,p.id,o.id,o.price,o.availability_normalized FROM source_products p
            JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
            WHERE s.code='ltt_yug' AND p.external_id IN ('312','297') ORDER BY p.external_id""").fetchall()
        assert len(yug)==2 and yug[0][1]!=yug[1][1] and yug[0][2]!=yug[1][2]
        assert after['supplier_http_captures']['count']==7+sum(r['outcome']=='PERSISTED' for r in state['rows'])
        assert after['offer_commercial_observations']['count']==after['supplier_http_captures']['count']
        assert connection.execute("SELECT count(*) FROM pg_stat_activity WHERE application_name='ltt_uss_live_canary'").fetchone()[0]==0
        state.update(status='LIVE_CANARY_BLOCKED',stopped_at=datetime.now(timezone.utc).isoformat(),
                     stage='LIVE_CANARY',blocker='LOCAL_TRANSPORT_URL_ENCODING_ERROR',
                     root_cause='Unicode degree character in frozen SIB URL passed to urllib Request without UTF8 percent encoding; ASCII request target rejects it before supplier HTTP',
                     failure_row=failures[0],protected_table_delta=delta,db_after=after,
                     verified_persisted_rows=sum(r['outcome']=='PERSISTED' for r in state['rows']),
                     duplicate_external_ids=0,yug_312_297_retained=True,yug_db_rows=yug,
                     per_supplier_db_counts=[dict(supplier=r[0],source_products=r[1],distinct_external_id=r[2],offers=r[3],
                                                 distinct_source_product_id=r[4],captures=r[5],observations=r[6]) for r in counts],
                     confirmed_http_responses=sum(r['http_status'] is not None for r in state['rows']),
                     failed_before_http=sum(r['http_status'] is None for r in state['rows']))
        state['sources']['ltt_sib']=gate([r for r in state['rows'] if r['supplier']=='ltt_sib'],'ltt_sib')
        for supplier in ('ltt_yug','uss_stanko'):
            state['sources'][supplier]=dict(status='NOT_STARTED',reason='STOP_AFTER_MANDATORY_CANARY_FAILURE')
        save(OUT/'LIVE_CANARY.json',state)
        save(OUT/'BLOCKED_DB_VERIFICATION.json',dict(status='READ_ONLY_VERIFIED',counts=state['per_supplier_db_counts'],
            protected_delta=delta,table_fingerprints=after,yug_rows=yug,verified_persisted_rows=state['verified_persisted_rows'],
            all_capture_ledger_rows_accounted=True))
        print(json.dumps({k:state[k] for k in ('status','verified_persisted_rows','supplier_gets','confirmed_http_responses','failed_before_http','per_supplier_db_counts','protected_table_delta')},default=str))
    finally:
        connection.close()


if __name__=='__main__':
    main()
