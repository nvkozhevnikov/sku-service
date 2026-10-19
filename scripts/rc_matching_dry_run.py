"""Offline before/after decisions using copied DB and Git-pinned engines.

Network beyond the single pinned local read-only DB is denied. No apply path.
"""
import argparse,csv,json,os,socket,sys,hashlib,subprocess,tarfile
from collections import Counter
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'reports/integration/MATCHING_DRY_RUN'

def deny_external():
    original=socket.socket.connect
    def connect(sock,address):
        if not isinstance(address,tuple) or address[:2]!=('127.0.0.1',55461):
            raise AssertionError('External network denied during frozen dry-run')
        return original(sock,address)
    socket.socket.connect=connect

def worker(engine,label):
    sys.path.insert(0,str(engine))
    import psycopg
    from universal_supplier.identity_service import _load_cards
    from universal_supplier.matching_service import load_registry_from_postgres,match_cards
    from universal_supplier.postgres import PostgresRepository
    cfg=json.loads((ROOT/'reports/integration/private-runtime.json').read_text(encoding='utf-8'))
    deny_external()
    results=[]
    with psycopg.connect(host='127.0.0.1',port=55461,dbname='sterbrust_unified_rc_20261019',user='reporting_ro',password=cfg['reporting_ro'],autocommit=True,options='-c default_transaction_read_only=on') as con:
        assert con.execute('SHOW default_transaction_read_only').fetchone()[0]=='on'
        registry=load_registry_from_postgres(con)
        cards,_=_load_cards(con,None)
        groups={}
        for card in cards:groups.setdefault(card.supplier_code,[]).append(card)
        for code,cards in sorted(groups.items()):
            links=PostgresRepository.load_existing_link_contexts(SimpleNamespace(connection=con),code)
            for mode,contexts in [('with_existing_links',links),('fresh',{})]:
                rows=match_cards(cards,registry,contexts)
                for r in rows:
                    r['pass_mode']=mode
                    r['supplier_code']=code
                    r['external_id']=r['source_external_id']
                results.extend(rows)
            print(label,code,len(cards),'done',flush=True)
        manual=con.execute('SELECT count(*) FROM review_decisions').fetchone()[0]
        preserved=con.execute("SELECT md5(coalesce(string_agg(row_to_json(r)::text,'' ORDER BY id),'')) FROM review_decisions r").fetchone()[0]
    (OUT/(label+'.json')).write_text(json.dumps({'rows':results,'manual_decisions':manual,'manual_sha256':preserved},default=str,ensure_ascii=False),encoding='utf-8')

def main():
    OUT.mkdir(parents=True,exist_ok=True)
    parser=argparse.ArgumentParser();parser.add_argument('--worker',nargs=2);args=parser.parse_args()
    if args.worker:return worker(Path(args.worker[0]),args.worker[1])
    before=OUT/'before-engine'
    archive=OUT/'base-engine.tar'
    data=subprocess.check_output(['git','-C',str(ROOT),'archive','58152e4','sterbrust_matching','universal_supplier'])
    if before.exists():
        assert archive.read_bytes()==data
        assert not (OUT/'before.json').exists() and not (OUT/'after.json').exists()
    else:
        archive.write_bytes(data);before.mkdir()
        with tarfile.open(archive) as tar:tar.extractall(before,filter='data')
    after=ROOT.parent/'sku-service-matching-f01-f03-20261019'
    assert subprocess.check_output(['git','-C',str(after),'rev-parse','HEAD']).decode().strip()=='0c0164a5ea49659a895866766731bf83adddea76'
    for engine,label in [(before,'before'),(after,'after')]:
        subprocess.run([sys.executable,str(Path(__file__).resolve()),'--worker',str(engine),label],check=True,cwd=OUT)
    old=json.loads((OUT/'before.json').read_text(encoding='utf-8'));new=json.loads((OUT/'after.json').read_text(encoding='utf-8'))
    def key(r):return (r['pass_mode'],r['supplier_code'],r['external_id'])
    a={key(r):r for r in old['rows']};b={key(r):r for r in new['rows']}
    assert a.keys()==b.keys()
    transitions=Counter();changed=[];manual_changes=[]
    for k,left in a.items():
        right=b[k]
        fields=('status','sterbrust_product_id','auto_accepted','match_method')
        if all(left.get(x)==right.get(x) for x in fields):continue
        transition=('AUTO_TO_REVIEW_OR_CONFLICT' if left['auto_accepted']=='TRUE' and right['auto_accepted']!='TRUE' else 'NOT_AUTO_TO_AUTO' if left['auto_accepted']!='TRUE' and right['auto_accepted']=='TRUE' else 'DECISION_OR_CANDIDATE_CHANGED')
        transitions[(k[0],transition)]+=1
        reason={x:right.get(x) for x in right if any(p in x for p in ('reason','conflict','warning','evidence'))}
        changed.append({'pass_mode':k[0],'supplier_code':k[1],'external_id':k[2],'transition':transition,'old_status':left['status'],'new_status':right['status'],'old_id':left['sterbrust_product_id'],'new_id':right['sterbrust_product_id'],'old_method':left['match_method'],'new_method':right['match_method'],'reasons':json.dumps(reason,ensure_ascii=False)})
        if 'MANUAL' in str(left['match_method']) and left['sterbrust_product_id']!=right['sterbrust_product_id']:manual_changes.append(k)
    assert old['manual_sha256']==new['manual_sha256'] and not manual_changes
    fields=list(changed[0]) if changed else ['pass_mode','supplier_code','external_id','transition','reasons']
    with (OUT/'CHANGED_DECISIONS.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(changed)
    summary={'evidence_class':'REPOSITORY-VERIFIED + LIVE-VERIFIED copied read-only DB','before_sha':'58152e4bd390361d3e97ee475bae78a91295e241','after_F01_SHA':'0c0164a5ea49659a895866766731bf83adddea76','decisions_each':len(a),'transitions':{'|'.join(k):v for k,v in transitions.items()},'manual_rows':old['manual_decisions'],'manual_ids_changed':manual_changes,'source_link_writes':0,'external_http':0,'scope_note':'Exact original F01/F03 patch comparison; unified integration changes excluded.'}
    (OUT/'SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8')
    print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
