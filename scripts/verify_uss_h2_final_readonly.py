"""Read-only final receipt after the completed full H2 URL-handoff RUN2."""
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from psycopg import sql
from scripts import resume_uss_h2_url_handoff as s
r,o=s.r,s.o


def main():
    result=r.load(s.PHASE/'RUN2/RESULT.json')
    assert result['status']=='LTT_USS_H2_TITLE_FIX_AND_RUN2_REVERIFIED'
    private,cfg=r.config();admin=dict(cfg,user='ltt_uss_admin',password=private['admin_password'])
    assert (admin['host'],admin['port'])==('127.0.0.1',55455)
    with o.offline_guard() as network:
      with psycopg.connect(**admin,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        c.execute("SET application_name='uss_h2_final_readonly_verification'")
        system=dict(version=c.execute('SHOW server_version').fetchone()[0],system_identifier=str(c.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0]))
        assert system==dict(version='17.11',system_identifier='7696153366151515704')
        current=o.snapshot(c);after=r.load(s.PHASE/'RUN2/AFTER.json');before=r.load(s.PHASE/'BEFORE_CORRECTION.json')
        assert not [k for k in current if r.encoded(current[k])!=r.encoded(after[k])]
        affected=r.load(s.PHASE/'AFFECTED_ROWS.json');receipt=r.load(s.PHASE/'BACKFILL_RECEIPT.json')
        newids=[a['db_verification']['source_product_id'] for a in receipt['rows']]
        assert len(set(newids))==5
        append_only={}
        for table,column in [('source_products','id'),('offers','source_product_id'),('supplier_http_captures','source_product_id'),('offer_commercial_observations','source_product_id')]:
            rows=c.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} AS t WHERE {} <> ALL(%s::bigint[])').format(sql.Identifier(table),sql.Identifier(column)),(newids,)).fetchall()
            values=sorted(row[0] for row in rows)
            fingerprint=dict(count=len(values),sha256=r.digest('\n'.join(values).encode('utf8')))
            assert fingerprint==before['fingerprints'][table],('OLD_ROWS_CHANGED',table)
            append_only[table]=dict(old_rows_unchanged=True,old_fingerprint=fingerprint,appended=current['totals'][table]-fingerprint['count'])
        facts=[]
        for a in affected:
            rows=c.execute('''SELECT row_to_json(p),row_to_json(o),row_to_json(cap),row_to_json(b)
                FROM source_products p JOIN suppliers sup ON sup.id=p.supplier_id
                JOIN offers o ON o.source_product_id=p.id
                JOIN supplier_http_captures cap ON cap.source_product_id=p.id
                JOIN offer_commercial_observations b ON b.capture_id=cap.id AND b.offer_id=o.id
                WHERE sup.code=%s AND p.external_id=%s''',('uss_stanko',a['external_id'])).fetchall()
            assert len(rows)==1
            p,offer,cap,b=rows[0]
            assert p['name']==offer['title']==a['title'] and p['sku']==offer['sku']==a['article']
            assert p['source_url']==p['canonical_url']==cap['requested_url']==cap['final_url']==a['url']
            assert p['raw_data']['source_identity']['raw_model']==a['model']
            assert p['raw_data']['source_identity']['site_internal_id']==a['external_id']
            assert p['raw_data']['enrichment_evidence']['availability_raw']==a['raw_availability']
            assert p['raw_data']['price_extraction']['raw_text']==b['price_raw']==a['raw_price']
            assert Decimal(str(offer['price']))==Decimal(str(b['price']))==Decimal(a['price'])
            assert offer['currency']==b['currency']==a['currency']=='RUB'
            assert offer['availability_normalized']==b['availability_normalized']=='in_stock'
            observed=datetime.fromisoformat(a['original_time'])
            assert datetime.fromisoformat(cap['observed_at'])==datetime.fromisoformat(b['observed_at'])==observed
            assert datetime.fromisoformat(p['last_success_at'])==observed
            assert cap['response_sha256'].strip()==a['response_sha256'] and cap['evidence_sha256'].strip()==a['evidence_sha256']
            assert p['catalog_product_id'] is None and not offer['active']
            revised=r.load(s.PHASE/a['revision']);old=o.ROOT/revised['reviewed_extraction']['original_journal']
            assert r.digest(old.read_bytes())==revised['reviewed_extraction']['original_journal_sha256']
            assert r.load(old)['outcome']=='AMBIGUOUS'
            facts.append(dict(index=a['index'],product=p,offer=offer,capture=cap,observation=b,historical_ambiguous_preserved=True))
        checks=s.integrity(c);clients=o.clients(c)
        r.save(s.PHASE/'APPEND_ONLY_AUDIT.json',append_only)
        r.save(s.PHASE/'FIVE_ROWS_FINAL_SQL.json',facts)
        r.save(s.PHASE/'FINAL_DB_VERIFICATION.json',dict(status='LIVE_VERIFIED_AFTER_WORKER_STOP',**system,**current,
            append_only=append_only,clients=clients,broken_references=checks,supplier_http=0,network=network,checked_at=r.now()))
        print(r.encoded(dict(status='FINAL_READONLY_PASS',totals=current['totals'],clients=clients,old_rows_unchanged=True,original_times_preserved=True)).decode(),flush=True)


if __name__=='__main__':main()
