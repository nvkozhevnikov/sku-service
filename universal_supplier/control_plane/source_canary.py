"""Approved bounded collection for all eleven sources; no activation or HTTP.

Preview and enqueue share the same SQL selection and immutable input. The
worker rechecks the limit, target, enabled supplier and selected SQL identities.
"""
from copy import deepcopy
from pathlib import Path
import threading
import math
from .source_jobs import RUNTIME_SOURCES as SOURCES,validate_url
from .source_resume import digest
from .host_fetch import compile_policy
from .rate_control import RequestAborted
from universal_supplier.partner_pilot import DATABASE,SYSTEM,PGDATA

FORMAT='source-bounded-job/v2'
TARGET={'host':DATABASE[0],'port':DATABASE[1],'dbname':DATABASE[2],
        'system_identifier':SYSTEM,'pgdata':str(PGDATA)}

def limit_value(value):
    if type(value) is not int or not 1<=value<=100000:raise ValueError('Общий лимит должен быть целым от 1 до 100000')
    return value

def validate_config(config):
    if config is None or (config.host,config.port,config.dbname)!=DATABASE:
        raise PermissionError('CANARY_DATABASE_NOT_ALLOWED')

def validate_connection(connection):
    with connection.cursor() as q:
        q.execute("SELECT current_database(),system_identifier::text,current_setting('server_version'),current_setting('data_directory'),host(inet_server_addr()),inet_server_port() FROM pg_control_system()")
        db,system,version,data,host,port=q.fetchone()
    if (db,system,version,host,port)!=(DATABASE[2],SYSTEM,'17.11',DATABASE[0],DATABASE[1]) or Path(data).resolve()!=PGDATA.resolve():
        raise PermissionError('CANARY_CLUSTER_IDENTITY_MISMATCH')

def build_preview(supplier_id,supplier,rows,config,limit,mode='REFRESH'):
    """Rows are already SQL-limited: (PK, external_id, URL, total unique known)."""
    from .source_runtime import checked_input,engine_fingerprint,runtime_enabled
    from .queue import normalized_domain
    if not runtime_enabled():raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
    limit_value(limit);validate_config(config)
    code,adapter,base,enabled,crawl,rate=supplier
    if code not in SOURCES or adapter!=code:raise ValueError('CANARY_SOURCE_NOT_SUPPORTED')
    if mode not in ('REFRESH','DISCOVERY'):raise ValueError('SOURCE_MODE')
    if (mode=='REFRESH' and not rows) or len(rows)>limit:raise ValueError('CANARY_SELECTED_URL_LIMIT')
    policy=compile_policy(code,base,rate,approval=(crawl or {}).get('host_policy_approval')).snapshot()
    entries=[{'source_product_id':sid,'source_external_id':str(ext),'url':url} for sid,ext,url,total in rows]
    total=rows[0][3] if rows else 0
    if type(total) is not int or total<len(rows) or any(r[3]!=total for r in rows):raise ValueError('CANARY_KNOWN_COUNT')
    settings=deepcopy(crawl or {})
    for key in ('resume_owner_job_id','resume_input_sha256'):settings.pop(key,None)
    duration=settings.get('max_run_duration_seconds',policy['max_run_duration_seconds'])
    gate=settings.get('safety_gate_size',5);pages=settings.get('max_discovery_pages',100)
    if type(gate) is not int or not 1<=gate<=20:raise ValueError('SAFETY_GATE_SIZE')
    if type(pages) is not int or not 1<=pages<=100000:raise ValueError('DISCOVERY_PAGE_BUDGET')
    if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=86400:raise ValueError('SOURCE_TIME_BUDGET')
    if duration>policy['max_run_duration_seconds']:raise ValueError('TIME_BUDGET_EXCEEDS_HOST_PROFILE')
    settings.update(collection_mode=mode,execution_mode='LIVE',persist_source_data=True,max_products=limit,max_run_duration_seconds=duration,source_canary=True,safety_gate_size=gate,max_discovery_pages=pages)
    from .source_discovery import root_urls
    value={'format':'source-runtime-input/v1','supplier_code':code,'mode':mode,'entries':entries if mode=='REFRESH' else [],'seeds':root_urls(code) if mode=='DISCOVERY' else [],
           'rate_policy':policy,'engine_sha256':engine_fingerprint(),
           'canary':{'format':FORMAT,'mode':mode,'supplier_id':supplier_id,'max_products':limit,'selected':len(entries) if mode=='REFRESH' else 0,
                     'host_policy_approval':settings.get('host_policy_approval'),'safety_gate_size':gate,'max_discovery_pages':pages,'max_run_duration_seconds':duration,
                     'total_known':total,'supplier_enabled':enabled,'database':dict(TARGET)}}
    checked_input(value,code);validate_input(value,settings,config)
    snapshot={'supplier_code':code,'adapter_name':code,'domain':normalized_domain(base),'crawl_type':'full',
              'crawl_settings':settings,'rate_settings':policy,'source_input':value}
    return {'snapshot':snapshot,'confirmation_sha256':digest(snapshot)}

