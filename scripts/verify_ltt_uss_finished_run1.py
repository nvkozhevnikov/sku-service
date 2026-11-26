"""Read-only RUN1 closeout and explicit non-RUB source-offer audit."""
from pathlib import Path
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r
from universal_supplier.adapters.ltt_uss_html import price_value
from universal_supplier.commercial import PriceState

PHASE=r.OUT/'FINISH_USS_2026-10-15'


def main():
    state=r.load(r.OUT/'CHECKPOINT.json');assert state['status'] in {'RUN1_COMPLETED','RUN1_BLOCKED','RUN1_PARTIAL_SAFE_CHECKPOINT'}
    system=r.verify_system();_,cfg=r.config()
    with psycopg.connect(**cfg,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        c.execute("SET application_name='ltt_uss_finished_run1_readonly'")
        current=r.fingerprints(c);assert current==r.load(r.OUT/'DB_AFTER.json')['fingerprints']
        baseline=r.load(PHASE/'LIVE_PREFLIGHT.json')['fingerprints']
        assert all(current[k]==baseline[k] for k in current if k not in r.ALLOWED)
        receipts={}
        for supplier in r.SOURCES:
            own=state['sources'][supplier];assert own['cursor']==sum(own['terminal_counts'].values())
            sid=r.load(r.OUT/'SUPPLIERS'/supplier/'BEFORE.json')['supplier_id']
            if own['status']=='RUN1_COMPLETED':
                assert r.namespace_digest(c,sid)==own['closed_namespace_fingerprints']
                assert own['cursor']==own['intended_total']
            filename='CLOSED_SOURCE_VERIFICATION.json' if state['status']=='RUN1_COMPLETED' else ('CLOSED_SOURCE_VERIFICATION.json' if supplier!='uss_stanko' else 'RECONCILIATION.json')
            receipt=r.load(r.OUT/'SUPPLIERS'/supplier/filename)
            assert receipt['processed']==own['cursor'] and receipt['terminal_counts']==own['terminal_counts']
            assert all(receipt[k]==0 for k in ('duplicate_external_ids','duplicate_offer_links','unexplained_missing','foreign_mutations','identity_overwrite'))
            receipts[supplier]=receipt
        if state['status']=='RUN1_COMPLETED':
            all_source=r.load(r.OUT/'ALL_SOURCE_RECONCILIATION.json');assert all_source['status']=='RUN1_APPLIED_AND_VERIFIED'
            assert [state['sources'][s]['cursor'] for s in r.SOURCES]==[369,1266,592,407]
        assert not c.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        assert not c.execute('SELECT source_product_id FROM offers GROUP BY source_product_id HAVING count(*)>1').fetchall()
        assert c.execute('SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM suppliers WHERE enabled').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM offers WHERE active').fetchone()[0]==0
        yug=c.execute("SELECT p.external_id,p.id,o.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id WHERE s.code='ltt_yug' AND p.external_id IN ('1','312','297') ORDER BY p.external_id").fetchall()
        assert len(yug)==2 and {row[0] for row in yug}=={'312','297'} and yug[0][1]!=yug[1][1] and yug[0][2]!=yug[1][2]
        audit=[]
        rows=c.execute('''SELECT s.code,p.external_id,p.canonical_url,o.id,o.price,o.currency,o.raw_data,p.raw_data,p.last_success_at
            FROM offers o JOIN suppliers s ON s.id=o.supplier_id JOIN source_products p ON p.id=o.source_product_id
            WHERE s.code=ANY(%s) AND o.currency IS NOT NULL AND o.currency NOT IN ('','RUB') ORDER BY s.code,p.external_id''',(list(r.SOURCES),)).fetchall()
        for supplier,external,url,oid,amount,currency,offer_raw,source_raw,observed in rows:
            raw=offer_raw['price_extraction']['raw_text'];assert raw==source_raw['price_extraction']['raw_text']
            extracted=price_value(raw,identity=external,url=url,path=offer_raw['price_extraction']['source_path'],currency=None)
            assert extracted.state==PriceState.NUMERIC_PUBLIC and extracted.current_price==amount and extracted.currency==currency
            assert amount is not None and amount>0
            capture=offer_raw['commercial_capture'];assert observed is not None
            evidence=c.execute('''SELECT b.price,b.currency,b.price_raw,b.extraction_evidence,c.response_sha256,c.evidence_sha256
                FROM offer_commercial_observations b JOIN supplier_http_captures c ON c.id=b.capture_id
                WHERE b.offer_id=%s AND b.observed_at=%s AND c.response_sha256=%s''',(oid,observed,capture['response_sha256'])).fetchall()
            assert len(evidence)==1 and evidence[0][:3]==(amount,currency,raw)
            assert evidence[0][3]['raw_text']==raw
            assert not any(k in offer_raw or k in source_raw for k in ('fx_rate','converted_price','price_rub','conversion_rate'))
            audit.append(dict(supplier=supplier,external_id=external,url=url,offer_id=oid,price=str(amount),currency=currency,raw_price=raw,observed_at=str(observed),
                response_sha256=evidence[0][4].strip(),evidence_sha256=evidence[0][5].strip(),fx_conversion='NONE'))
        currencies=[row[0] for row in c.execute("SELECT DISTINCT o.currency FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code=ANY(%s) AND o.currency IS NOT NULL AND o.currency<>'' ORDER BY o.currency",(list(r.SOURCES),)).fetchall()]
        r.save(PHASE/'NON_RUB_AUDIT.json',dict(status='LIVE_VERIFIED_READ_ONLY',non_rub_offers_count=len(audit),currencies_seen=sorted({row['currency'] for row in audit}),
            all_offer_currencies_seen=currencies,offers=audit,explicit_amount_and_source_currency_verified=True,raw_source_price_retained=True,fx_conversion='NONE',offer_selection_run=False,checked_at=r.now()))
        clients=c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0]
        transactions=c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid() AND xact_start IS NOT NULL").fetchone()[0]
        prepared=c.execute('SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()').fetchone()[0]
        assert clients==transactions==prepared==0
        receipt=dict(status='LIVE_VERIFIED_AFTER_WORKER_STOP',**system,fingerprints=current,counts={s:r.counts(c,s) for s in r.SOURCES},
            totals={k:current[k]['count'] for k in ('source_products','offers','supplier_http_captures','offer_commercial_observations')},
            sources={s:dict(processed=state['sources'][s]['cursor'],total=state['sources'][s]['intended_total'],terminal_counts=state['sources'][s]['terminal_counts']) for s in r.SOURCES},
            duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,identity_overwrite=0,foreign_mutations=0,protected_table_delta=[],
            yug_rows=yug,yug_312_present=True,yug_297_present=True,yug_collapsed=False,yug_id1_present=False,
            non_rub_offers_count=len(audit),currencies_seen=sorted({row['currency'] for row in audit}),other_clients=clients,open_transactions=transactions,prepared_transactions=prepared,checked_at=r.now())
        r.save(PHASE/'FINAL_DB_VERIFICATION.json',receipt)
        print(r.encoded({k:receipt[k] for k in ('status','totals','sources','non_rub_offers_count','currencies_seen','other_clients','open_transactions')}).decode(),flush=True)


if __name__=='__main__':main()
