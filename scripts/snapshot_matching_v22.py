"""Freeze six stable namespaces from approved idle RC; no 55452/Vekprom access.

SQL read-only repeatable-read transaction; never imports live runners or writers.
Bulk input artifacts stay ignored. No HTTP, dump, process/STOP manipulation.
"""
import hashlib,json,os,subprocess
from pathlib import Path
from collections import Counter
import psycopg
from psycopg.rows import dict_row

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'
SOURCES=('partner_st','optimum','intervesp','beka_mak','beka_mak_tr','kami')
EXPECTED={'partner_st':1225,'optimum':1353,'intervesp':1642,'beka_mak':109,'beka_mak_tr':78,'kami':5264}
PRIVATE=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'

def save(name,value):
    path=OUT/name
    if path.exists():raise RuntimeError('Frozen artifact exists, use it instead of resnapshot: '+name)
    path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def jsonlines(name,rows):
    path=OUT/name
    if path.exists():raise RuntimeError('Frozen artifact exists: '+name)
    digest=hashlib.sha256();count=0
    with path.open('wb') as f:
        for row in rows:
            body=(json.dumps(row,ensure_ascii=False,sort_keys=True,default=str)+'\n').encode('utf-8')
            f.write(body);digest.update(body);count+=1
    return {'rows':count,'bytes':path.stat().st_size,'sha256':digest.hexdigest()}

def main():
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='feature/universal-supplier-matching-v2-2-ai-review'
    OUT.mkdir(parents=True,exist_ok=True)
    password=(PRIVATE/'pgpass.conf').read_text().strip().split(':',4)[4]
    with psycopg.connect(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',user='kami_admin',password=password,connect_timeout=5,row_factory=dict_row) as c:
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
        c.execute("SET LOCAL statement_timeout='60s'")
        identity=c.execute("SELECT current_database() AS db,host(inet_server_addr()) AS host,inet_server_port() AS port,current_setting('server_version') AS version,system_identifier::text AS system_id FROM pg_control_system()").fetchone()
        assert identity==dict(db='universal_supplier_kami_rc',host='127.0.0.1',port=55451,version='17.11',system_id='7693098812512405428')
        counts={r['code']:r['n'] for r in c.execute('SELECT s.code,count(sp.id) AS n FROM suppliers s JOIN source_products sp ON sp.supplier_id=s.id WHERE s.code=ANY(%s) GROUP BY s.code',(list(SOURCES),))}
        assert counts==EXPECTED
        properties=list(c.execute('SELECT pp.* FROM product_properties pp JOIN source_products sp ON sp.id=pp.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=ANY(%s) ORDER BY pp.source_product_id,pp.position,pp.id',(list(SOURCES),)))
        sources=list(c.execute("""SELECT s.code AS supplier,sp.*,pm.status AS current_match_status,pm.auto_accepted,
            pm.match_method,pm.confidence_score,pm.evidence AS match_evidence,pm.conflicts AS match_conflicts,
            pm.catalog_product_id AS match_catalog_product_id,pm.rule_version AS current_rule_version,
            target.sterbrust_product_id AS proposed_sterbrust_product_id,
            target.name AS proposed_name,linked.sterbrust_product_id AS linked_sterbrust_product_id,
            pid.decision AS identity_decision,pid.own_model,pid.product_kind,pid.reference_model,
            pid.best_sterbrust_id,pid.decision_reason,pid.identity_characteristics,pid.conflicts AS identity_conflicts
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            LEFT JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
            LEFT JOIN sterbrust_products target ON target.catalog_product_id=pm.catalog_product_id
            LEFT JOIN sterbrust_products linked ON linked.catalog_product_id=sp.catalog_product_id
            LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
            WHERE s.code=ANY(%s) ORDER BY s.code,sp.external_id""",(list(SOURCES),)))
        assert len(sources)==9671 and len({(r['supplier'],r['external_id']) for r in sources})==9671
        canonical=list(c.execute("""SELECT sterbrust_product_id,name,brand_raw,manufacturer_raw,model_raw,
            article_raw,active,category_id,category_name,category_path,properties,product_url,snapshot_hash,
            last_synced_at FROM sterbrust_products ORDER BY sterbrust_product_id"""))
        # Physical consolidated RC has 59427 records. The full pinned public
        # registry (59500 IDs) remains authoritative for offline candidate search.
        assert len(canonical)==59427
        # DB property dictionaries preserve their raw representation. Rich saved
        # canonical own description tables are separately loaded offline later.
        artifacts={name:jsonlines(name,rows) for name,rows in [('SOURCE_ROWS.jsonl',sources),('SOURCE_PROPERTIES.jsonl',properties),('CANONICAL_INDEX.jsonl',canonical)]}
        status=Counter((r['supplier'],r['current_match_status'] or 'NONE') for r in sources)
    save('INPUT_MANIFEST.json',{'provenance':'LIVE-VERIFIED read-only stable isolated RC export',
        'identity':identity,'namespaces':counts,'artifacts':artifacts,
        'persisted_match_status_counts':{s:{k:n for (s2,k),n in status.items() if s2==s} for s in SOURCES},
        'scope':'Six stable namespaces only; no Vekprom/55452 access',
        'database_writes':0,'HTTP':0,'snapshot_read_only':True,'canonical_count':len(canonical),
        'canonical_scope_note':'Physical RC index59427 is not full canonical absence proof; use pinned full59500 saved public registry offline',
        'develop_SHA':subprocess.check_output(['git','rev-parse','origin/develop'],cwd=ROOT,text=True).strip()})
    save('CHECKPOINT.json',{'status':'READONLY_INPUTS_FROZEN','resume_from':'INPUT_MANIFEST.json then offline inventory/cohort analysis, no resnapshot',
        'DB_writes':0,'HTTP':0,'active_55452_access':0,'Vekprom_manipulation':0})
    print(json.dumps({'namespaces':counts,'statuses':{s:{k:n for (s2,k),n in status.items() if s2==s} for s in SOURCES},'canonical':len(canonical),'files':artifacts}))

if __name__=='__main__':main()
