"""Bounded approved sessions for the EXISTING scheduler/queue/worker, never LIVE11 recovery."""
from contextlib import contextmanager
from datetime import datetime,timezone
import hashlib,json,os,shutil,subprocess,threading,time
from pathlib import Path
from .supplier_automation import digest,settings_for,rate_for
from .source_jobs import RUNTIME_SOURCES

ACTIVE=None
ROOT=Path(__file__).resolve().parents[2]
ENV='STERBRUST_AUTOMATION'


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
    require(set(value['suppliers'])==set(RUNTIME_SOURCES) and value['max_parallel']==2,
            'AUTOMATION_EXACT_11_AND_MAX2_REQUIRED')
    require(value.get('production') is False and value.get('esol') is False and
        value.get('protected_live11_batch_id')=='3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','AUTOMATION_PROTECTED_SCOPE_CHANGED')
    start=datetime.fromisoformat(value['not_before']);end=datetime.fromisoformat(value['execution_until'])
    require(start.tzinfo is not None and end.tzinfo is not None and 0<(end-start).total_seconds()<=86400,
            'AUTOMATION_WINDOW_INVALID')
    require(len(value['mappings1282'])==1282 and len({r[0] for r in value['mappings1282']})==1282,'AUTOMATION_MAPPING_BASELINE_REQUIRED')
    for code,rule in value['suppliers'].items():
        require(isinstance(rule.get('settings_sha256'),str) and len(rule['settings_sha256'])==64,
                'AUTOMATION_SOURCE_SETTINGS_APPROVAL_REQUIRED')
        require(type(rule['supplier_id']) is int and rule['supplier_id']>0 and
            type(rule['min_request_interval_seconds']) in (int,float) and __import__('math').isfinite(rule['min_request_interval_seconds']) and rule['min_request_interval_seconds']>0,
            'AUTOMATION_SOURCE_POLICY_INVALID')
        require(type(rule['max_queue_attempts']) is int and 1<=rule['max_queue_attempts']<=3,
                'AUTOMATION_QUEUE_ATTEMPTS_INVALID')
        for phase in ('REFRESH','DISCOVERY'):
            cap=rule['limits'][phase]
            require(type(cap) is int and 1<=cap<=100000,'AUTOMATION_EXPLICIT_PRODUCT_CAP_REQUIRED')
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


def validate_job(snapshot):
    marker=snapshot.get('automation')
    if not marker:return
    session=require_active(marker.get('approval_sha256'))
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
        rows=c.execute('SELECT id,code,enabled,base_url,rate_settings,crawl_settings FROM suppliers ORDER BY code').fetchall()
        require({r[1] for r in rows}==set(RUNTIME_SOURCES),'AUTOMATION_SOURCE_SET_CHANGED')
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
                require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running') AND coalesce(settings_snapshot->'automation'->>'approval_sha256','')<>%s",(self.sha,)).fetchall(),
                    'AUTOMATION_FOREIGN_ACTIVE_JOB')
                require(not c.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running') AND settings_snapshot ? 'source_batch'").fetchall(),
                    'AUTOMATION_LIVE11_ACTIVE_REQUIRES_SEPARATE_RECOVERY')
                phases=c.execute('''SELECT DISTINCT ON (supplier_id,settings_snapshot->'source_batch'->>'phase') status
                    FROM crawl_jobs WHERE settings_snapshot->'source_batch'->>'id'=%s
                    ORDER BY supplier_id,settings_snapshot->'source_batch'->>'phase',id DESC''',
                    (self.value['protected_live11_batch_id'],)).fetchall()
                require(len(phases)==22 and all(r[0]=='succeeded' for r in phases),'AUTOMATION_LIVE11_UNFINISHED_REQUIRES_SEPARATE_RECOVERY')
                ACTIVE=self
                atomic_text(self.folder/'SESSION.json',json.dumps({'sha256':self.sha,'started_at':self.started.isoformat(),
                    'work_until':self.end.isoformat(),'execution_until':self.hard.isoformat(),
                    'pid':os.getpid(),'no_automatic_restart':True}))
                yield self
            finally:
                ACTIVE=None;self.lock=None
                # Closing a lost connection releases its session lock; do not
                # replace the original PostgreSQL failure with an unlock error.
                if not c.closed:
                    try:c.execute('SELECT pg_advisory_unlock(%s)',(8611026,))
                    except Exception:pass
