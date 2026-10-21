"""Fail-closed bounded Partner-ST pilot, using the existing queue and parser."""
import hashlib,json,os
from pathlib import Path
from urllib.parse import urlsplit

DATABASE=('127.0.0.1',55461,'rc_verify_vi_20261019')
SYSTEM='7698346631476390904'
RATE_PROFILES={'conservative':(1,2.0),'compare_2':(2,1.0),'compare_4':(4,0.5),'compare_4_fast':(4,0.2)}
def rate_profile(name):
    if name not in RATE_PROFILES:raise ValueError('UNKNOWN_PILOT_RATE_PROFILE')
    workers,interval=RATE_PROFILES[name]
    return {'max_concurrency':workers,'min_request_interval_seconds':interval,'max_attempts':1,'request_timeout_seconds':20.0,'max_run_duration_seconds':300.0}
PGDATA=Path('C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019')

def enabled():return os.environ.get('STERBRUST_PARTNER_PILOT')=='YES'
def validate_config(config):
    if (config.host,config.port,config.dbname)!=DATABASE:raise PermissionError('PILOT_DATABASE_NOT_ALLOWED')
def connect_postgres(config,**kwargs):
    import psycopg
    if not enabled():return psycopg.connect(**config.kwargs(),**kwargs)
    from psycopg.rows import tuple_row
    validate_config(config)
    opts=kwargs.pop('options','')
    if opts and opts!='-c default_transaction_read_only=on':raise PermissionError('PILOT_CONNECTION_OPTIONS')
    c=psycopg.connect(**config.kwargs(),**kwargs,options='-c default_transaction_read_only=on')
    try:
        with c.cursor(row_factory=tuple_row) as q:
            q.execute("SELECT current_database(),system_identifier::text,current_setting('server_version'),current_setting('data_directory'),host(inet_server_addr()),inet_server_port() FROM pg_control_system()")
            db,system,version,data,host,port=q.fetchone()
            if (db,system,version,host,port)!=(DATABASE[2],SYSTEM,'17.11',DATABASE[0],DATABASE[1]) or Path(data).resolve()!=PGDATA.resolve():raise PermissionError('PILOT_CLUSTER_IDENTITY_MISMATCH')
        if not opts:c.execute('SET default_transaction_read_only=off')
        return c
    except BaseException:
        c.close();raise

def limit_value(value):
    if isinstance(value,bool) or not isinstance(value,int) or not 1<=value<=50:raise ValueError('PILOT_LIMIT_MUST_BE_1_TO_50')
    return value

def safe_url(url):
    p=urlsplit(url)
    if p.scheme!='https' or p.hostname not in ('partner-st.ru','www.partner-st.ru') or p.port not in (None,443) or p.username or p.password or p.query or p.fragment or p.path in ('','/'):raise ValueError('PILOT_UNSAFE_PRODUCT_URL')
    return url

def digest(batch):return hashlib.sha256(json.dumps(batch,sort_keys=True,separators=(',',':')).encode()).hexdigest()
def snapshot(limit,rows,profile='conservative'):
    limit_value(limit)
    batch=[{'source_product_id':int(sid),'external_id':str(ext),'url':safe_url(url)} for sid,ext,url in rows]
    rate_profile(profile)
    p={'rate_profile':profile,'limit':limit,'batch':batch,'batch_sha256':digest(batch),'mode':'SAVED_URL_LIVE','database':list(DATABASE),'system_identifier':SYSTEM}
    validate_snapshot(p);return p

def validate_snapshot(p):
    rate_profile(p.get('rate_profile','conservative'))
    limit=limit_value(p['limit']);batch=p['batch']
    if p.get('mode')!='SAVED_URL_LIVE' or p.get('database')!=list(DATABASE) or p.get('system_identifier')!=SYSTEM:raise ValueError('PILOT_SNAPSHOT_SCOPE')
    if not isinstance(batch,list) or not 1<=len(batch)<=limit or p.get('batch_sha256')!=digest(batch):raise ValueError('PILOT_BATCH_LIMIT_OR_HASH')
    for key in ('url','external_id','source_product_id'):
        if len({r[key] for r in batch})!=len(batch):raise ValueError('PILOT_DUPLICATE_'+key)
    for r in batch:
        safe_url(r['url'])
        if not isinstance(r['external_id'],str) or not r['external_id'] or type(r['source_product_id']) is not int or r['source_product_id']<1:raise ValueError('PILOT_SOURCE_ID_REQUIRED')
    return batch


def read_stop_reason(path):
    """Return the operator's reason; an empty stop file still means STOP."""
    try:
        return Path(path).read_text(encoding='utf-8').strip()[:1000] or 'STOP_REQUESTED'
    except FileNotFoundError:
        return None
