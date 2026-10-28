"""Proof of the recorded zero-product attempt; no old RESUME/status rewrite.

Fresh inputs on a new exact approval are allowed only when every old product
URL is durably NOT_REQUESTED and SQL confirms zero captures since that start.
Any COMMITTED/cached/inflight/uncertain product rejects this narrow path.
"""
from pathlib import Path
import hashlib,json,subprocess
from .automation_execution import require,Session,ROOT
from .source_hourly import checkpoint
from .source_resume import digest
from .source_recovery import normal
from .runtime_config import guarded_connect

KIND='commercial-not-requested-continuation/v1'
RECEIPT_KIND='commercial-receipt-continuation/v1'
# Reuse reads historical receipts, never runs an old parser under a new engine.
REUSE_CODE_PATHS={
    'universal_supplier/control_plane/commercial_continuation.py',
    'universal_supplier/control_plane/commercial_refresh.py',
    'universal_supplier/control_plane/automation_report.py',
}


def file_sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def check_unstarted(folder,status,error,run_rows):
    """No runner input/run/response: native expired-lease history is retained."""
    require(status=='cancelled' or (status=='stale_failed' and
        error=='ORCHESTRATOR_LEASE_EXPIRED_UNCERTAIN_RESULT'),'CONTINUATION_UNSTARTED_STATE_NOT_SAFE')
    require(not run_rows,'CONTINUATION_UNLINKED_CRAWL_RUN_EXISTS')
    require(not any((Path(folder)/name).exists() for name in
        ('input.json','checkpoint.json','result.json','responses','captures')),
        'CONTINUATION_UNSTARTED_INPUT_OR_HTTP_EVIDENCE_EXISTS')
    return {'no_started_run_input_or_response':True,'native_status_retained':status}


def check_files(folder,code,jid,runtime,expected,previous_engine):
    folder=Path(folder);original=json.loads((folder/'input.json').read_text(encoding='utf-8'))
    cp=checkpoint(folder/'checkpoint.json');result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    require(original['supplier_code']==code and original['mode']=='REFRESH' and original['entries']==expected,
        'CONTINUATION_FROZEN_INPUT_CHANGED')
    require(original['engine_sha256']==previous_engine and digest(original)==runtime['input_sha256'],
        'CONTINUATION_PREVIOUS_ENGINE_OR_INPUT_CHANGED')
    require(cp['source']==code and cp['job_id']==jid and cp['manifest_sha256']==runtime['input_sha256'],
        'CONTINUATION_CHECKPOINT_IDENTITY_CHANGED')
    require(not cp['terminal'] and not cp['inflight'] and sorted(cp['frontier'])==sorted(e['url'] for e in expected),
        'CONTINUATION_PRODUCTS_NOT_PROVEN_UNREQUESTED')
    require(result['owner_job_id']==jid and result['input_sha256']==runtime['input_sha256'] and
        result['sql_committed']==runtime.get('sql_committed')==0 and result['processed']==0 and
        result['canary']['product_requests']==[] and not runtime.get('resume_uncertain'),
        'CONTINUATION_PRODUCT_OPERATION_ALREADY_ATTEMPTED')
    return {'input_sha256':runtime['input_sha256'],'input_file_sha256':file_sha(folder/'input.json'),
        'checkpoint_sha256':file_sha(folder/'checkpoint.json'),'result_sha256':file_sha(folder/'result.json')}


