"""One approved confirmed REFRESH on the existing queue, never LIVE11 replay.

Preparation is SELECT-only. Execution is admitted by automation_execution's
exact HEAD/engine/SHA/window guard and uses its session/worker/STOP machinery.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
import csv,hashlib,json,os,subprocess
from pathlib import Path
from .supplier_automation import digest,settings_for
from .automation_execution import require
from .source_failure import failure_scope

PURPOSE='confirmed-commercial-refresh/v1'
ROOT=Path(__file__).resolve().parents[2]


def is_refresh(value):
    from .execution_canary import is_canary
    return value.get('purpose')==PURPOSE or is_canary(value)


def matrix_pins(value,matrix):
    """Exact file pin; otherwise row drift is ruled by the continuation.

    REUSE_TERMINAL rows stay byte-pinned. NOT_REQUESTED rows are free (the
    successor re-signs and exactly pins the whole file). Rows of sources the
    continuation never covered — and approvals without a continuation — may
    have drifted through later signed re-issues; those rows must remain
    self-consistent signed profiles."""
    if hashlib.sha256(Path(matrix).read_bytes()).hexdigest()==value['matrix_sha256']:return
    p=value.get('continuation') or {}
    jobs=p.get('jobs') if p.get('kind')=='commercial-receipt-continuation/v1' else None
    fresh={j['source'] for j in (jobs or []) if j.get('disposition')=='NOT_REQUESTED'}
    frozen={j['source'] for j in (jobs or []) if j.get('disposition')=='REUSE_TERMINAL'}
    rows={r['SOURCE']:r for r in csv.DictReader(Path(matrix).open(encoding='utf-8-sig',newline=''))}
    from .host_fetch import policy_digest
    from .rate_control import DomainRatePolicy
    for code,rule in value['suppliers'].items():
        row=rows.get(code);digest_value=value['host_policies'][rule['host']][code]
        if row is None or row.get('APPROVAL_DIGEST_SHA256')==digest_value:continue
        if code in fresh:continue
        if code in frozen:
            require(False,'COMMERCIAL_REFRESH_MATRIX_CHANGED')
        require(row.get('APPROVAL_DIGEST_SHA256')==policy_digest(code,rule['host'],
            DomainRatePolicy.from_mapping(json.loads(row['RATE_POLICY_JSON'])),row.get('APPROVAL_REFERENCE')),
            'COMMERCIAL_REFRESH_MATRIX_CHANGED')


def validate(value):
    from .execution_canary import is_canary,validate as validate_canary
    if is_canary(value):
        validate_canary(value)
        require(hashlib.sha256((ROOT/'config/live11_approval_matrix.csv').read_bytes()).hexdigest()==value['matrix_sha256'],'CANARY_MATRIX_CHANGED')
        return
    rules=value['suppliers'];targets=[e for r in rules.values() for e in r['targets']]
    require(len(targets)==1270 and len({e['source_product_id'] for e in targets})==1270,'COMMERCIAL_REFRESH_EXACT_1270_REQUIRED')
    require(value['scope_sha256']==digest(targets),'COMMERCIAL_REFRESH_SCOPE_CHANGED')
    require(value.get('commercial_only') is True and value.get('discovery') is False and value.get('explicit_safe_resume') is True,'COMMERCIAL_REFRESH_PURPOSE_CHANGED')
    for code,r in rules.items():
        entries=r['targets']
        require(r['limits']=={'REFRESH':len(entries),'DISCOVERY':0},'COMMERCIAL_REFRESH_LIMIT_CHANGED')
        require(entries==sorted(entries,key=lambda e:e['source_product_id']),'COMMERCIAL_REFRESH_ORDER_CHANGED')
        require(all(e['supplier_code']==code and e['supplier_id']==r['supplier_id'] and
            e['catalog_product_id'] is not None and e['confirmed_sterbrust_id'] for e in entries),'COMMERCIAL_REFRESH_IDENTITY_CHANGED')
        require(code!='beka_mak_tr' or not entries,'COMMERCIAL_REFRESH_REFERENCE_ONLY')
        require(r['settings_after']['enabled']==bool(entries) or not entries,'COMMERCIAL_REFRESH_ENABLE_APPROVAL_REQUIRED')
    matrix=ROOT/'config/live11_approval_matrix.csv'
    matrix_pins(value,matrix)
    if value.get('continuation'):
        p=value['continuation']
        from .commercial_continuation import RECEIPT_KIND,validate_window
        if p.get('kind')==RECEIPT_KIND:
            pending=sum(j.get('recover_pending',0) for j in p['jobs'] if j.get('disposition')=='RECOVER_RESUME')
            require(p.get('committed',0)+p.get('quarantined',0)+p.get('not_requested',0)+pending==1270 and
                all(j['disposition'] in ('REUSE_TERMINAL','NOT_REQUESTED','RECOVER_RESUME') for j in p['jobs']),
                'CONTINUATION_RECEIPT_ACCOUNTING_CHANGED')
            validate_window(value,p['not_before'],p['execution_until'])
        else:
            require(p.get('kind')=='commercial-not-requested-continuation/v1' and
                p.get('committed')==p.get('product_dispatches')==p.get('product_inflight')==0,
                'CONTINUATION_SCOPE_OR_PROOF_CHANGED')
        require(p.get('scope_count')==1270 and
            p.get('previous_scope_sha256')==value['scope_sha256'] and p.get('target')==value['target'] and
            (p.get('kind')==RECEIPT_KIND or (p.get('not_before')==value['not_before'] and p.get('execution_until')==value['execution_until'])),
            'CONTINUATION_SCOPE_OR_PROOF_CHANGED')


def scope_rows(config,targets,*,accounted_quarantine=(),canary=False):
    from universal_supplier.preview_store import load
    from .runtime_config import guarded_connect
    state=load(config,connection_factory=guarded_connect)
    ids={e['source_product_id'] for e in targets}
    actual=[r for r in state['records'] if r['source_product_id'] in ids]
    require(len(actual)==len(targets),'COMMERCIAL_REFRESH_SCOPE_ROWS_CHANGED')
    keys=('supplier_id','supplier_code','source_product_id','external_id','source_url','offer_id','confirmed_sterbrust_id','catalog_product_id')
    require(sorted(({k:r[k] for k in keys} for r in actual),key=lambda r:r['source_product_id'])==
        sorted(({k:r[k] for k in keys} for r in targets),key=lambda r:r['source_product_id']), 'COMMERCIAL_REFRESH_TARGETS_CHANGED')
    require(all((r['identity_confirmed'] or canary and r['external_id_is_stable'] and r['supplier_code'] in ('partner_st','optimum')) and (not r['blocking_quarantine'] or r['source_product_id'] in accounted_quarantine)
        and r['active'] for r in actual),
        'COMMERCIAL_REFRESH_IDENTITY_OR_QUARANTINE_CHANGED')
    return state,actual


def prepare(config,path,not_before,execution_until,*,canary=False):
    from .source_canary import TARGET,validate_connection
    from .source_runtime import engine_fingerprint
    from .runtime_config import guarded_connect
    from .source_recovery import normal
    from .host_fetch import compile_policy,policy_digest
    from .rate_control import DomainRatePolicy
    from .source_jobs import RUNTIME_SOURCES
    targets=json.loads(Path(path).read_text(encoding='utf-8'))['targets']
    state,actual=scope_rows(config,targets,canary=canary)
    matrix_path=ROOT/'config/live11_approval_matrix.csv'
    matrix={r['SOURCE']:r for r in csv.DictReader(matrix_path.open(encoding='utf-8-sig',newline=''))}
    rules={};hosts={}
    for s in state['suppliers']:
        code=s['code'];m=matrix[code];rate=json.loads(m['RATE_POLICY_JSON'])
        approval={'host':m['APPROVAL_HOST'],'reference':m['APPROVAL_REFERENCE']}
        host=approval['host'];hosts.setdefault(host,{})[code]=policy_digest(code,host,DomainRatePolicy.from_mapping(rate),approval['reference'])
        require(hosts[host][code]==m['APPROVAL_DIGEST_SHA256'],'COMMERCIAL_REFRESH_MATRIX_POLICY_INVALID')
        policy=compile_policy(code,s['base_url'],rate,approval=approval,approved_digests=hosts).snapshot()
        entries=sorted([{k:r[k] for k in ('supplier_id','supplier_code','source_product_id','external_id','source_url','offer_id','confirmed_sterbrust_id','catalog_product_id')}
            for r in actual if r['supplier_code']==code],key=lambda r:r['source_product_id'])
        before={k:deepcopy(s[k]) for k in ('enabled','rate_settings','crawl_settings')}
        after=deepcopy(before)
        if entries:
            after.update(enabled=True,rate_settings=policy)
            after['crawl_settings']={**after['crawl_settings'],'host_policy_approval':approval,
                'max_run_duration_seconds':int(m['REFRESH_JOB_SECONDS']),'max_discovery_pages':1,'safety_gate_size':5}
        rules[code]={'supplier_id':s['id'],'host':host,'enabled_at_approval':bool(entries),
            'settings_before':before,'settings_after':after,'settings_sha256':digest(settings_for(after)),
            'rate_sha256':digest(policy),'limits':{'REFRESH':len(entries),'DISCOVERY':0},
            'targets':entries,'max_queue_attempts':3,'min_request_interval_seconds':policy['min_request_interval_seconds'],
            'max_run_duration_seconds':int(m['REFRESH_JOB_SECONDS']),'max_discovery_pages':1,'policy':policy}
    require(set(rules)==set(RUNTIME_SOURCES),'COMMERCIAL_REFRESH_EXACT_11_REQUIRED')
    baseline=ROOT.parent.parent/'reports/RC_LIVE11_AUTONOMOUS_24H_20261026/AUTONOMOUS_APPROVAL.json'
    original=json.loads(baseline.read_text(encoding='utf-8'))['continuation']['mappings1282']
    with guarded_connect(config,autocommit=True,options='-c default_transaction_read_only=on') as c:
        validate_connection(c)
        require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running')").fetchall(),'COMMERCIAL_REFRESH_ACTIVE_JOB')
        protected=c.execute("SELECT id,md5(row_to_json(j)::text) FROM crawl_jobs j ORDER BY id").fetchall()
    value={'format':'supplier-schedule-grant/v1','purpose':PURPOSE,'commercial_only':True,'discovery':False,'explicit_safe_resume':True,
        'head':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),'engine':engine_fingerprint(),
        'not_before':not_before,'execution_until':execution_until,'max_parallel':2,'suppliers':rules,
        'scope_sha256':digest([e for r in rules.values() for e in r['targets']]),
        'host_policies':hosts,'mappings1282':original,'target':dict(TARGET),'protected_jobs':normal(protected),
        'matrix_sha256':hashlib.sha256(matrix_path.read_bytes()).hexdigest(),
        'protected_live11_batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d',
        'production':False,'esol':False,'requires_operator_confirmation':True}
    if canary:
        from .execution_canary import PURPOSE as CANARY
        value.update(purpose=CANARY,matching_apply=False,explicit_safe_resume=False)
        for code,r in rules.items():
            r['max_queue_attempts']=1
            if r['targets']:
                r['policy']['max_attempts']=1
                r['settings_after']['rate_settings']['max_attempts']=1
                r['rate_sha256']=digest(r['policy'])
                r['settings_sha256']=digest(settings_for(r['settings_after']))
                from .rate_control import DomainRatePolicy
                hosts[r['host']][code]=policy_digest(code,r['host'],DomainRatePolicy.from_mapping(r['policy']),r['settings_after']['crawl_settings']['host_policy_approval']['reference'])
    validate(value)
    return value


def authorize_card(card,capture):
    from .automation_execution import require_active
    session=require_active();require(session.value.get('purpose')==PURPOSE,'COMMERCIAL_REFRESH_APPROVAL_REQUIRED')
    from .commercial_continuation import reused_sources
    require(card.supplier_code not in reused_sources(session.value),'CONTINUATION_ACCOUNTED_SOURCE_CANNOT_RUN')
    targets=session.value['suppliers'][card.supplier_code]['targets']
    found=[e for e in targets if e['external_id']==card.external_id and e['source_url']==capture.requested_url]
    require(len(found)==1 and card.canonical_url==capture.final_url==capture.requested_url,
        'COMMERCIAL_REFRESH_CARD_OUTSIDE_APPROVED_SCOPE')
    return found[0]


def validate_snapshot(snapshot,session):
    from .commercial_continuation import reused_sources
    require(snapshot['supplier_code'] not in reused_sources(session.value),'CONTINUATION_ACCOUNTED_SOURCE_CANNOT_RUN')
    marker=snapshot['automation'];rule=session.value['suppliers'][snapshot['supplier_code']]
    from .execution_canary import is_canary
    if is_canary(session.value):
        require(not snapshot['crawl_settings'].get('resume_owner_job_id') and snapshot['crawl_settings'].get('collection_mode')=='REFRESH','CANARY_NO_RESUME')
    require(marker.get('purpose')==session.value['purpose'] and marker['phase']=='REFRESH','COMMERCIAL_REFRESH_JOB_PURPOSE_CHANGED')
    expected=[{'source_product_id':r['source_product_id'],'source_external_id':r['external_id'],'url':r['source_url']} for r in rule['targets']]
    require(snapshot['source_input']['entries']==expected and snapshot['source_input']['mode']=='REFRESH',
        'COMMERCIAL_REFRESH_JOB_SCOPE_CHANGED')
    engine_ok=snapshot['source_input']['engine_sha256']==session.value['engine']
    if not engine_ok and (snapshot['crawl_settings'] or {}).get('resume_owner_job_id'):
        # A mixed-recovery RESUME child carries its parent's frozen input; only
        # the operator-signed block may pin that exact parent engine.
        block=rule.get('mixed_recovery') or {}
        engine_ok=(block.get('format')=='commercial-mixed-recovery/v1'
            and block.get('parent_job_id')==(snapshot['crawl_settings'] or {}).get('resume_owner_job_id')
            and block.get('parent_engine_sha256')==snapshot['source_input']['engine_sha256'])
    require(engine_ok,'COMMERCIAL_REFRESH_JOB_ENGINE_CHANGED')
    require(not snapshot.get('source_batch'),'COMMERCIAL_REFRESH_CANNOT_RECOVER_LIVE11')


def preview(config,supplier_id,supplier):
    from .automation_execution import require_active
    from .source_canary import build_preview
    session=require_active();rule=session.value['suppliers'][supplier[0]]
    from .commercial_continuation import reused_sources
    require(supplier[0] not in reused_sources(session.value),'CONTINUATION_ACCOUNTED_SOURCE_CANNOT_RUN')
    rows=[(e['source_product_id'],e['external_id'],e['source_url'],len(rule['targets'])) for e in rule['targets']]
    require(rows and supplier_id==rule['supplier_id'],'COMMERCIAL_REFRESH_EMPTY_OR_WRONG_SUPPLIER')
    return build_preview(supplier_id,supplier,rows,config,len(rows),'REFRESH')


@contextmanager
def settings(session,connection):
    """CAS activation/restoration only for the explicitly approved eight sources."""
    from psycopg.types.json import Jsonb
    from .source_recovery import normal
    from .source_canary import validate_connection
    validate_connection(connection)
    from .commercial_continuation import validate_live,reused_sources,quarantine_ids
    validate_live(session.config,session.value)
    from .execution_canary import is_canary
    scope_rows(session.config,[e for r in session.value['suppliers'].values() for e in r['targets']],**({'canary':True} if is_canary(session.value) else {}),
        **({'accounted_quarantine':quarantine_ids(session.value)} if session.value.get('continuation',{}).get('kind')=='commercial-receipt-continuation/v1' else {}))
    def check_protected():
        old=session.value['protected_jobs']
        rows=connection.execute('SELECT id,md5(row_to_json(j)::text) FROM crawl_jobs j WHERE id=ANY(%s) ORDER BY id',([r[0] for r in old],)).fetchall()
        require(normal(rows)==old,'COMMERCIAL_REFRESH_PROTECTED_JOBS_CHANGED')
    check_protected()
    changed=[];activated=False
    try:
        with connection.transaction():
            for code,r in session.value['suppliers'].items():
                if not r['targets'] or code in reused_sources(session.value):continue
                before=r['settings_before'];after=r['settings_after']
                row=connection.execute('SELECT enabled,rate_settings,crawl_settings FROM suppliers WHERE id=%s FOR UPDATE',(r['supplier_id'],)).fetchone()
                require(list(row)==[before[k] for k in ('enabled','rate_settings','crawl_settings')],'COMMERCIAL_REFRESH_SOURCE_SETTINGS_CHANGED')
                connection.execute('UPDATE suppliers SET enabled=%s,rate_settings=%s,crawl_settings=%s WHERE id=%s',
                    (after['enabled'],Jsonb(after['rate_settings']),Jsonb(after['crawl_settings']),r['supplier_id']))
                changed.append(r)
        activated=True
        yield
    finally:
        # Attempt all restorations; a concurrent operator change is reported,
        # never overwritten. All successful CAS updates are read back by SELECT.
        failures=[]
        for r in changed if activated else []:
            before=r['settings_before'];after=r['settings_after']
            try:
                with connection.transaction():
                    row=connection.execute('SELECT enabled,rate_settings,crawl_settings FROM suppliers WHERE id=%s FOR UPDATE',(r['supplier_id'],)).fetchone()
                    require(list(row)==[after[k] for k in ('enabled','rate_settings','crawl_settings')],'COMMERCIAL_REFRESH_RESTORE_CONCURRENT_CHANGE')
                    connection.execute('UPDATE suppliers SET enabled=%s,rate_settings=%s,crawl_settings=%s WHERE id=%s',
                        (before['enabled'],Jsonb(before['rate_settings']),Jsonb(before['crawl_settings']),r['supplier_id']))
                    row=connection.execute('SELECT enabled,rate_settings,crawl_settings FROM suppliers WHERE id=%s',(r['supplier_id'],)).fetchone()
                    require(list(row)==[before[k] for k in ('enabled','rate_settings','crawl_settings')],'COMMERCIAL_REFRESH_RESTORE_FAILED')
            except Exception as error:failures.append(type(error).__name__+':'+str(error))
        if failures:raise RuntimeError('COMMERCIAL_REFRESH_SETTINGS_RESTORE_UNVERIFIED:'+','.join(failures))
        session.check_mappings(connection);check_protected()
        from .source_hourly import atomic_text
        atomic_text(session.folder/'SETTINGS_RESTORED.json',json.dumps({'restored':True,'SELECT_verified':True,'protected_jobs_exact':True,'original1282_exact':True}))


def tick(scheduler,now):
    """A single occurrence, native safe RESUME only; no recurring calendar slots."""
    session=scheduler.automation;session.check();scheduler.blocked={};scheduler.refresh_finished=False
    enqueued=[];done=0
    from .commercial_continuation import reused_sources
    for code,r in session.value['suppliers'].items():
        if not r['targets'] or code in reused_sources(session.value):done+=1;continue
        paused=session.source_reason(code) if hasattr(session,'source_reason') else None
        if paused:scheduler.blocked[code]=paused;continue
        with scheduler._connect() as c,c.cursor() as q:
            q.execute('''SELECT j.id,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata,j.error_message
                FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id WHERE j.supplier_id=%s
                AND j.settings_snapshot->'automation'->>'approval_sha256'=%s ORDER BY j.id DESC LIMIT 1''',(r['supplier_id'],session.sha))
            previous=q.fetchone()
            if previous is None:
                # A signed mixed-recovery parent job is the explicit previous
                # occurrence for its source inside this window.
                block=r.get('mixed_recovery') or {}
                if block.get('format')=='commercial-mixed-recovery/v1' and type(block.get('parent_job_id')) is int:
                    q.execute('''SELECT j.id,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata,j.error_message
                        FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
                        WHERE j.id=%s AND j.supplier_id=%s''',(block['parent_job_id'],r['supplier_id']))
                    previous=q.fetchone()
        resume=None
        if previous:
            jid,status,run,snapshot,metadata,error=previous
            if status=='succeeded':done+=1;continue
            if status in ('queued','running'):continue
            from .execution_canary import is_canary
            if is_canary(session.value):
                scheduler.blocked[code]='CANARY_TERMINAL_NO_AUTOMATIC_RETRY';continue
            explicit=jid in getattr(session,'prior_jobs',set()) and getattr(session,'resume_safe',False)
            if jid in getattr(session,'prior_jobs',set()) and not explicit:
                scheduler.blocked[code]='COMMERCIAL_REFRESH_EXPLICIT_RESUME_REQUIRED';continue
            rt=(metadata or {}).get('source_runtime') or {}
            # STOP and unknown outcomes never become an implicit retry.
            reason=rt.get('termination_reason') or error or 'UNPROVEN_PARENT'
            if failure_scope(reason,rt)=='supplier':
                if hasattr(session,'pause_source'):session.pause_source(code,reason)
                scheduler.blocked[code]=reason;continue
            explicit_stop_resume=explicit and str(reason).startswith('OPERATOR') and rt.get('resume_available') and not rt.get('resume_uncertain') and not rt.get('resume_failed')
            from . import automation_execution
            recovery_reauth=automation_execution.reauthorized_uncertain(code,rt.get('owner_job_id'),rt.get('input_sha256')) \
                if rt.get('owner_job_id') and rt.get('input_sha256') else set()
            # Operator-signed mixed recovery: every UNCERTAIN url of this exact
            # parent is reauthorized, so the resume is an explicit reviewed path.
            whitelisted=bool(recovery_reauth) and set(rt.get('resume_uncertain') or [])<=recovery_reauth and not rt.get('resume_failed')
            if run is not None and failure_scope(reason,rt)=='system' and not explicit_stop_resume and not whitelisted:
                session.fail('AUTOMATION_CRITICAL_JOB:'+str(reason)[:180]);session.check()
            if run is None and explicit and status=='cancelled' and error in ('AUTOMATION_STOP','AUTOMATION_WINDOW_STOP'):
                pass  # Never started: original frozen scope, same bounded occurrence.
            elif run is None or (not whitelisted and (not rt.get('resume_available') or rt.get('resume_uncertain') or rt.get('resume_failed'))) or str(reason).startswith(('SOURCE_BLOCKED','SOURCE_CARD_ERROR_THRESHOLD','SOURCE_IDENTITY_MASS_FAILURE','SOURCE_HTTP_CIRCUIT','SOURCE_NETWORK')) or (str(reason).startswith('OPERATOR') and not explicit):
                scheduler.blocked[code]=reason;continue
            else:scheduler.queue.source_resume_status(jid);resume=jid
        args={'trigger_type':'schedule','crawl_type':'full','scheduled_for':datetime.fromisoformat(session.value['not_before']),
            'automation_sha256':session.sha}
        try:
            if resume is not None:result=scheduler.queue.enqueue(r['supplier_id'],source_mode='RESUME',resume_job_id=resume,**args)
            else:
                previewed=scheduler.queue.preview_source_canary(r['supplier_id'],len(r['targets']),'REFRESH')
                result=scheduler.queue.enqueue(r['supplier_id'],source_mode='REFRESH',source_limit=len(r['targets']),source_preview_sha256=previewed['confirmation_sha256'],**args)
        except PermissionError as error:
            if str(error)!='AUTOMATION_QUEUE_ATTEMPTS_EXHAUSTED':raise
            scheduler.blocked[code]=str(error);continue
        if result.enqueued:enqueued.append(result.job_id)
    scheduler.refresh_finished=done+len(scheduler.blocked)==len(session.value['suppliers'])
    return enqueued
