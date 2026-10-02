"""Explicit current Partner/Optimum refresh into the one approved RC database."""
from pathlib import Path
import sys,os,json,subprocess,hashlib
from collections import Counter
import psycopg
from psycopg.rows import dict_row
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.intervesp_full_preflight import read_source_state
from universal_supplier.rc_catalog_refresh import apply_one
OUT=ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02'
COMMIT='a54c139e728e60aff2e7ae2e44a81c66cb9fa70b'
def load(p):return json.loads(p.read_text(encoding='utf-8'))
def save(name,v):(OUT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def connect(readonly=False):
    return psycopg.connect(host='127.0.0.1',port=55449,dbname='universal_supplier_server',user='rc_runtime',
        passfile=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf'),connect_timeout=5,
        autocommit=True,options='-c default_transaction_read_only='+('on' if readonly else 'off'))
def state(c):
    with c.cursor(row_factory=dict_row) as cur:
        cur.execute("SELECT s.code,sp.id,sp.external_id,sp.catalog_product_id,sp.active,sp.name,sp.raw_data,o.id offer_id,o.active offer_active,o.price,o.availability_normalized FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default' ORDER BY s.code,sp.external_id")
        rows=cur.fetchall()
        cur.execute('SELECT code,enabled FROM suppliers ORDER BY code');suppliers=cur.fetchall()
        totals={t:cur.execute(f'SELECT count(*) n FROM {t}').fetchone()['n'] for t in ('source_products','offers','supplier_http_captures','offer_commercial_observations','product_matches','catalog_products')}
    return {'suppliers':suppliers,'totals':totals,'rows':rows,'source_counts':dict(Counter(r['code'] for r in rows))}
def main():
    proof=load(OUT/'EVIDENCE_VERIFIED.json');assert proof['commit']==COMMIT
    if (OUT/'APPLIED.json').exists():raise RuntimeError('Already applied; resume from APPLIED.json, do not ingest again')
    pre=read_source_state()
    evidence=load(OUT/'CURRENT_COMMERCIAL_EVIDENCE.json')
    kwargs=connect()
    try:
        before=state(kwargs)
        assert before['source_counts']=={'partner_st':1222,'optimum':1351,'intervesp':1642,'beka_mak':109,'beka_mak_tr':78}
        save('BEFORE_SQL.json',{**before,'identity':pre['identity'],'data_directory':pre['data_directory']})
        binary=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
        backup=OUT/'RC_BEFORE_REFRESH.dump'
        env=dict(os.environ);env['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
        if not backup.exists():
            subprocess.run([str(binary),'-h','127.0.0.1','-p','55449','-U','rc_admin','-d','universal_supplier_server','-Fc','--no-owner','-f',str(backup)],env=env,check=True,capture_output=True)
        else:
            listing=subprocess.check_output([str(binary.with_name('pg_restore.exe')),'--list',str(backup)],env=env)
            assert b'universal_supplier_server' in listing and backup.stat().st_size>0
        observed=subprocess.check_output(['git','-C',str(ROOT),'show','-s','--format=%cI',COMMIT]).decode().strip()
        applied=[];replays=[]
        # One atomic guarded transaction across both current catalogs. No missing
        # reconciliation/deactivation, scheduler hooks or canonical assignment.
        read_source_state()
        with kwargs.transaction(),kwargs.cursor() as cur:
            cur.execute("SELECT current_database(),current_user,system_identifier,pg_is_in_recovery() FROM pg_control_system()")
            assert cur.fetchone()==('universal_supplier_server','rc_runtime',7691270601420084116,False)
            for supplier in ('partner_st','optimum'):
                for row in evidence[supplier]:
                    result=apply_one(cur,row,commit=COMMIT,observed_at=observed,evidence_ref=f'git://{COMMIT}/catalog_refresh/evidence/{supplier}/CATALOG.jsonl#{row["source_identity"]}')
                    applied.append({'supplier':supplier,**result})
            interim=state(kwargs)
            for supplier in ('partner_st','optimum'):
                for row in evidence[supplier]:
                    result=apply_one(cur,row,commit=COMMIT,observed_at=observed,evidence_ref=f'git://{COMMIT}/catalog_refresh/evidence/{supplier}/CATALOG.jsonl#{row["source_identity"]}')
                    assert result['exact_noop'];replays.append({'supplier':supplier,**result})
            after=state(kwargs);assert interim==after,'Replay changed projection/capture/observation'
            old={(r['code'],r['external_id']):r for r in before['rows']}
            new={(r['code'],r['external_id']):r for r in after['rows']}
            for key,prev in old.items():
                assert key in new
                for field in ('id','offer_id','catalog_product_id','active','offer_active'):assert new[key][field]==prev[field]
                if key[0] not in ('partner_st','optimum'):assert new[key]==prev
            assert before['suppliers']==after['suppliers']
            assert after['source_counts']=={'partner_st':1225,'optimum':1353,'intervesp':1642,'beka_mak':109,'beka_mak_tr':78}
            assert after['totals']['source_products']==before['totals']['source_products']+5
            assert after['totals']['offers']==before['totals']['offers']+5
            for t in ('supplier_http_captures','offer_commercial_observations'):assert after['totals'][t]==before['totals'][t]+2578
            for t in ('product_matches','catalog_products'):assert after['totals'][t]==before['totals'][t]
            assert all(r['price'] is None for r in after['rows'] if r['code']=='optimum' and r['external_id'] in {x['source_identity'] for x in evidence['optimum'] if x['price_state']=='price_on_request'})
        post=read_source_state();save('AFTER_SQL.json',after)
        save('APPLIED.json',{'commit':COMMIT,'source_counts':after['source_counts'],'counts_before':before['totals'],'counts_after':after['totals'],
            'new_source_products':sum(r.get('new_source_product',False) for r in applied),'applied':applied,'replay':replays,
            'replay_exact_no_op':True,'old_pk_links_activation_preserved':True,'optimum_provisional':True,
            'backup_path':str(backup),'backup_sha256':hashlib.sha256(backup.read_bytes()).hexdigest(),'postflight_safety_counts':post['safety_counts']})
        print(json.dumps({'source_counts':after['source_counts'],'counts':after['totals'],'new':5,'replays_exact_no_op':2578}))
    finally:kwargs.close()
if __name__=='__main__':main()
