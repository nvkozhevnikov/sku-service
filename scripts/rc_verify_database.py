"""Independent payload, history and mapping verification on isolated copies."""
import csv
import json
from psycopg import sql
from psycopg.rows import dict_row
from rc_prepare_database import connect,OUT,TARGET
from rc_consolidate_database import digest,TABLES,REFS

def fingerprint(c,schema='public',exclude=()):
    result={}
    tables=c.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s ORDER BY 1",(schema,)).fetchall()
    for (name,) in tables:
        if name in exclude:continue
        # Stable multiset of complete row payloads, independent of physical order.
        q=sql.SQL('SELECT count(*),md5(coalesce(string_agg(h,\'\' ORDER BY h),\'\')) FROM (SELECT md5(row_to_json(t)::text) h FROM {}.{} t) s').format(sql.Identifier(schema),sql.Identifier(name))
        result[name]=c.execute(q).fetchone()
    return result

def main():
    receipt={'status':'PASS','evidence_class':'LIVE-VERIFIED','ltt_uss':{},'base':{},'competitor':{}}
    with connect(TARGET) as t,connect('rc_stage_ltt_uss') as s,connect('rc_stage_kami') as base,connect('rc_stage_competitor') as comp:
        for c in (t,s,base,comp):c.execute('SET default_transaction_read_only=on')
        rows=t.execute('SELECT source_db,source_schema,table_name,old_pk,new_pk FROM rc_migration_map ORDER BY table_name,old_pk').fetchall()
        with (OUT/'UNIFIED_DB_MAPPING.csv').open('w',newline='',encoding='utf-8') as f:
            writer=csv.writer(f);writer.writerow(['source_db','source_schema','table','old_pk','target_pk']);writer.writerows(rows)
        maps={table:{old:new for db,sch,name,old,new in rows if name==table} for table in TABLES}
        s.row_factory=dict_row
        for table in TABLES:
            original=s.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier(table))).fetchall()
            projected=[]
            for row in original:
                r=dict(row);r['id']=maps[table][r['id']]
                for key,parent in REFS.items():
                    if r.get(key) is not None:r[key]=maps[parent][r[key]]
                projected.append(r)
            with t.cursor(row_factory=dict_row) as cur:
                actual=cur.execute(sql.SQL('SELECT * FROM {} WHERE id=ANY(%s) ORDER BY id').format(sql.Identifier(table)),(list(maps[table].values()),)).fetchall()
            assert digest(actual)==digest(projected),table
            receipt['ltt_uss'][table]={'count':len(original),'source_sha256':digest(original),'target_mapped_sha256':digest(actual),'payload_equal':True}
        target_fingerprints=fingerprint(t)
        for name,(n,fp) in fingerprint(base).items():
            if name in TABLES:
                q=sql.SQL('SELECT count(*),md5(coalesce(string_agg(h,\'\' ORDER BY h),\'\')) FROM (SELECT md5(row_to_json(t)::text) h FROM {} t WHERE id<>ALL(%s)) s').format(sql.Identifier(name))
                current=t.execute(q,(list(maps[name].values()),)).fetchone()
            elif name=='schema_migrations':
                # Two additive migrations preserve all historical ledger entries.
                old=base.execute('SELECT * FROM schema_migrations ORDER BY filename').fetchall()
                current_rows=t.execute('SELECT * FROM schema_migrations WHERE filename=ANY(%s) ORDER BY filename',([r[0] for r in old],)).fetchall()
                assert current_rows==old
                receipt['base'][name]={'historical_rows':len(old),'preserved':True};continue
            elif name=='app_users':
                current=target_fingerprints[name]
            else:current=target_fingerprints[name]
            # 017 adds columns to users/crawl_jobs; both are empty in source.
            assert tuple(current)==(n,fp),(name,current,(n,fp))
            receipt['base'][name]={'rows':n,'fingerprint':fp,'preserved':True}
        before=fingerprint(comp,'competitor');after=fingerprint(t,'competitor',('rc_identity',))
        assert before==after
        receipt['competitor']={'tables':after,'all_payloads_equal':True}
        receipt['per_namespace']=t.execute('SELECT code,count(sp.id),count(sp.id) FILTER(WHERE sp.active) FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.id ORDER BY code').fetchall()
        receipt['target_system_identifier']=str(t.execute('SELECT system_identifier FROM pg_control_system()').fetchone()[0])
        receipt['foreign_keys_not_valid']=t.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]
        receipt['duplicate_source_identity']=t.execute('SELECT count(*) FROM (SELECT supplier_id,external_id FROM source_products GROUP BY 1,2 HAVING count(*)>1) s').fetchone()[0]
        assert receipt['foreign_keys_not_valid']==receipt['duplicate_source_identity']==0
        receipt['known_omissions']=['Vekprom source and newer Intervesp snapshot: source 55452 stopped; no portable dump found.']
    (OUT/'DB_CONSOLIDATION.json').write_text(json.dumps(receipt,default=str,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in receipt.items() if k not in ('ltt_uss','base','competitor')},indent=2))
if __name__=='__main__':main()