def proof(config,previous_path,previous_sha):
    path=Path(previous_path).resolve()
    require(file_sha(path)==previous_sha,'CONTINUATION_PREVIOUS_APPROVAL_CHANGED')
    old=json.loads(path.read_text(encoding='utf-8'))
    require(old.get('purpose')=='confirmed-commercial-refresh/v1' and old.get('commercial_only') is True,
        'CONTINUATION_WRONG_PREVIOUS_OPERATION')
    targets=[e for r in old['suppliers'].values() for e in r['targets']]
    require(len(targets)==1270 and len({e['source_product_id'] for e in targets})==1270,'CONTINUATION_EXACT_1270_REQUIRED')
    sources={code for code,r in old['suppliers'].items() if r['targets']};seen=set();jobs=[]
    with guarded_connect(config,autocommit=True,options='-c default_transaction_read_only=on -c statement_timeout=120000') as c:
        Session(config,old,previous_sha,ROOT,approval_path=path).check_mappings(c)
        require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running')").fetchall(),'CONTINUATION_ACTIVE_JOB')
        rows=c.execute('''SELECT j.id,s.code,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata
            ,j.error_message FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
            WHERE j.settings_snapshot->'automation'->>'approval_sha256'=%s ORDER BY j.id''',(previous_sha,)).fetchall()
        for jid,code,status,run,snapshot,metadata,error in rows:
            require(code in sources and code not in seen,'CONTINUATION_DUPLICATE_OR_UNKNOWN_SOURCE')
            seen.add(code);rule=old['suppliers'][code]
            expected=[{'source_product_id':e['source_product_id'],'source_external_id':e['external_id'],'url':e['source_url']} for e in rule['targets']]
            require(not snapshot.get('source_batch') and snapshot.get('source_input',{}).get('entries')==expected,
                'CONTINUATION_JOB_SCOPE_CHANGED')
            require(snapshot.get('automation',{}).get('phase')=='REFRESH','CONTINUATION_NON_REFRESH_JOB')
            record={'job_id':jid,'source':code,'status':status,'run_id':run,'scope_count':len(expected)}
            folder=ROOT/'reports/source-runner-evidence'/code/('job-'+str(jid))
            if run is None:
                require(snapshot['source_input'].get('engine_sha256')==old['engine'],
                    'CONTINUATION_PREVIOUS_ENGINE_OR_INPUT_CHANGED')
                run_rows=c.execute("SELECT id FROM crawl_runs WHERE metadata->>'job_id'=%s",(str(jid),)).fetchall()
                record.update(check_unstarted(folder,status,error,run_rows))
            else:
                require(status=='partial','CONTINUATION_STARTED_STATE_NOT_SAFE')
                rt=(metadata or {}).get('source_runtime') or {}
                require(Path(rt['checkpoint']).resolve()==(folder/'checkpoint.json').resolve(),'CONTINUATION_CHECKPOINT_PATH_CHANGED')
                record.update(check_files(folder,code,jid,rt,expected,old['engine']))
            jobs.append(record)
        require(seen==sources,'CONTINUATION_MISSING_SOURCE_JOB')
        after=c.execute('''SELECT count(*) FROM supplier_http_captures WHERE source_product_id=ANY(%s)
            AND observed_at>=%s::timestamptz''',([e['source_product_id'] for e in targets],old['not_before'])).fetchone()[0]
        require(after==0,'CONTINUATION_CAPTURE_OR_COMMITTED_EXISTS')
    return {'kind':KIND,'previous_path':str(path),'previous_approval_sha256':previous_sha,
        'previous_head':old['head'],'previous_engine':old['engine'],'previous_scope_sha256':old['scope_sha256'],
        'target':old['target'],'not_before':old['not_before'],'execution_until':old['execution_until'],
        'jobs':jobs,'scope_count':1270,'committed':0,'product_dispatches':0,'product_inflight':0,
        'captures_since_previous_start':after,'original1282_exact':True,
        'method':'new frozen inputs for proven NOT_REQUESTED URLs; no old statuses/checkpoints altered'}


def attach(config,value,previous_path,previous_sha):
    p=proof(config,previous_path,previous_sha)
    old=json.loads(Path(previous_path).read_text(encoding='utf-8'))
    require(value['scope_sha256']==p['previous_scope_sha256'] and value['target']==p['target'] and
        value['not_before']==p['not_before'] and value['execution_until']==p['execution_until'],
        'CONTINUATION_SCOPE_TARGET_OR_WINDOW_CHANGED')
    for code,r in value['suppliers'].items():
        require(all(r[k]==old['suppliers'][code][k] for k in ('targets','limits','policy','max_queue_attempts')),
            'CONTINUATION_LIMITS_OR_POLICIES_CHANGED')
    value['operation_id']=old.get('operation_id',previous_sha)
    value['continuation']=p
    return value


def validate_live(config,value):
    p=value.get('continuation')
    if not p:return
    if p.get('kind')==RECEIPT_KIND:
        old=json.loads(Path(p['previous_path']).read_text(encoding='utf-8'))
        require(all(value[k]==old[k] for k in ('scope_sha256','target','not_before','execution_until',
            'suppliers','host_policies','mappings1282','matrix_sha256','protected_live11_batch_id')) and
            value.get('operation_id')==old.get('operation_id',p['previous_approval_sha256']) and
            value['protected_jobs']==p['protected_jobs'],'CONTINUATION_ORIGINAL_CONTRACT_CHANGED')
        require(p==receipt_proof(config,p['previous_path'],p['previous_approval_sha256']),
            'CONTINUATION_DURABLE_RECEIPTS_CHANGED')
        return
    require(p==proof(config,p['previous_path'],p['previous_approval_sha256']), 'CONTINUATION_DURABLE_PROOF_CHANGED')


