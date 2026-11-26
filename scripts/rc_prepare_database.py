"""Create only NEW isolated RC/staging DBs; sources are backup/SELECT-only.

No existing source cluster may be started, stopped, restored or migrated here.
"""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import ctypes
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
BIN = Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin'
OUT = ROOT/'reports/integration'
DATA = Path('C:/Users/man503/.codex/visualizations/2026/10/19/01a153ff-dc7c-77b3-a6be-d05f81a86e5c/rc-pg-20261019')
PORT = 55461
TARGET = 'sterbrust_unified_rc_20261019'

def run(name, *args, env=None):
    executable = str(BIN/(name+'.exe'))
    if os.name == 'nt':
        buf=ctypes.create_unicode_buffer(32768)
        if not ctypes.windll.kernel32.GetShortPathNameW(executable,buf,len(buf)):
            raise RuntimeError('PostgreSQL ASCII executable path unavailable')
        executable=buf.value
    # Windows postmaster can inherit pipe handles after pg_ctl exits; files
    # avoid blocking communicate() for the lifetime of the server.
    log=OUT/(name+'-last.log')
    with log.open('wb') as handle:
        p = subprocess.run([executable, *map(str,args)], stdout=handle, stderr=handle, env=env, cwd=DATA.parent)
    if p.returncode:
        raise RuntimeError(f'{name} exit {p.returncode}: '+log.read_text(errors='replace')[-1800:])
    return log.read_bytes()

def connect(db='postgres'):
    return psycopg.connect(host='127.0.0.1', port=PORT, user='rc_owner', dbname=db, autocommit=True)

def restore(db, path):
    with connect() as c:
        c.execute(sql.SQL('CREATE DATABASE {} TEMPLATE template0').format(sql.Identifier(db)))
    run('pg_restore','-h','127.0.0.1','-p',PORT,'-U','rc_owner','--no-owner','--no-acl','--exit-on-error','-d',db,path)

def backup(label, kwargs):
    dest = OUT/(label+'.dump')
    if dest.exists(): raise RuntimeError(f'Refuse overwrite: {dest.name}')
    # Confirm identity and read-only settings before invoking pg_dump.
    with psycopg.connect(**kwargs, connect_timeout=3, autocommit=True, options='-c default_transaction_read_only=on') as c:
        identity = c.execute('SELECT current_database(),current_user,version()').fetchone()
        if identity[0] != kwargs['dbname']: raise RuntimeError('Source identity mismatch')
    env = {**os.environ, 'PGPASSWORD':kwargs.get('password',''), 'PGOPTIONS':'-c default_transaction_read_only=on', 'PGCONNECT_TIMEOUT':'3'}
    run('pg_dump','-h',kwargs['host'],'-p',kwargs['port'],'-U',kwargs['user'],'-d',kwargs['dbname'], '-Fc','--no-owner','--no-acl','-f',dest, env=env)
    return dest, identity

def main():
    OUT.mkdir(exist_ok=True, parents=True)
    if '--resume-new-empty-cluster' in sys.argv:
        with connect() as c:
            assert Path(c.execute('SHOW data_directory').fetchone()[0]).resolve()==DATA.resolve()
            assert c.execute("SELECT count(*) FROM pg_database WHERE datname NOT IN ('postgres','template0','template1')").fetchone()[0]==0
    else:
        if DATA.exists(): raise RuntimeError('New cluster path must not exist')
        run('initdb','-D',DATA,'-U','rc_owner','-A','trust','--encoding=UTF8','--locale=C')
        with (DATA/'postgresql.conf').open('a') as f:
            f.write("\nlisten_addresses='127.0.0.1'\nport=55461\n")
        run('pg_ctl','-D',DATA,'-l',DATA/'server.log','-w','start')
    from psycopg.conninfo import conninfo_to_dict
    dsn = json.loads((WORK/'sterbrust-competitive-intelligence/competitor-data/dev-config.json').read_text())['dsn']
    sources = {}
    path, ident = backup('competitor_source',conninfo_to_dict(dsn))
    sources['competitor']={'path':str(path),'identity':ident}
    cfg = json.loads((Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/vekprom-intervesp-20261008/runtime.json').read_text(encoding='utf-8-sig'))
    kwargs = {key:cfg['DB_'+key.upper()] for key in ('host','port','user','password')}
    kwargs['dbname']=cfg['DB_NAME']
    try:
        path, ident=backup('vekprom_intervesp_source',kwargs)
        sources['vekprom_intervesp']={'path':str(path),'identity':ident}
    except psycopg.OperationalError:
        sources['vekprom_intervesp']={'status':'BLOCKED_SOURCE_NOT_RUNNING_AND_NO_DUMP'}
    sources['kami']={'path':str(WORK/'sku-service-kami/release/database/universal_supplier_kami_matching_v2_1_2026-10-08.dump')}
    sources['ltt_uss']={'path':str(WORK/'sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/SOURCE_INGESTION_FINALIZATION_2026-10-16/LTT_USS_SOURCE_INGESTION_VERIFIED_2026-10-16.dump')}
    for key,value in sources.items():
        if 'path' not in value: continue
        p=Path(value['path']);value['bytes']=p.stat().st_size
        value['sha256']=hashlib.file_digest(p.open('rb'),'sha256').hexdigest()
        db='rc_stage_'+key
        restore(db,p);value['stage_db']=db
    restore(TARGET,Path(sources['kami']['path']))
    (OUT/'DB_INPUTS.json').write_text(json.dumps(sources,indent=2),encoding='utf-8')
    print(json.dumps(sources,indent=2))

if __name__=='__main__':main()
