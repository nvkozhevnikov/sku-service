"""Fail-closed bounded Partner-ST pilot, using the existing queue and parser."""
import hashlib,json,os
from pathlib import Path
from urllib.parse import urlsplit

DATABASE=('127.0.0.1',55461,'rc_verify_vi_20261019')
SYSTEM='7698346631476390904'
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
            q.execute("SELECT current_database(),system_identifier::text,current_setting('server_version'),current_setting('data_directory'),inet_server_addr()::text,inet_server_port() FROM pg_control_system()")
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
def snapshot(limit,rows):
    limit_value(limit)
    batch=[{'source_product_id':int(sid),'external_id':str(ext),'url':safe_url(url)} for sid,ext,url in rows]
    p={'limit':limit,'batch':batch,'batch_sha256':digest(batch),'mode':'SAVED_URL_LIVE','database':list(DATABASE),'system_identifier':SYSTEM}
    validate_snapshot(p);return p

def validate_snapshot(p):
    limit=limit_value(p['limit']);batch=p['batch']
    if p.get('mode')!='SAVED_URL_LIVE' or p.get('database')!=list(DATABASE) or p.get('system_identifier')!=SYSTEM:raise ValueError('PILOT_SNAPSHOT_SCOPE')
    if not isinstance(batch,list) or not 1<=len(batch)<=limit or p.get('batch_sha256')!=digest(batch):raise ValueError('PILOT_BATCH_LIMIT_OR_HASH')
    for key in ('url','external_id','source_product_id'):
        if len({r[key] for r in batch})!=len(batch):raise ValueError('PILOT_DUPLICATE_'+key)
    for r in batch:
        safe_url(r['url'])
        if not isinstance(r['external_id'],str) or not r['external_id'] or type(r['source_product_id']) is not int or r['source_product_id']<1:raise ValueError('PILOT_SOURCE_ID_REQUIRED')
    return batch
