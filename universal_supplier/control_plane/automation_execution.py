"""Bounded approved sessions for the EXISTING scheduler/queue/worker, never LIVE11 recovery."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess,threading,time
from pathlib import Path
from .supplier_automation import digest,settings_for,rate_for
from .source_jobs import FROZEN_LIVE11_SOURCES

ACTIVE=None
ROOT=Path(__file__).resolve().parents[2]
ENV='STERBRUST_AUTOMATION'
MIXED_RECOVERY_KIND='commercial-mixed-recovery/v1'


def require(condition,reason):
    if not condition:raise PermissionError(reason)


def load(path,sha,confirm=None,*,for_execution=False):
    require(hashlib.sha256(Path(path).read_bytes()).hexdigest()==sha,'AUTOMATION_APPROVAL_SHA_CHANGED')
    value=json.loads(Path(path).read_text(encoding='utf-8'))
    require(value.get('format')=='supplier-schedule-grant/v1','AUTOMATION_APPROVAL_FORMAT')
    from .source_canary import TARGET
    require(value.get('target')==TARGET,'AUTOMATION_TARGET_CHANGED')
    head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    require(head==value['head'],'AUTOMATION_HEAD_CHANGED')
    from .source_runtime import engine_fingerprint
    require(engine_fingerprint()==value['engine'],'AUTOMATION_ENGINE_CHANGED')
    if value.get('transport_tls'):
        from universal_supplier.public_connection import public_tls_identity
        require(value['transport_tls']==public_tls_identity(),'AUTOMATION_TLS_POLICY_CHANGED')
    require(set(value['suppliers'])==set(FROZEN_LIVE11_SOURCES) and value['max_parallel']==2,
            'AUTOMATION_EXACT_11_AND_MAX2_REQUIRED')
    require(value.get('production') is False and value.get('esol') is False and
        value.get('protected_live11_batch_id')=='3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','AUTOMATION_PROTECTED_SCOPE_CHANGED')
    start=datetime.fromisoformat(value['not_before']);end=datetime.fromisoformat(value['execution_until'])
    require(start.tzinfo is not None and end.tzinfo is not None and 0<(end-start).total_seconds()<=86400,
            'AUTOMATION_WINDOW_INVALID')
    require(len(value['mappings1282'])==1282 and len({r[0] for r in value['mappings1282']})==1282,'AUTOMATION_MAPPING_BASELINE_REQUIRED')
    from .commercial_refresh import is_refresh,validate
    if is_refresh(value):validate(value)
    else:require(value.get('purpose') is None,'AUTOMATION_PURPOSE_UNKNOWN')
    for code,rule in value['suppliers'].items():
        require(isinstance(rule.get('settings_sha256'),str) and len(rule['settings_sha256'])==64,
                'AUTOMATION_SOURCE_SETTINGS_APPROVAL_REQUIRED')
        require(type(rule['supplier_id']) is int and rule['supplier_id']>0 and
            type(rule['min_request_interval_seconds']) in (int,float) and __import__('math').isfinite(rule['min_request_interval_seconds']) and rule['min_request_interval_seconds']>0,
            'AUTOMATION_SOURCE_POLICY_INVALID')
        require(type(rule['max_queue_attempts']) is int and 1<=rule['max_queue_attempts']<=3,
                'AUTOMATION_QUEUE_ATTEMPTS_INVALID')
        exception=rule.get('robots_policy_exception')
        if exception is not None:
            from .source_runtime import ROBOTS_EXCEPTION_KIND
            import re as _re
            require(exception.get('format')==ROBOTS_EXCEPTION_KIND and exception.get('source')==code
                    and exception.get('host')==rule.get('host')
                    and exception.get('requires_operator_confirmation') is True,
                    'AUTOMATION_ROBOTS_EXCEPTION_CONTRACT_INVALID')
            require(isinstance(exception.get('robots_sha256'),str)
                    and _re.fullmatch(r'[0-9a-f]{64}',exception['robots_sha256']) is not None,
                    'AUTOMATION_ROBOTS_EXCEPTION_ROBOTS_SHA_INVALID')
            require(type(exception.get('declared_minimum_seconds')) in (int,float)
                    and __import__('math').isfinite(exception['declared_minimum_seconds'])
                    and exception['declared_minimum_seconds']>0,
                    'AUTOMATION_ROBOTS_EXCEPTION_DECLARED_INVALID')
            require(type(exception.get('applied_min_request_interval_seconds')) in (int,float)
                    and __import__('math').isfinite(exception['applied_min_request_interval_seconds'])
                    and 0<exception['applied_min_request_interval_seconds']<exception['declared_minimum_seconds'],
                    'AUTOMATION_ROBOTS_EXCEPTION_APPLIED_INVALID')
            require(exception['applied_min_request_interval_seconds']==rule['min_request_interval_seconds'],
                    'AUTOMATION_ROBOTS_EXCEPTION_RATE_MISMATCH')
        recovery=rule.get('mixed_recovery')
        if recovery is not None:
            import re as _re
            require(recovery.get('format')==MIXED_RECOVERY_KIND and recovery.get('source')==code
                and recovery.get('host')==rule.get('host')
                and recovery.get('requires_operator_confirmation') is True,
                'AUTOMATION_MIXED_RECOVERY_CONTRACT_INVALID')
            require(type(recovery.get('parent_job_id')) is int and recovery['parent_job_id']>0,
                'AUTOMATION_MIXED_RECOVERY_PARENT_INVALID')
            require(isinstance(recovery.get('parent_manifest_sha256'),str)
                and _re.fullmatch(r'[0-9a-f]{64}',recovery['parent_manifest_sha256']) is not None,
                'AUTOMATION_MIXED_RECOVERY_MANIFEST_INVALID')
            require(isinstance(recovery.get('parent_approval_sha256'),str)
                and _re.fullmatch(r'[0-9a-f]{64}',recovery['parent_approval_sha256']) is not None,
                'AUTOMATION_MIXED_RECOVERY_APPROVAL_INVALID')
            require(isinstance(recovery.get('parent_engine_sha256'),str)
                and _re.fullmatch(r'[0-9a-f]{64}',recovery['parent_engine_sha256']) is not None,
                'AUTOMATION_MIXED_RECOVERY_ENGINE_INVALID')
            items=recovery.get('reauthorized_uncertain')
            require(isinstance(items,dict) and items and all(isinstance(url,str) and isinstance(item,dict)
                and item.get('transport_response_available') is False for url,item in items.items()),
                'AUTOMATION_MIXED_RECOVERY_URLS_INVALID')
        for phase in ('REFRESH','DISCOVERY'):
            cap=rule['limits'][phase]
            require(type(cap) is int and (0 if is_refresh(value) else 1)<=cap<=100000,'AUTOMATION_EXPLICIT_PRODUCT_CAP_REQUIRED')
    if for_execution:
        require(confirm==sha,'AUTOMATION_EXACT_OPERATOR_CONFIRMATION_REQUIRED')
        require(not subprocess.check_output(['git','status','--porcelain'],cwd=ROOT,text=True).strip(),
                'AUTOMATION_DIRTY_CHECKOUT')
        require(start<=datetime.now(timezone.utc)<end,'AUTOMATION_EXECUTION_WINDOW_EXPIRED')
    return value


def require_active(sha=None):
    require(ACTIVE is not None and (sha is None or ACTIVE.sha==sha),'AUTOMATION_EXECUTION_APPROVAL_REQUIRED')
    ACTIVE.check()
    return ACTIVE


def stop_reason():
    return ACTIVE.reason() if ACTIVE else None


def mixed_recovery(code,owner,sha):
    """Operator-signed admission for one mixed parent checkpoint: its named
    UNCERTAIN urls (proven no-response transport) may be re-requested and the
    parent resumed into this session. Inert outside the active approval."""
    session=ACTIVE
    if session is None:return None
    block=((session.value['suppliers'].get(code) or {}).get('mixed_recovery') or {})
    if block.get('format')!=MIXED_RECOVERY_KIND or block.get('source')!=code:return None
    if block.get('requires_operator_confirmation') is not True:return None
    if block.get('parent_job_id')!=owner or block.get('parent_manifest_sha256')!=sha:return None
    items=block.get('reauthorized_uncertain')
    if not isinstance(items,dict) or not items:return None
    return block if all(isinstance(item,dict) and item.get('transport_response_available') is False
        for item in items.values()) else None


def reauthorized_uncertain(code,owner,sha):
    return set(((mixed_recovery(code,owner,sha) or {}).get('reauthorized_uncertain') or {}))


def source_stop_reason(code):
    return ACTIVE.source_reason(code) if ACTIVE and hasattr(ACTIVE,'source_reason') else None


def validate_job(snapshot):
    marker=snapshot.get('automation')
    if not marker:return
    session=require_active(marker.get('approval_sha256'))
    from .commercial_refresh import is_refresh,validate_snapshot
    if is_refresh(session.value):validate_snapshot(snapshot,session)
    code=snapshot['supplier_code'];rule=session.value['suppliers'][code]
    require(rule.get('enabled_at_approval') is True,'AUTOMATION_SUPPLIER_ENABLE_REQUIRES_APPROVAL')
    require(digest(settings_for(snapshot))==rule['settings_sha256'],'AUTOMATION_SOURCE_SETTINGS_CHANGED')
    require(marker['phase'] in ('REFRESH','DISCOVERY') and marker['rate_sha256']==digest(snapshot['rate_settings'])==rule['rate_sha256'],
            'AUTOMATION_JOB_POLICY_CHANGED')
    source=snapshot['source_input'];phase=source['mode'];canary=source['canary']
    require(phase==marker['phase'] and canary['max_products']==rule['limits'][phase] and
        canary['max_discovery_pages']==rule['max_discovery_pages'] and
        canary['max_run_duration_seconds']==rule['max_run_duration_seconds'], 'AUTOMATION_JOB_SCOPE_CHANGED')
    require(source['supplier_code']==code and canary['supplier_id']==rule['supplier_id'],
            'AUTOMATION_JOB_IDENTITY_CHANGED')
    require(snapshot['domain']==rule['host'],'AUTOMATION_JOB_HOST_CHANGED')


def prepare(config,limits,not_before,execution_until):
    """Explicit draft only; no settings activation, enqueue or parser request."""
    import psycopg
    from .source_canary import validate_config,validate_connection
    from .source_runtime import engine_fingerprint
    from .source_recovery import normal
    validate_config(config)
    with psycopg.connect(**config.kwargs(),autocommit=True,options='-c default_transaction_read_only=on') as c:
        validate_connection(c)
        rows=c.execute('SELECT id,code,enabled,base_url,rate_settings,crawl_settings FROM suppliers WHERE code=ANY(%s) ORDER BY code',(list(FROZEN_LIVE11_SOURCES),)).fetchall()
        require({r[1] for r in rows}==set(FROZEN_LIVE11_SOURCES),'AUTOMATION_SOURCE_SET_CHANGED')
        from .source_batch import normalized_domain
        rules={};hosts={}
        for sid,code,enabled,url,rate,crawl in rows:
            current={'code':code,'rate_settings':rate,'crawl_settings':crawl}
            cfg=settings_for(current);effective=rate_for(cfg,rate)
            require(code in limits and set(limits[code])=={'REFRESH','DISCOVERY'},'AUTOMATION_EXPLICIT_CAPS_REQUIRED')
            rules[code]={'supplier_id':sid,'host':normalized_domain(url),'enabled_at_approval':enabled,
                'settings_sha256':digest(cfg),
                'limits':limits[code],'rate_sha256':digest(effective),'max_queue_attempts':3,
                'min_request_interval_seconds':effective['min_request_interval_seconds'],
                'max_discovery_pages':crawl.get('max_discovery_pages',100),
                'max_run_duration_seconds':crawl.get('max_run_duration_seconds',effective['max_run_duration_seconds'])}
            approval=(crawl or {}).get('host_policy_approval') or {}
            if approval.get('reference'):
                from .host_fetch import policy_digest
                from .rate_control import DomainRatePolicy
                hosts.setdefault(normalized_domain(url),{})[code]=policy_digest(code,normalized_domain(url),DomainRatePolicy.from_mapping(effective),approval['reference'])
        # The original 1282 baseline stays explicitly pinned; unrelated new links are allowed.
        baseline=ROOT.parent.parent/'reports/RC_LIVE11_AUTONOMOUS_24H_20261026/AUTONOMOUS_APPROVAL.json'
        original=json.loads(baseline.read_text(encoding='utf-8'))['continuation']['mappings1282']
        head=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
        return {'format':'supplier-schedule-grant/v1','head':head,'engine':engine_fingerprint(),
            'not_before':not_before,'execution_until':execution_until,'max_parallel':2,'suppliers':rules,
            'host_policies':hosts,'mappings1282':original,'target':dict(__import__(
                'universal_supplier.control_plane.source_canary',fromlist=['TARGET']).TARGET),
            'protected_live11_batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d',
            'requires_operator_confirmation':True,'production':False,'esol':False}


class Session:
    """Explicit restart inside the same window is allowed; PostgreSQL owns exclusivity."""
    def __init__(self,config,value,sha,folder,*,approval_path):
        self.config=config;self.value=value;self.sha=sha;self.folder=Path(folder)
        self.approval_path=Path(approval_path);self.last_engine_check=float('-inf')
        self.hard=datetime.fromisoformat(value['execution_until'])
        from datetime import timedelta
        self.end=self.hard-timedelta(minutes=5);self.started=datetime.now(timezone.utc)
        self.started_mono=time.monotonic();self.seconds=(self.end-self.started).total_seconds()
        self.failure=None;self.lock=None
        self.limiters={};self.limiter_lock=threading.RLock()
        self.paused_sources={}

    def pause_source(self,code,reason,diagnostics=None):
        # A shared host is not independent. The global session remains live.
        host=self.value['suppliers'][code]['host']
        with self.limiter_lock:
            for source,rule in self.value['suppliers'].items():
                if rule['host']==host:self.paused_sources.setdefault(source,{'reason':reason,'diagnostics':diagnostics})

    def source_reason(self,code):
        with self.limiter_lock:return self.paused_sources.get(code,{}).get('reason')

    def host_limiter(self,host,rate,clock):
        from dataclasses import replace
        from .rate_control import DomainRateLimiter
        floor=max(r['min_request_interval_seconds'] for r in self.value['suppliers'].values() if r['host']==host)
        effective=replace(rate,min_request_interval_seconds=max(floor,rate.min_request_interval_seconds))
        with self.limiter_lock:
            if host not in self.limiters:self.limiters[host]=DomainRateLimiter(effective,clock=clock)
            limiter=self.limiters[host]
            # Existing host lease ensures the previous source's job is finished.
            # Retain its next-start gate when another source takes this host.
            with limiter._lock:limiter.policy=effective
            return limiter

    def fail(self,reason):
        if not self.failure:self.failure=reason

    def reason(self):
        if self.failure:return self.failure
        if time.monotonic()-self.started_mono>=self.seconds or datetime.now(timezone.utc)>=self.end:
            self.fail('AUTOMATION_EXECUTION_WINDOW_EXPIRED')
        if (self.folder/'STOP_REQUESTED').exists():self.fail('OPERATOR_STOP')
        return self.failure

    def check(self):
        require(not self.reason(),self.reason())
        require(subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()==self.value['head'],
                'AUTOMATION_HEAD_CHANGED')
        if time.monotonic()-self.last_engine_check>=10:
            from .source_runtime import engine_fingerprint
            require(engine_fingerprint()==self.value['engine'],'AUTOMATION_ENGINE_CHANGED')
            if self.value.get('transport_tls'):
                from universal_supplier.public_connection import public_tls_identity
                require(self.value['transport_tls']==public_tls_identity(),'AUTOMATION_TLS_POLICY_CHANGED')
            require(hashlib.sha256(self.approval_path.read_bytes()).hexdigest()==self.sha,'AUTOMATION_APPROVAL_SHA_CHANGED')
            self.last_engine_check=time.monotonic()
        if shutil.disk_usage(self.folder).free<5*1024**3:raise PermissionError('AUTOMATION_DISK_LOW')
        from .source_canary import TARGET
        if shutil.disk_usage(Path(TARGET['pgdata'])).free<5*1024**3:raise PermissionError('AUTOMATION_DISK_LOW')
        if self.lock is not None:
            try:self.lock.execute('SELECT 1')
            except Exception:
                self.fail('AUTOMATION_POSTGRES_OR_LEASE_LOST');raise PermissionError(self.failure)

    def check_mappings(self,connection):
        from .source_recovery import normal
        rows=self.value['mappings1282']
        saved=connection.execute('''SELECT sp.id,s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id LEFT JOIN sterbrust_products sb
            ON sb.catalog_product_id=sp.catalog_product_id WHERE sp.id=ANY(%s) AND sp.catalog_product_id IS NOT NULL
            ORDER BY sp.id,sb.sterbrust_product_id''',(sorted({r[0] for r in rows}),)).fetchall()
        require(normal(saved)==rows,'AUTOMATION_ORIGINAL_MAPPINGS_CHANGED')

    @contextmanager
    def activate(self):
        global ACTIVE
        import psycopg
        from .source_canary import validate_config,validate_connection
        from .source_hourly import atomic_text
        validate_config(self.config)
        self.folder.mkdir(parents=True,exist_ok=True)
        require(ACTIVE is None,'AUTOMATION_DOUBLE_RUN')
        with psycopg.connect(**self.config.kwargs(),autocommit=True,connect_timeout=5,
            options='-c statement_timeout=60000 -c lock_timeout=15000') as c:
            validate_connection(c)
            require(c.execute("SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user").fetchone()==('rc_runtime',False,False),'AUTOMATION_RUNTIME_ROLE_REQUIRED')
            require(c.execute('SELECT pg_try_advisory_lock(%s)',(8611026,)).fetchone()[0],'AUTOMATION_DOUBLE_RUN')
            self.lock=c
            try:
                self.check();self.check_mappings(c)
                from .execution_canary import is_canary,require_unused_session
                if is_canary(self.value):require_unused_session(ROOT,self.sha,self.folder)
                require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running') AND coalesce(settings_snapshot->'automation'->>'approval_sha256','')<>%s",(self.sha,)).fetchall(),
                    'AUTOMATION_FOREIGN_ACTIVE_JOB')
                require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running') AND settings_snapshot ? 'source_batch'").fetchall(),
                    'AUTOMATION_LIVE11_ACTIVE_REQUIRES_SEPARATE_RECOVERY')
                if self.value.get('commercial_only'):
                    self.prior_jobs={r[0] for r in c.execute("SELECT id FROM crawl_jobs WHERE settings_snapshot->'automation'->>'approval_sha256'=%s",(self.sha,)).fetchall()}
                    from .execution_canary import is_canary,require_unused
                    if is_canary(self.value):require_unused(self.prior_jobs)
                phases=c.execute('''SELECT DISTINCT ON (supplier_id,settings_snapshot->'source_batch'->>'phase') status
                    FROM crawl_jobs WHERE settings_snapshot->'source_batch'->>'id'=%s
                    ORDER BY supplier_id,settings_snapshot->'source_batch'->>'phase',id DESC''',
                    (self.value['protected_live11_batch_id'],)).fetchall()
                from .commercial_refresh import is_refresh,settings
                if not is_refresh(self.value):
                    require(len(phases)==22 and all(r[0]=='succeeded' for r in phases),'AUTOMATION_LIVE11_UNFINISHED_REQUIRES_SEPARATE_RECOVERY')
                ACTIVE=self
                atomic_text(self.folder/'SESSION.json',json.dumps({'sha256':self.sha,'started_at':self.started.isoformat(),
                    'work_until':self.end.isoformat(),'execution_until':self.hard.isoformat(),
                    'pid':os.getpid(),'no_automatic_restart':True}))
                if is_refresh(self.value):
                    with settings(self,c):yield self
                else:yield self
            finally:
                ACTIVE=None;self.lock=None
                # Closing a lost connection releases its session lock; do not
                # replace the original PostgreSQL failure with an unlock error.
                if not c.closed:
                    try:c.execute('SELECT pg_advisory_unlock(%s)',(8611026,))
                    except Exception:pass