def reused_sources(value):
    p=value.get('continuation') or {}
    return {j['source'] for j in p.get('jobs',[]) if p.get('kind')==RECEIPT_KIND and j['disposition']=='REUSE_TERMINAL'}


def quarantine_ids(value):
    p=value.get('continuation') or {}
    return {i for j in p.get('jobs',[]) for i in j.get('quarantine_ids',[]) if p.get('kind')==RECEIPT_KIND}


def compatibility(old):
    changed=subprocess.check_output(['git','diff',old['head'],'--name-only','--',
        'universal_supplier','sterbrust_matching'],cwd=ROOT,text=True).splitlines()
    require(set(changed)<=REUSE_CODE_PATHS,'CONTINUATION_ENGINE_COMPATIBILITY_REQUIRES_REVIEW')
    return {p:file_sha(ROOT/p) for p in sorted(changed)}


def verify_capture(folder,cap):
    path=Path(cap['evidence_ref']).resolve()
    require(path.is_relative_to((Path(folder)/'captures').resolve()),'CONTINUATION_CAPTURE_PATH_CHANGED')
    require(file_sha(path)==cap['evidence_sha256'],'CONTINUATION_CAPTURE_BYTES_CHANGED')


def check_receipt_files(folder,code,jid,run,status,runtime,expected,previous_engine,*,verify=verify_capture):
    """Only entire accounted phases OR entirely unrequested phases are reusable.

    Mixed, cached and uncertain phases need native RESUME, not this admission.
    """
    folder=Path(folder)
    original=json.loads((folder/'input.json').read_text(encoding='utf-8'))
    cp=checkpoint(folder/'checkpoint.json')
    result=json.loads((folder/'result.json').read_text(encoding='utf-8'))
    require(original['supplier_code']==code and original['mode']=='REFRESH' and original['entries']==expected,
        'CONTINUATION_FROZEN_INPUT_CHANGED')
    require(original['engine_sha256']==previous_engine and digest(original)==runtime['input_sha256'],
        'CONTINUATION_PREVIOUS_ENGINE_OR_INPUT_CHANGED')
    require(cp['source']==code and cp['job_id']==jid and cp['manifest_sha256']==runtime['input_sha256'] and
        result['owner_job_id']==jid and result['crawl_run_id']==run and result['input_sha256']==runtime['input_sha256'],
        'CONTINUATION_CHECKPOINT_IDENTITY_CHANGED')
    record={'disposition':'REUSE_TERMINAL','committed':0,'quarantined':0,'quarantine_ids':[],
        'files':{n:file_sha(folder/(n+'.json')) for n in ('input','checkpoint','result')},'receipts':{}}
    if status=='partial' and runtime.get('termination_reason')=='SOURCE_ROBOTS_UNAVAILABLE':
        record.update(check_files(folder,code,jid,runtime,expected,previous_engine))
        require(result.get('stop_scope')=='supplier' and not result.get('global_stop_reason'),
            'CONTINUATION_ROBOTS_NOT_SOURCE_LOCAL')
        record['disposition']='NOT_REQUESTED';return record
    require(status=='succeeded' and result['status']=='succeeded' and not runtime.get('termination_reason') and
        not runtime.get('resume_uncertain') and not runtime.get('resume_failed') and not cp['inflight'] and not cp['frontier'],
        'CONTINUATION_PHASE_NOT_ACCOUNTED')
    entries={e['url']:e for e in expected}
    require(len(entries)==len(expected) and set(cp['terminal'])==set(entries),'CONTINUATION_TERMINAL_SCOPE_CHANGED')
    matching=runtime.get('identity_post_processing') or {}
    require(matching.get('status')=='succeeded' and matching.get('existing_ids_preserved') is True,
        'CONTINUATION_MATCHING_NOT_DURABLE')
    for url,item in cp['terminal'].items():
        state=item['state'];r=item['receipt'];entry=entries[url]
        require(state in ('COMMITTED','QUARANTINED') and r['state']==state and r['url']==url and
            r['external_id']==entry['source_external_id'] and r['origin']=={'source':code,'job_id':jid,'manifest_sha256':runtime['input_sha256']},
            'CONTINUATION_TERMINAL_RECEIPT_CHANGED')
        cap=r['original_capture_metadata']
        require(cap['requested_url']==cap['final_url']==url and cap['http_status']==200 and not cap.get('redirects'),
            'CONTINUATION_CAPTURE_IDENTITY_CHANGED')
        verify(folder,cap)
        fp=r['capture_fingerprint'];require(fp not in record['receipts'],'CONTINUATION_DUPLICATE_RECEIPT')
        record['receipts'][fp]={'state':state,'source_product_id':entry['source_product_id'],
            'external_id':r['external_id'],'url':url,'observed_at':cap['observed_at'],
            'response_sha256':cap['response_sha256'],'evidence_sha256':cap['evidence_sha256']}
        if state=='COMMITTED':
            mr=r.get('matching_receipt') or {}
            require(r.get('sql_verified') is True and mr.get('status')=='succeeded' and mr.get('job_id')==jid and
                mr.get('run_id')==run and mr.get('external_id')==r['external_id'] and
                mr.get('policy_version')==matching.get('policy_version'),'CONTINUATION_MATCHING_NOT_DURABLE')
            record['committed']+=1
        else:
            iso=r.get('isolation') or {};p=iso.get('rejection_proof') or {}
            require(iso.get('category')=='matching' and iso.get('before_ingest') is True and
                p.get('rollback_verified') is True and p.get('sql_absent') is True and p.get('source_product_id')==entry['source_product_id'],
                'CONTINUATION_QUARANTINE_NOT_PROVEN')
            record['quarantined']+=1;record['quarantine_ids'].append(entry['source_product_id'])
    require(result['sql_committed']==runtime['sql_committed']==record['committed'] and
        result['processed']==runtime['processed']==len(expected) and matching.get('total')==record['committed'],
        'CONTINUATION_RESULT_COUNTS_CHANGED')
    record['quarantine_ids'].sort()
    return record


