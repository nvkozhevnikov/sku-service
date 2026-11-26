"""Read-only reconciliation after mandatory YUG canary stop; no source requests."""
from pathlib import Path
from datetime import datetime,timezone
import hashlib
import json
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.run_ltt_uss_live_canary import parse,verify_persisted,ALLOWED,gate
from scripts.run_ltt_uss_offline_canary import fingerprints,save
from universal_supplier.commercial_persistence import HttpCapture

BASE=Path(__file__).resolve().parents[1]/'reports/LTT_USS_INTEGRATION_2026-10-13'
OUT=BASE/'UNICODE_RESUME_2026-10-14'


def main():
    state=json.loads((OUT/'LIVE_CANARY.json').read_text('utf-8'))
    failures=[r for r in state['rows'] if r['outcome']=='PARSE_ERROR']
    assert len(failures)==1 and failures[0]['supplier']=='ltt_yug'
    failure=failures[0]
    body=(OUT/'EVIDENCE/ltt_yug'/(failure['evidence_sha256']+'.html')).read_bytes()
    assert hashlib.sha256(body).hexdigest()==failure['evidence_sha256']
    product=parse('ltt_yug',body,failure['final_url'])
    assert product.name and product.site_internal_id=='1' and product.source_url and product.source_category==''
    proof=dict(status='REPOSITORY_AND_SAVED_EVIDENCE_VERIFIED',source_url=failure['requested_url'],http_status=200,
               source_title=product.name,parsed_site_id=product.site_internal_id,category=product.source_category,
               failing_assertion='assert product.source_url and product.source_category',
               root_cause='LTT YUG category helper derives only from URLs with >=3 path components; shallow /catalog/<slug>.html has no category; capture gate did not reject incomplete source-card evidence',
               page_role='UNRESOLVED; generic landing/category is INFERENCE, not a proven commercial product',
               evidence_sha256=failure['evidence_sha256'],supplier_http=0)
    save(OUT/'YUG_CATEGORY_BLOCKER.json',proof)
    config=json.loads((BASE/'PRIVATE/connection.json').read_text('utf-8'))['runtime']
    assert (config['host'],config['port'],config['dbname'])==('127.0.0.1',55455,'universal_supplier_ltt_uss')
    with psycopg.connect(**config,autocommit=True) as connection:
        connection.execute('SET default_transaction_read_only=on')
        class Repo:pass
        repo=Repo();repo.connection=connection
        for row in state['rows']:
            if row['outcome']!='PERSISTED':continue
            root=BASE/row.get('evidence_root','UNICODE_RESUME_2026-10-14/EVIDENCE')
            frozen=(root/row['supplier']/(row['evidence_sha256']+'.html')).read_bytes()
            assert hashlib.sha256(frozen).hexdigest()==row['evidence_sha256']
            parsed=parse(row['supplier'],frozen,row['final_url'])
            metadata=dict(row['capture'])
            metadata['observed_at']=datetime.fromisoformat(metadata['observed_at'])
            metadata['redirects']=tuple(metadata['redirects']);metadata['diagnostics']=tuple(metadata['diagnostics'])
            assert verify_persisted(repo,parsed,HttpCapture(**metadata))==row['db_verification']
        before=json.loads((OUT/'DB_BEFORE_LIVE_CANARY.json').read_text('utf-8'))
        after=fingerprints(connection)
        assert all(before[n]==after[n] for n in before if n not in ALLOWED)
        assert connection.execute("SELECT count(*) FROM source_products p JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='ltt_yug' AND p.external_id='1'").fetchone()[0]==0
        assert not connection.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        counts=connection.execute("""SELECT s.code,count(DISTINCT p.id),count(DISTINCT p.external_id),count(DISTINCT o.id),
            count(DISTINCT o.source_product_id),count(DISTINCT c.id),count(DISTINCT b.id)
            FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id LEFT JOIN offers o ON o.source_product_id=p.id
            LEFT JOIN supplier_http_captures c ON c.source_product_id=p.id LEFT JOIN offer_commercial_observations b ON b.capture_id=c.id
            GROUP BY s.code ORDER BY s.code""").fetchall()
        yug=connection.execute("""SELECT p.external_id,p.id,o.id,o.price,o.availability_normalized FROM source_products p
            JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
            WHERE s.code='ltt_yug' AND p.external_id IN ('312','297') ORDER BY p.external_id""").fetchall()
        assert len(yug)==2 and yug[0][1]!=yug[1][1] and yug[0][2]!=yug[1][2]
        state.update(status='LIVE_CANARY_BLOCKED',source='ltt_yug',stage='LIVE_CANARY',blocker='SOURCE_CATEGORY_UNRESOLVED',
                     root_cause=proof['root_cause'],stopped_at=datetime.now(timezone.utc).isoformat(),
                     source_products=after['source_products']['count'],offers=after['offers']['count'],
                     captures=after['supplier_http_captures']['count'],observations=after['offer_commercial_observations']['count'],
                     per_supplier_counts=[dict(supplier=r[0],source_products=r[1],distinct_external_id=r[2],offers=r[3],
                         distinct_source_product_id=r[4],captures=r[5],observations=r[6]) for r in counts],
                     protected_delta=[],duplicate_external_ids=0,yug_312_297_retained=True,yug_rows=yug,
                     blocked_yug_id_1_persisted=False,db_fingerprints=after)
        state['sources']['uss_stanko']=dict(status='PARTIAL_NOT_VERIFIED',attempts=len([r for r in state['rows'] if r['supplier']=='uss_stanko']),
            persisted=sum(r['supplier']=='uss_stanko' and r['outcome']=='PERSISTED' for r in state['rows']),reason='STOP_AFTER_YUG_MANDATORY_FAILURE')
        save(OUT/'LIVE_CANARY.json',state)
        save(OUT/'FINAL_DB_VERIFICATION.json',dict(status='READ_ONLY_VERIFIED',counts=state['per_supplier_counts'],protected_delta=[],
             fingerprints=after,source_products=state['source_products'],offers=state['offers'],observations=state['observations'],
             yug_rows=yug,blocked_yug_id_1_persisted=False))
        print(json.dumps({k:state[k] for k in ('status','source_products','offers','captures','observations','per_supplier_counts','protected_delta')},default=str))


if __name__=='__main__':
    main()
