import json,os,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from rc_runtime import config,OUT
from psycopg.conninfo import make_conninfo
from competitor_intel.store import Store
import psycopg

def main():
    os.environ['STERBRUST_UNIFIED_RC']='YES'
    cfg=config('app_competitor').kwargs();checks={}
    s=Store(make_conninfo(**cfg))
    checks['positive_unified_role_schema_identity']=s.query('SELECT count(*) n FROM competitor_sources')[0]['n']==12
    try:s.query('SELECT * FROM public.source_products LIMIT 1')
    except psycopg.errors.InsufficientPrivilege:checks['cross_schema_read_denied']=True
    try:s.query('UPDATE public.source_products SET name=name WHERE false')
    except psycopg.errors.InsufficientPrivilege:checks['cross_schema_write_denied']=True
    s.close()
    for name,change in [('wrong_db',{'dbname':'rc_stage_kami'}),('wrong_role',{'user':'app_supplier'}),('wrong_port',{'port':55452}),('wrong_search_path',{'options':'-c search_path=public'})]:
        try:
            other=Store(make_conninfo(**{**cfg,**change}));other.close();checks[name]=False
        except ValueError:checks[name]=True
    receipt={'status':'PASS' if all(checks.values()) else 'FAIL','checks':checks,'source_writes':0}
    (OUT/'UNIFIED_ROLE_ISOLATION_QA.json').write_text(json.dumps(receipt,indent=2),encoding='utf-8');print(json.dumps(receipt,indent=2));assert all(checks.values())
if __name__=='__main__':main()