def validate_input(value,settings=None,config=None):
    c=value['canary'];entries=value.get('entries',[]);code=value['supplier_code']
    limit_value(c['max_products'])
    legacy=c.get('format')=='source-canary/v1'
    if legacy and c['max_products']>50:raise ValueError('LEGACY_CANARY_SCOPE_CHANGED')
    mode=value.get('mode');expected_mode='REFRESH' if legacy else c.get('mode')
    if (code not in SOURCES or c.get('format') not in (FORMAT,'source-canary/v1') or mode!=expected_mode or mode not in ('REFRESH','DISCOVERY')
            or (mode=='REFRESH' and value.get('seeds')) or (mode=='DISCOVERY' and (entries or not value.get('seeds'))) or c.get('database')!=TARGET
            or type(c.get('supplier_enabled')) is not bool
            or type(c.get('supplier_id')) is not int or c['supplier_id']<1
            or not (1 if mode=='REFRESH' else 0)<=len(entries)<=c['max_products'] or c.get('selected')!=len(entries)
            or type(c.get('total_known')) is not int or c['total_known']<len(entries)):
        raise ValueError('CANARY_SNAPSHOT_SCOPE_OR_LIMIT')
    ids=[e.get('source_product_id') for e in entries]
    if any(type(sid) is not int or sid<1 for sid in ids) or ids!=sorted(set(ids)):raise ValueError('CANARY_SOURCE_PK_OR_ORDER')
    if len({e['url'] for e in entries})!=len(entries) or len({e['source_external_id'] for e in entries})!=len(entries):raise ValueError('CANARY_DUPLICATE_IDENTITY')
    for entry in entries:validate_url(code,entry['url'])
    from .source_discovery import HOSTS
    # Legacy snapshots retain their original cap and policy; current policy
    # approval is checked by prepare_snapshot/run_source, never fabricated here.
    if not legacy:compile_policy(code,'https://'+HOSTS[code],value['rate_policy'],approval=c.get('host_policy_approval') or (settings or {}).get('host_policy_approval'))
    if not legacy:
        if type(c.get('safety_gate_size')) is not int or not 1<=c['safety_gate_size']<=20:raise ValueError('SAFETY_GATE_SIZE')
        if type(c.get('max_discovery_pages')) is not int or not 1<=c['max_discovery_pages']<=100000:raise ValueError('DISCOVERY_PAGE_BUDGET')
        duration=c.get('max_run_duration_seconds')
        if type(duration) not in (int,float) or not math.isfinite(duration) or not 0<duration<=86400:raise ValueError('SOURCE_TIME_BUDGET')
        if duration>value['rate_policy']['max_run_duration_seconds']:raise ValueError('TIME_BUDGET_EXCEEDS_HOST_PROFILE')
    if settings is not None and (settings.get('source_canary') is not True or settings.get('max_products')!=c['max_products'] or settings.get('max_run_duration_seconds')!=c.get('max_run_duration_seconds',300)
            or (not legacy and any(settings.get(k)!=c[k] for k in ('safety_gate_size','max_discovery_pages','host_policy_approval')))
            or settings.get('collection_mode') not in (mode,'RESUME') or settings.get('persist_source_data') is not True):
        raise ValueError('CANARY_RUNTIME_LIMIT_CHANGED')
    if config is not None:validate_config(config)

def validate_live_selection(connection,value,*,allow_inactive=False):
    """SELECT only, before crawl_run/ingest; an enabled snapshot is no override."""
    validate_connection(connection)
    c=value['canary']
    with connection.cursor() as q:
        q.execute('SELECT code,enabled FROM suppliers WHERE id=%s',(c['supplier_id'],))
        if q.fetchone()!=(value['supplier_code'],True):raise PermissionError('Поставщик выключен; canary заблокирован')
        q.execute('''SELECT id,external_id,coalesce(canonical_url,source_url) FROM source_products
                     WHERE supplier_id=%s AND id=ANY(%s) AND (%s OR active) AND external_id_is_stable ORDER BY id''',
                  (c['supplier_id'],[e['source_product_id'] for e in value['entries']],allow_inactive))
        actual=[{'source_product_id':sid,'source_external_id':str(ext),'url':url} for sid,ext,url in q.fetchall()]
    if actual!=value['entries']:raise PermissionError('CANARY_REGISTERED_SELECTION_CHANGED')

class DispatchBudget:
    """At most N product identities; robots has a separate one-request budget.

    Legacy v1 permits one wire start per URL. V2 permits only the frozen number
    of attempts per selected URL; retries never add an identity outside scope.
    """
    def __init__(self,value,robots_url):
        self.urls={e['url'] for e in value['entries']};self.limit=value['canary']['max_products']
        self.attempts=1 if value['canary']['format']=='source-canary/v1' else value['rate_policy']['max_attempts']
        self.robots_url=robots_url;self.products=[];self.robots=0;self.lock=threading.Lock()
    def __call__(self,url):
        with self.lock:
            if url==self.robots_url:
                if self.robots:raise RequestAborted('CANARY_ROBOTS_REQUEST_LIMIT')
                self.robots+=1;return
            if (url not in self.urls or self.products.count(url)>=self.attempts
                    or (url not in self.products and len(set(self.products))>=self.limit)):
                raise RequestAborted('CANARY_PRODUCT_REQUEST_LIMIT')
            self.products.append(url)
