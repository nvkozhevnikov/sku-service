"""Explicit saved29 scope -> resolved-only RC source writes, no HTTP or links."""
from pathlib import Path
import sys,os,json,subprocess
from datetime import datetime,timezone
from dataclasses import asdict
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.audit_beka_visible_identity import inputs,OUT,PRIOR,save,load,sha
from universal_supplier.beka_visible_identity import assess,product_from_assessment,RESOLVED,STALE_URL
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.postgres import PostgresConfig,PostgresRepository
from scripts.run_full_supplier_qa import _preflight_rc
from scripts.intervesp_full_preflight import read_source_state

def pairs():
    rows=inputs(); scope={r['url'] for r in rows}; audits=[]; ready=[]
    for r in rows:
        f=Path(r['capture_path']); body=f.read_text(encoding='utf-8')
        a=assess(r['source'],r['url'],body,approved_urls=scope,operator_stale=r['url']==STALE_URL)
        a.update(origin=r['origin'],capture_path=str(f),capture_sha256=sha(f)); audits.append(a)
        if a['verdict']!=RESOLVED: continue
        p=product_from_assessment(a,body)
        if not p.site_internal_id: raise ValueError('Stable namespace source key missing')
        if r.get('capture'):
            args=dict(r['capture']); args['observed_at']=datetime.fromisoformat(args['observed_at'])
            capture=HttpCapture(**args)
            if capture.evidence_sha256!=sha(f): raise ValueError('Saved HTTP evidence digest changed')
        else:
            capture=HttpCapture(requested_url=r['url'],final_url=r['url'],http_status=200,content_type='text/html',
                observed_at=datetime.fromtimestamp(f.stat().st_mtime,timezone.utc),response_sha256=sha(f),
                evidence_sha256=sha(f),evidence_ref=str(f),capture_scope='detail',response_hash_basis='sanitized_archive',
                diagnostics=('offline_saved_detail_replay','archive_mtime_not_verified_original_fetch_time'))
        ready.append((p,capture,f,sha(f)))
    if len(ready)!=24: raise ValueError('Resolved count changed; stop before writes')
    return audits,ready

def sql_ids(repo):
    with repo.connection.cursor() as cur:
        cur.execute("SELECT s.code,sp.external_id,sp.id,o.id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id AND o.supplier_id=s.id")
        return {tuple(r[:2]):tuple(r[2:]) for r in cur.fetchall()}

def main():
    if (OUT/'APPLIED.json').exists(): raise ValueError('Checkpoint exists; no automatic reapplication')
    audits,ready=pairs(); before=read_source_state()
    os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
    repo=PostgresRepository(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_runtime',None,'disable'))
    report={'stage':'PREFLIGHT_PASS','http_gets':0,'before':before,'rows':[],'replay':[],
        'prior_matching_sha256':sha(PRIOR/'MATCHING_AFTER.json')}
    OUT.mkdir(exist_ok=True); save(OUT/'SOURCE_IDENTITY_AUDIT.json',audits)
    try:
        _preflight_rc(repo); old=sql_ids(repo)
        with repo.connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended('beka-gap-ledger-explicit-job',0))")
            if not cur.fetchone()[0]: raise RuntimeError('Another Beka gap job active')
        keys=[(p.supplier.replace('-','_'),p.site_internal_id) for p,c,f,d in ready]
        if len(set(keys))!=24 or any(k in old for k in keys): raise ValueError('Approved source key already exists or duplicate')
        backup=OUT/'BEFORE.dump'
        if backup.exists(): raise ValueError('Preserve existing backup')
        binary=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
        subprocess.run([str(binary),'-h','127.0.0.1','-p','55449','-U','rc_admin','-d','universal_supplier_server','-Fc','--no-owner','-f',str(backup)],check=True,capture_output=True)
        report['backup']={'path':str(backup),'sha256':sha(backup),'bytes':backup.stat().st_size}
        save(OUT/'APPLIED.json',report)
        for p,c,f,d in ready:
            read_source_state(); _preflight_rc(repo)
            if sha(f)!=d: raise ValueError('Saved evidence changed')
            result=repo.persist_commercial_observation(p,c)
            report['rows'].append({'source':p.supplier.replace('-','_'),'external_id':p.site_internal_id,
                'url':p.source_url,'model':p.supplier_model,'product':asdict(p),'capture':asdict(c),
                **{k:v for k,v in result.items() if k!='ingest_counts'}})
            save(OUT/'APPLIED.json',report)
        after=read_source_state(); current=sql_ids(repo)
        if any(current.get(k)!=v for k,v in old.items()): raise RuntimeError('Old product/offer IDs changed')
        if after['table_counts']['source_products']-before['table_counts']['source_products']!=24: raise RuntimeError('Source delta mismatch')
        for table in ('product_matches','catalog_products','suppliers'):
            if after['table_counts'][table]!=before['table_counts'][table]: raise RuntimeError('Forbidden SQL table changed')
        for p,c,f,d in ready:
            _preflight_rc(repo); result=repo.persist_commercial_observation(p,c)
            if not result['exact_noop']: raise RuntimeError('Replay not exact no-op')
            report['replay'].append({'source':p.supplier.replace('-','_'),'external_id':p.site_internal_id,'exact_noop':True})
        replay_after=read_source_state()
        if replay_after!=after: raise RuntimeError('Replay state changed')
        report.update(stage='INGEST_AND_REPLAY_VERIFIED',after=after,new_source_records=24,old_pks_preserved=True,
            replay_after=replay_after,finished_at=datetime.now(timezone.utc).isoformat())
        save(OUT/'APPLIED.json',report)
        print(json.dumps({k:report[k] for k in ('stage','new_source_records','old_pks_preserved','http_gets')},default=str))
    except Exception as e:
        report.update(stage='STOPPED_NO_AUTOMATIC_RETRY',error=str(e)); save(OUT/'APPLIED.json',report); raise
    finally: repo.close()

if __name__=='__main__': main()
