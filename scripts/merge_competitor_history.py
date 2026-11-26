"""Allowlisted additive competitor-history merge; source and supplier data stay immutable."""
import argparse,json,hashlib,sys
from pathlib import Path
import psycopg
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
ROOT=Path(__file__).resolve().parents[1]
TABLES=['competitor_sources','competitor_runs','competitor_pages','competitor_page_versions','competitor_campaign_items','competitor_observations','competitor_ai_analysis','competitor_source_surfaces','competitor_historical_seeds','competitor_meta']
MUTABLE={'competitor_pages':{'published_at','last_seen_at','current_content_hash'},'competitor_source_surfaces':{'status','fetch_status','reason','observed_at','raw_capture_ref','details'}}
TARGETS={'ci_restore_target_20261028','rc_verify_vi_20261019'}
def connect(db):return psycopg.connect(host='127.0.0.1',port=55461,dbname=db,user='rc_owner',connect_timeout=5,row_factory=dict_row)
def key(table,r):return (r['source_id'],r['seed_id']) if table=='competitor_historical_seeds' else (r['id'],) if 'id' in r else (r['singleton'],)
def merge(database,output,apply=False):
 if database not in TARGETS:raise ValueError('TARGET_NOT_ALLOWED')
 with connect('ci_restore_source_20261028') as source,connect(database) as target:
  source.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
  target.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
  target.execute("SET LOCAL lock_timeout='5s'")
  assert target.execute('select system_identifier::text s from pg_control_system()').fetchone()['s']=='7698346631476390904'
  assert not target.execute("select id from public.crawl_jobs where status in ('queued','running')").fetchall()
  assert not target.execute("select id from competitor.competitor_runs where status='RUNNING'").fetchall()
  plan={};data={}
  for table in TABLES:
   old=target.execute(sql.SQL('SELECT * FROM competitor.{}').format(sql.Identifier(table))).fetchall()
   new=source.execute(sql.SQL('SELECT * FROM public.{}').format(sql.Identifier(table))).fetchall()
   aa={key(table,r):r for r in old};bb={key(table,r):r for r in new}
   if aa.keys()-bb.keys():raise ValueError('TARGET_ONLY_HISTORY_REQUIRES_REVIEW:'+table)
   inserts=[r for k,r in bb.items() if k not in aa];updates=[]
   for k in aa.keys() & bb.keys():
    changed={col for col in aa[k] if aa[k][col]!=bb[k][col]}
    if changed-MUTABLE.get(table,set()):raise ValueError('IMMUTABLE_HISTORY_CONFLICT:'+table)
    if changed:
     stamp='last_seen_at' if table=='competitor_pages' else 'observed_at'
     if bb[k][stamp]<aa[k][stamp]:raise ValueError('TARGET_NEWER_REQUIRES_REVIEW:'+table)
     updates.append((k,bb[k],sorted(changed)))
   plan[table]={'before':len(old),'source':len(new),'insert':len(inserts),'update_current':len(updates),'overlap_unchanged':len(old)-len(updates),'skipped_unresolved':0,'conflicts':0}
   data[table]=(new,inserts,updates)
  if apply:
   # Identity/natural-key conflicts are SQL errors; never ON CONFLICT DO NOTHING.
   for table in TABLES:
    rows,inserts,updates=data[table]
    if inserts:
     cols=list(inserts[0]);cmd=sql.SQL('INSERT INTO competitor.{} ({}) VALUES ({})').format(sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,cols)),sql.SQL(',').join(sql.Placeholder() for _ in cols))
     with target.cursor() as cur:cur.executemany(cmd,[[Jsonb(r[k]) if isinstance(r[k],(dict,list)) else r[k] for k in cols] for r in inserts])
    for k,r,cols in updates:
     cmd=sql.SQL('UPDATE competitor.{} SET {} WHERE id=%s').format(sql.Identifier(table),sql.SQL(',').join(sql.SQL('{}=%s').format(sql.Identifier(c)) for c in cols))
     target.execute(cmd,[Jsonb(r[c]) if isinstance(r[c],(dict,list)) else r[c] for c in cols]+[k[0]])
   for table in TABLES:
    if table in ('competitor_historical_seeds','competitor_meta'):continue
    seq=table+'_id_seq'
    old=target.execute('select last_value from pg_sequences where schemaname=%s and sequencename=%s',('competitor',seq)).fetchone()['last_value'] or 1
    source_value=source.execute('select last_value from pg_sequences where schemaname=%s and sequencename=%s',('public',seq)).fetchone()['last_value'] or 1
    maximum=target.execute(sql.SQL('select coalesce(max(id),1) n from competitor.{}').format(sql.Identifier(table))).fetchone()['n']
    target.execute('select setval(%s::regclass,%s,true)',('competitor.'+seq,max(old,source_value,maximum)))
   for table in TABLES:
    actual=target.execute(sql.SQL('SELECT * FROM competitor.{}').format(sql.Identifier(table))).fetchall()
    assert {key(table,r):r for r in actual}=={key(table,r):r for r in data[table][0]},table
   target.commit()
  else:target.rollback()
 result={'database':database,'applied':apply,'tables':plan,'source':'ci_restore_source_20261028','ID_remap':0,'unresolved_skips':0,'supplier_DML':0}
 Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8');return result
if __name__=='__main__':
 p=argparse.ArgumentParser();p.add_argument('--database',choices=sorted(TARGETS),required=True);p.add_argument('--output',type=Path,required=True);p.add_argument('--apply',action='store_true');a=p.parse_args()
 r=merge(a.database,a.output,a.apply);print(json.dumps({'database':a.database,'applied':a.apply,'inserted':sum(x['insert'] for x in r['tables'].values()),'updated_current':sum(x['update_current'] for x in r['tables'].values()),'conflicts':0}))
