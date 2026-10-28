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
    require(p==proof(config,p['previous_path'],p['previous_approval_sha256']), 'CONTINUATION_DURABLE_PROOF_CHANGED')
