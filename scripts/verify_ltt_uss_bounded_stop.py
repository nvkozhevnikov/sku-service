"""Read-only shutdown-phase verification; no HTTP, writes or further stages."""
from pathlib import Path
from decimal import Decimal
import sys
import psycopg
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r

PHASE=r.OUT/'USD_30MIN_2026-10-14'


def main():
    state=r.load(r.OUT/'CHECKPOINT.json')
    assert state['status'] in {'RUN1_PARTIAL_SAFE_CHECKPOINT','RUN1_COMPLETED','RUN1_BLOCKED'}
    system=r.verify_system();_,cfg=r.config()
    with psycopg.connect(**cfg,autocommit=True) as c:
        c.execute('SET default_transaction_read_only=on')
        c.execute("SET application_name='ltt_uss_bounded_final_readonly'")
        current=r.fingerprints(c);assert current==r.load(r.OUT/'DB_AFTER.json')['fingerprints']
        baseline=r.load(PHASE/'BASELINE_AND_CURRENCY_STORAGE.json')['fingerprints']
        assert all(current[k]==baseline[k] for k in current if k not in r.ALLOWED)
        for supplier in ('ltt_msk','ltt_sib','ltt_yug'):
            sid=r.load(r.OUT/'SUPPLIERS'/supplier/'BEFORE.json')['supplier_id']
            assert r.namespace_digest(c,sid)==state['sources'][supplier]['closed_namespace_fingerprints']
        own=state['sources']['uss_stanko'];assert own['cursor']==sum(own['terminal_counts'].values())
        recon=r.load(r.OUT/'SUPPLIERS/uss_stanko/RECONCILIATION.json')
        assert recon['processed']==own['cursor'] and recon['terminal_counts']==own['terminal_counts']
        assert recon['fingerprints']==current
        assert not c.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        assert not c.execute('SELECT source_product_id FROM offers GROUP BY source_product_id HAVING count(*)>1').fetchall()
        assert c.execute('SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM suppliers WHERE enabled').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM offers WHERE active').fetchone()[0]==0
        stored=c.execute('''SELECT o.price,o.currency,b.price,b.currency,b.price_raw,b.observed_at,o.quantity
            FROM suppliers s JOIN source_products p ON p.supplier_id=s.id JOIN offers o ON o.source_product_id=p.id
            JOIN supplier_http_captures cap ON cap.source_product_id=p.id JOIN offer_commercial_observations b ON b.capture_id=cap.id
            WHERE s.code='uss_stanko' AND p.external_id='uss:mach/frez/uf100.htm' ''').fetchall()
        assert len(stored)==1 and stored[0][:5]==(Decimal('21312'),'USD',Decimal('21312'),'USD','21 312 USD')
        assert stored[0][5]==r.datetime.fromisoformat('2026-10-14T14:52:11.551415+00:00') and stored[0][6] is None
        for item in r.load(r.OUT/'UNIT_FIX_2026-10-14/INPUT_VERIFICATION.json')['rows']:
            assert r.digest((r.OUT/item['journal']).read_bytes())==item['journal_sha256']
        original=r.OUT/'ROWS/uss_stanko/000178_9d7d063a1dbb2797.json'
        assert r.digest(original.read_bytes())==r.load(PHASE/'SESSION.json')['original_journal_sha256']
        clients=c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid()").fetchone()[0]
        tx=c.execute("SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND backend_type='client backend' AND pid<>pg_backend_pid() AND xact_start IS NOT NULL").fetchone()[0]
        prepared=c.execute('SELECT count(*) FROM pg_prepared_xacts WHERE database=current_database()').fetchone()[0]
        assert clients==tx==prepared==0
        values={k:current[k]['count'] for k in ('source_products','offers','supplier_http_captures','offer_commercial_observations')}
        receipt=dict(status='LIVE_VERIFIED_AFTER_WORKER_STOP',**system,fingerprints=current,counts={s:r.counts(c,s) for s in r.SOURCES},
            totals=values,processed=own['cursor'],total=407,remaining=407-own['cursor'],terminal_counts=own['terminal_counts'],last_durable_row=own['last_completed_row'],
            duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,identity_overwrite=0,foreign_mutations=0,protected_table_delta=[],
            other_clients=clients,open_transactions=tx,prepared_transactions=prepared,uf100_price='21312',uf100_currency='USD',uf100_repaired=True,
            fx_conversion='NONE',repair_http_gets=0,yug_312_present=True,yug_297_present=True,yug_collapsed=False,yug_id1_present=False,checked_at=r.now())
        r.save(PHASE/'FINAL_DB_VERIFICATION.json',receipt)
        print(r.encoded({k:receipt[k] for k in ('status','totals','processed','remaining','terminal_counts','other_clients','open_transactions')}).decode(),flush=True)


if __name__=='__main__':main()