def check_sql_receipts(receipts,rows):
    """Rows join each capture to its exact observation; duplicates/extra SQL STOP."""
    committed={fp:r for fp,r in receipts.items() if r['state']=='COMMITTED'}
    require(len(rows)==len(committed) and {r[0] for r in rows}==set(committed),
        'CONTINUATION_SQL_RECEIPT_MISMATCH')
    for fp,pid,ext,url,final,at,response,evidence,observation_offer,observation_same_time in rows:
        r=committed[fp]
        require((pid,ext,url,final,response,evidence)==(r['source_product_id'],r['external_id'],r['url'],r['url'],r['response_sha256'],r['evidence_sha256']) and
            datetime_iso(at)==datetime_iso(r['observed_at']) and observation_offer==r['offer_id'] and observation_same_time,
            'CONTINUATION_SQL_RECEIPT_IDENTITY_CHANGED')


def datetime_iso(value):
    from datetime import datetime,timezone
    return (datetime.fromisoformat(value) if isinstance(value,str) else value).astimezone(timezone.utc).isoformat()


def receipt_proof(config,previous_path,previous_sha):
    path=Path(previous_path).resolve()
    require(file_sha(path)==previous_sha,'CONTINUATION_PREVIOUS_APPROVAL_CHANGED')
    old=json.loads(path.read_text(encoding='utf-8'))
    from .commercial_refresh import validate
    validate(old)
    require(old.get('commercial_only') is True,'CONTINUATION_WRONG_PREVIOUS_OPERATION')
    changes=compatibility(old)
    targets=[e for r in old['suppliers'].values() for e in r['targets']]
    jobs=[];receipts={};seen=set()
    with guarded_connect(config,autocommit=True,options='-c default_transaction_read_only=on -c statement_timeout=120000') as c:
        c.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        Session(config,old,previous_sha,ROOT,approval_path=path).check_mappings(c)
        require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running')").fetchall(),'CONTINUATION_ACTIVE_JOB')
        rows=c.execute('''SELECT j.id,s.code,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata,j.error_message
            FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
            WHERE j.settings_snapshot->'automation'->>'approval_sha256'=%s ORDER BY j.id''',(previous_sha,)).fetchall()
        for jid,code,status,run,snapshot,metadata,error in rows:
            require(code in old['suppliers'] and code not in seen,'CONTINUATION_DUPLICATE_OR_UNKNOWN_SOURCE')
            seen.add(code);rule=old['suppliers'][code]
            expected=[{'source_product_id':e['source_product_id'],'source_external_id':e['external_id'],'url':e['source_url']} for e in rule['targets']]
            require(expected and not snapshot.get('source_batch') and snapshot.get('source_input',{}).get('entries')==expected and
                snapshot.get('automation',{}).get('phase')=='REFRESH','CONTINUATION_JOB_SCOPE_CHANGED')
            rt=(metadata or {}).get('source_runtime') or {}
            require(digest(snapshot['source_input'])==rt.get('input_sha256') and snapshot['rate_settings']==rule['policy'],
                'CONTINUATION_JOB_INPUT_OR_POLICY_CHANGED')
            folder=ROOT/'reports/source-runner-evidence'/code/('job-'+str(jid))
            require(Path(rt.get('checkpoint','')).resolve()==(folder/'checkpoint.json').resolve(),'CONTINUATION_CHECKPOINT_PATH_CHANGED')
            checked=check_receipt_files(folder,code,jid,run,status,rt,expected,old['engine'])
            require(not set(receipts)&set(checked['receipts']),'CONTINUATION_DUPLICATE_RECEIPT')
            receipts.update(checked.pop('receipts'))
            jobs.append({'job_id':jid,'source':code,'status':status,'run_id':run,**checked})
        require(seen=={code for code,r in old['suppliers'].items() if r['targets']},'CONTINUATION_MISSING_SOURCE_JOB')
        saved=c.execute('''SELECT cap.capture_fingerprint,cap.source_product_id,sp.external_id,cap.requested_url,cap.final_url,
            cap.observed_at,cap.response_sha256,cap.evidence_sha256,obs.offer_id,obs.observed_at=cap.observed_at
            FROM supplier_http_captures cap JOIN source_products sp ON sp.id=cap.source_product_id
            LEFT JOIN offer_commercial_observations obs ON obs.capture_id=cap.id AND obs.source_product_id=cap.source_product_id
            WHERE (cap.source_product_id=ANY(%s) AND cap.observed_at>=%s::timestamptz)
                OR cap.capture_fingerprint=ANY(%s) ORDER BY cap.id''',
            ([e['source_product_id'] for e in targets],old['not_before'],list(receipts))).fetchall()
        target_by_id={e['source_product_id']:e for e in targets}
        for r in receipts.values():r['offer_id']=target_by_id[r['source_product_id']]['offer_id']
        check_sql_receipts(receipts,saved)
        maps=c.execute('SELECT id,supplier_id,external_id,catalog_product_id FROM source_products WHERE catalog_product_id IS NOT NULL ORDER BY id').fetchall()
        protected=c.execute('SELECT id,md5(row_to_json(j)::text) FROM crawl_jobs j ORDER BY id').fetchall()
        old_ids={r[0] for r in old['protected_jobs']}
        require([r for r in normal(protected) if r[0] in old_ids]==old['protected_jobs'],
            'CONTINUATION_ORIGINAL_PROTECTED_JOBS_CHANGED')
        require(len(maps)==len({r[0] for r in old['mappings1282']}),'CONTINUATION_PHYSICAL_MAPPING_COUNT_CHANGED')
        matches=c.execute('SELECT id,md5(row_to_json(pm)::text) FROM product_matches pm WHERE is_current AND source_product_id=ANY(%s) ORDER BY id',
            ([r['source_product_id'] for r in receipts.values() if r['state']=='COMMITTED'],)).fetchall()
        require(len(matches)==sum(j['committed'] for j in jobs),'CONTINUATION_CURRENT_MATCHING_MISSING')
        c.execute('ROLLBACK')
    return {'kind':RECEIPT_KIND,'previous_path':str(path),'previous_approval_sha256':previous_sha,
        'previous_head':old['head'],'previous_engine':old['engine'],'previous_scope_sha256':old['scope_sha256'],
        'target':old['target'],'not_before':old['not_before'],'execution_until':old['execution_until'],
        'compatible_code_sha256':changes,'jobs':jobs,'scope_count':1270,
        'committed':sum(j['committed'] for j in jobs),'quarantined':sum(j['quarantined'] for j in jobs),
        'not_requested':sum(len(old['suppliers'][j['source']]['targets']) for j in jobs if j['disposition']=='NOT_REQUESTED'),
        'sql_receipts_sha256':digest(normal(saved)),'all_mappings_sha256':digest(normal(maps)),
        'current_matching_sha256':digest(normal(matches)),'protected_jobs':normal(protected),'original1282_exact':True}


def prepare_receipts(config,previous_path,previous_sha):
    """SELECT-only successor draft, same original scope/window. Never self-approve."""
    from .commercial_refresh import ROOT as repo,validate,scope_rows
    from .source_runtime import engine_fingerprint
    from copy import deepcopy
    old=json.loads(Path(previous_path).read_text(encoding='utf-8'))
    p=receipt_proof(config,previous_path,previous_sha)
    value=deepcopy(old)
    value['head']=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    value['engine']=engine_fingerprint();value['continuation']=p
    value['operation_id']=old.get('operation_id',previous_sha);value['protected_jobs']=p['protected_jobs']
    value.pop('operator_startup_recovery',None)
    value['requires_operator_confirmation']=True
    # Old sources' settings remain frozen; only unfinished sources may activate.
    validate(value)
    scope_rows(config,[e for r in value['suppliers'].values() for e in r['targets']],accounted_quarantine=quarantine_ids(value))
    return value
