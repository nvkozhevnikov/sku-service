"""Three exact operator decisions, saved evidence only, resumable narrow RC job."""
from pathlib import Path
import sys,os,subprocess,json
from dataclasses import asdict
from datetime import datetime,timezone
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from scripts.audit_beka_visible_identity import load,save,sha
from scripts.apply_beka_visible_identity import sql_ids
from scripts.intervesp_full_preflight import read_source_state
from scripts.run_full_supplier_qa import _preflight_rc
from universal_supplier.beka_operator_decisions import apply_decision,operator_product,DECISIONS
from universal_supplier.beka_visible_identity import RESOLVED,STALE,INSUFFICIENT
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.postgres import PostgresConfig,PostgresRepository
BASE=ROOT/'reports/RC_LOCAL/BEKA_VISIBLE_IDENTITY_2026-10-01'
OUT=ROOT/'reports/RC_LOCAL/BEKA_OPERATOR_CLOSED_2026-10-01'
PREVIOUS=BASE/'CANONICAL_VISIBLE_CONTEXT'

def prepare():
    audits=[apply_decision(a) for a in load(BASE/'SOURCE_IDENTITY_AUDIT.json')]
    for a in audits:
        a.setdefault('ledger_disposition','RESOLVED_SAVED_VISIBLE_IDENTITY' if a['verdict']==RESOLVED else 'EXCLUDED_OPERATOR_SOURCE404' if a['verdict']==STALE else 'EXCLUDED_OPERATOR_INSUFFICIENT_IDENTITY')
    if sum(a['verdict']==RESOLVED for a in audits)!=27: raise ValueError('Final source ledger expected27 resolved and2 exclusions')
    pairs=[]
    for a in audits:
        if a['url'] not in DECISIONS: continue
        f=Path(a['capture_path'])
        if sha(f)!=a['capture_sha256']: raise ValueError('Saved capture changed')
        p=operator_product(a,f.read_text(encoding='utf-8'))
        c=HttpCapture(requested_url=a['url'],final_url=a['url'],http_status=200,content_type='text/html',
            observed_at=datetime.fromtimestamp(f.stat().st_mtime,timezone.utc),response_sha256=sha(f),evidence_sha256=sha(f),
            evidence_ref=str(f),capture_scope='detail',response_hash_basis='sanitized_archive',
            diagnostics=('offline_saved_detail_replay','archive_mtime_not_verified_original_fetch_time','exact_operator_card_equivalence'))
        pairs.append((p,c))
    return audits,pairs

def checkpoint(stage,**extra):
    save(OUT/'CHECKPOINT.json',{'stage':stage,'resume_from':str(OUT/'CHECKPOINT.json'),
        'resume_command':'uv run --offline --with-requirements requirements.txt python scripts/close_beka_operator_ledger.py',
        'no_http':True,'no_global_matching':True,**extra})

def main():
    audits,pairs=prepare(); OUT.mkdir(exist_ok=True)
    save(OUT/'SOURCE_IDENTITY_AUDIT.json',audits)
    if not (OUT/'CHECKPOINT.json').exists(): checkpoint('PREPARED_NOT_APPLIED',approved_urls=list(DECISIONS))
    if (OUT/'CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json').exists():
        print('Matching checkpoint already saved; do not rerun'); return
    state=read_source_state()
    os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
    repo=PostgresRepository(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_runtime',None,'disable'))
    path=OUT/'APPLIED.json'
    report=load(path) if path.exists() else {'stage':'PREPARED','before':state,'rows':[],'replay':[],'http_gets':0,
        'prior_matching_sha256':sha(PREVIOUS/'MATCHING_AFTER.json')}
    try:
        _preflight_rc(repo)
        with repo.connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended('beka-gap-ledger-explicit-job',0))")
            if not cur.fetchone()[0]: raise RuntimeError('Another Beka job running')
        old=sql_ids(repo)
        backup=OUT/'BEFORE.dump'
        if not path.exists():
            if any((p.supplier.replace('-','_'),p.site_internal_id) in old for p,c in pairs): raise ValueError('Untracked approved source already exists')
            if backup.exists(): raise ValueError('Untracked backup exists; inspect checkpoint')
            binary=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
            subprocess.run([str(binary),'-h','127.0.0.1','-p','55449','-U','rc_admin','-d','universal_supplier_server','-Fc','--no-owner','-f',str(backup)],check=True,capture_output=True)
            report['backup']={'path':str(backup),'sha256':sha(backup),'bytes':backup.stat().st_size}
            report['old_ids']=[{'source':k[0],'external_id':k[1],'ids':v} for k,v in old.items()]
            save(path,report); checkpoint('BACKUP_VERIFIED_INGEST_PENDING')
        if sha(backup)!=report['backup']['sha256']: raise ValueError('Backup changed')
        if report['stage']!='INGEST_AND_REPLAY_VERIFIED':
            completed={(r['source'],r['external_id']) for r in report['rows']}
            for p,c in pairs:
                _preflight_rc(repo); read_source_state()
                result=repo.persist_commercial_observation(p,c)
                key=p.supplier.replace('-','_'),p.site_internal_id
                if key not in completed:
                    report['rows'].append({'source':key[0],'external_id':key[1],'url':p.source_url,'product':asdict(p),'capture':asdict(c),
                        **{k:v for k,v in result.items() if k!='ingest_counts'}})
                    save(path,report); checkpoint('INGEST_PARTIAL',persisted=len(report['rows']))
            after=read_source_state(); current=sql_ids(repo)
            if any(current.get((r['source'],r['external_id']))!=tuple(r['ids']) for r in report['old_ids']): raise RuntimeError('Old PKs changed')
            before=report['before']['table_counts']
            for table in ('source_products','offers','supplier_http_captures','offer_commercial_observations'):
                if after['table_counts'][table]-before[table]!=3: raise RuntimeError('Expected exactly3 additions')
            for table in ('suppliers','product_matches','catalog_products'):
                if after['table_counts'][table]!=before[table]: raise RuntimeError('Forbidden table count changed')
            report['replay']=[]
            for p,c in pairs:
                _preflight_rc(repo); r=repo.persist_commercial_observation(p,c)
                if not r['exact_noop']: raise RuntimeError('Replay not exact no-op')
                report['replay'].append({'source':p.supplier.replace('-','_'),'external_id':p.site_internal_id,'exact_noop':True})
            if read_source_state()!=after: raise RuntimeError('Replay state changed')
            report.update(stage='INGEST_AND_REPLAY_VERIFIED',after=after,new_source_rows=3,old_pks_preserved=True)
            save(path,report); checkpoint('INGEST_AND_REPLAY_VERIFIED_MATCHING_PENDING')
    finally: repo.close()
    from scripts.match_beka_visible_identity import main as match
    match(out_dir=OUT,previous_dir=PREVIOUS,expected_count=3)
    checkpoint('MATCHING_SAVED_FINAL_VERIFICATION_PENDING',latest_matching='CANONICAL_VISIBLE_CONTEXT/MATCHING_AFTER.json')
    print('Scoped ingestion/replay/matching saved')

if __name__=='__main__': main()
