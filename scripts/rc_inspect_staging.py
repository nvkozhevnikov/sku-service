"""Bounded release dependency inventory over isolated restored copies only."""
import json
from pathlib import Path
from psycopg import sql
from rc_prepare_database import connect,OUT

def main():
    result={}
    for db in ['rc_stage_kami','rc_stage_ltt_uss','rc_stage_competitor','rc_stage_vekprom_intervesp']:
        try:
            with connect(db) as c:
                c.execute('SET default_transaction_read_only=on')
                tables=[r[0] for r in c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY 1")]
                rec={'tables':{}}
                for table in tables:
                    count=c.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(table))).fetchone()[0]
                    rec['tables'][table]=count
                if 'suppliers' in tables:
                    rec['suppliers']=c.execute('SELECT s.id,s.code,count(sp.id) products FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.id ORDER BY s.id').fetchall()
                    rec['fks']=c.execute("SELECT conrelid::regclass::text,confrelid::regclass::text,pg_get_constraintdef(oid) FROM pg_constraint WHERE contype='f' AND connamespace='public'::regnamespace ORDER BY 1").fetchall()
                    rec['columns']=c.execute("SELECT table_name,column_name,data_type,column_default FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position").fetchall()
                result[db]=rec
        except Exception as e:result[db]={'error':type(e).__name__}
    (OUT/'STAGING_INVENTORY.json').write_text(json.dumps(result,default=str,indent=2))
    for db,rec in result.items():print(db,rec.get('suppliers'),rec.get('tables'),rec.get('error'))
if __name__=='__main__':main()
