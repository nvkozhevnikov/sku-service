"""Resume only the explicitly initialized competitor cluster; no re-init."""
import json
import subprocess
from pathlib import Path
import psycopg
root=Path(__file__).resolve().parents[1]
data=root/'competitor-data'
cluster=data/'pg17'
bin_dir=Path.home()/'AppData/Local/Temp/universal_supplier_stage4_pg17_runtime/bin'
if (cluster/'PG_VERSION').read_text().strip()!='17': raise SystemExit('Wrong cluster')
cfg=json.loads((data/'dev-config.json').read_text())
if 'port=55454 dbname=sterbrust_competitor_intel user=competitor_dev' not in cfg['dsn']: raise SystemExit('Wrong config')
subprocess.run([str(bin_dir/'pg_ctl.exe'),'start','-D',str(cluster),'-l',str(data/'postgres.log'),'-o','-h 127.0.0.1 -p 55454','-w','-t','60'],check=True)
with psycopg.connect(cfg['dsn'].replace('dbname=sterbrust_competitor_intel','dbname=postgres'),autocommit=True) as conn:
    if not conn.execute('SELECT 1 FROM pg_database WHERE datname=%s',('sterbrust_competitor_intel',)).fetchone():
        conn.execute('CREATE DATABASE sterbrust_competitor_intel')
print('Dedicated competitor dev database ready / loopback55454')
