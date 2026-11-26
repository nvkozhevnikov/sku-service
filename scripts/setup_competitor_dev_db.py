"""Initialize a NEW local cluster only; refuses occupied port/existing directory.

Credentials stay in ignored competitor-data; no Docker/services/schedulers or
connections to existing supplier databases. Explicitly authorized task scope.
"""
import json
import secrets
import socket
import subprocess
from pathlib import Path

root = Path(__file__).resolve().parents[1]
binary = Path.home() / 'AppData/Local/Temp/universal_supplier_stage4_pg17_runtime/bin'
data_root = root / 'competitor-data'
data_root.mkdir(exist_ok=True)
cluster = data_root / 'pg17'
if cluster.exists(): raise SystemExit('Existing directory: refusing reinitialization')
with socket.socket() as s:
    if s.connect_ex(('127.0.0.1',55454)) == 0: raise SystemExit('Port 55454 occupied; refusing')
version = subprocess.check_output([str(binary/'postgres.exe'),'--version'],text=True)
if '17.11' not in version: raise SystemExit('Pinned PG17.11 required')
secret = secrets.token_urlsafe(36)
pw = data_root / 'init-password.txt'; pw.write_text(secret+'\n',encoding='utf-8')
try:
    subprocess.run([str(binary/'initdb.exe'),'-D',str(cluster),'-U','competitor_dev','--encoding=UTF8','--auth-host=scram-sha-256','--auth-local=scram-sha-256','--pwfile',str(pw)],check=True)
finally:
    pw.unlink(missing_ok=True)
dsn = f'host=127.0.0.1 port=55454 dbname=sterbrust_competitor_intel user=competitor_dev password={secret}'
(data_root/'dev-config.json').write_text(json.dumps({'dsn':dsn}),encoding='utf-8')
secret = None
subprocess.run([str(binary/'pg_ctl.exe'),'start','-D',str(cluster),'-l',str(data_root/'postgres.log'),'-o','-h 127.0.0.1 -p 55454','-w','-t','60'],check=True)
import psycopg
cfg = json.loads((data_root/'dev-config.json').read_text())
with psycopg.connect(cfg['dsn'].replace('dbname=sterbrust_competitor_intel','dbname=postgres'),autocommit=True) as conn:
    conn.execute('CREATE DATABASE sterbrust_competitor_intel')
print('Created new isolated PG17.11 cluster / loopback55454 / sterbrust_competitor_intel')
