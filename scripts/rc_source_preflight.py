"""Read-only local inputs and byte inventory. Never prints credentials."""
import hashlib
import json
import os
from pathlib import Path
import psycopg
from psycopg.conninfo import conninfo_to_dict

ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT.parent
OUT = ROOT / 'reports/integration'
OUT.mkdir(parents=True, exist_ok=True)

def main():
    result = {'files': [], 'sources': []}
    candidates = [WORK/'sku-service-ltt-uss/reports/LTT_USS_INTEGRATION_2026-10-13/SOURCE_INGESTION_FINALIZATION_2026-10-16/LTT_USS_SOURCE_INGESTION_VERIFIED_2026-10-16.dump']
    candidates += list((WORK/'sku-service-kami/release/database').glob('*.dump'))
    for p in candidates:
        result['files'].append({'path':str(p), 'bytes':p.stat().st_size, 'sha256':hashlib.file_digest(p.open('rb'), 'sha256').hexdigest()})
    configs = [('competitor', json.loads((WORK/'sterbrust-competitive-intelligence/competitor-data/dev-config.json').read_text())['dsn'])]
    for p in (Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier').rglob('runtime.json'):
        data = json.loads(p.read_text(encoding='utf-8-sig'))
        print('runtime', p.parent.name, 'keys', list(data))
    for label, dsn in configs:
        cfg = conninfo_to_dict(dsn)
        rec = {'label':label, 'identity':{k:v for k,v in cfg.items() if k in ('host','port','dbname','user')}}
        try:
            with psycopg.connect(dsn, connect_timeout=3, autocommit=True, options='-c default_transaction_read_only=on') as con:
                rec['server'] = con.execute('SELECT current_database(),current_user,version()').fetchone()
                rec['tables'] = con.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1").fetchall()
        except psycopg.Error as e:
            rec['error_type'] = type(e).__name__
        result['sources'].append(rec)
    (OUT/'SOURCE_INPUT_PREFLIGHT.json').write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, indent=2))

if __name__ == '__main__': main()
