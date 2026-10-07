"""Exact compensating recovery only; no evaluation, quarantine or matcher.

All six original fields come from preserved evidence whose hash is pinned to
the pre-delete table fingerprint. INSERT only exact absent approved PKs.
"""
import argparse
import hashlib
import json
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from psycopg import sql
from scripts.rehearse_kami_feature_restore import connection
from scripts.persist_kami_full_run1 import preflight
from scripts.verify_kami_canary import digest

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
OUT = BASE/'SELECTION_PRE_DELETE_RECOVERY_2026-10-06'
FIELDS = ('catalog_product_id','selected_offer_id','selection_status','rule_version','evidence','evaluated_at')
START = '292bd0cf27f2fc8f6f4577560cbb1d824b0880ae'
PRESERVED_DB = 'universal_supplier_kami_restore_20261006'


def save(name, value):
    OUT.mkdir(exist_ok=True)
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')


def rows(conn):
    return [r[0] for r in conn.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')]


def exact_missing(previous, current, allowed):
    before={r['catalog_product_id']:r for r in previous}
    now={r['catalog_product_id']:r for r in current}
    if len(before)!=len(previous) or len(now)!=len(current):
        raise ValueError('Duplicate selection PK')
    if not set(now)<=set(before) or any(before[k]!=r for k,r in now.items()):
        raise ValueError('Existing selection row differs from preserved tuple')
    missing=set(before)-set(now)
    if not missing<=allowed:
        raise ValueError('Missing selection row outside exact approved recovery')
    if missing and missing!=allowed:
        raise ValueError('Partial unexpected recovery state')
    return [before[k] for k in sorted(missing)]


def fingerprints(conn):
    """Full row content, no excluded columns; SHA256 each row + ordered SHA256 aggregate."""
    result={}
    for (table,) in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename"):
        query=sql.SQL("SELECT count(*),encode(sha256(convert_to(coalesce(string_agg(encode(sha256(convert_to(to_jsonb(t)::text,'UTF8')),'hex'),'' ORDER BY to_jsonb(t)::text),''),'UTF8')),'hex') FROM {} t").format(sql.Identifier(table))
        count,sha=conn.execute(query).fetchone()
        result[table]={'rows':count,'sha256':sha}
    result['sequences']={'sha256':digest(conn.execute("SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall())}
    return result


def preserved(marker, plan):
    with connection(PRESERVED_DB) as conn, conn.transaction():
        conn.execute('SET TRANSACTION READ ONLY')
        assert conn.execute('SELECT current_database(),inet_server_port(),system_identifier::text FROM pg_control_system()').fetchone()==(PRESERVED_DB,55451,marker['system_identifier'])
        previous=rows(conn)
    assert len(previous)==121
    assert digest(previous)==plan['before_hashes']['catalog_offer_selection']
    assert Counter(r['selection_status'] for r in previous)=={'selected':117,'no_eligible_offer':4}
    assert all(set(r)==set(FIELDS) for r in previous)
    return previous


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');args=parser.parse_args()
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True,stderr=subprocess.DEVNULL).strip()
    assert git('branch','--show-current')=='feature/universal-supplier-kami-rc1'
    assert git('rev-parse','HEAD')==START
    assert git('rev-parse','develop')=='deefb595868d19ba471b9b943ff779194e231c26'
    assert git('rev-parse','production')=='36924836bb02965e857173773cdb70f302914643'
    marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    plan=json.loads((BASE/'SELECTION_RECONCILIATION_2026-10-06/PLAN.json').read_bytes())
    receipt=json.loads((BASE/'SELECTION_RECONCILIATION_2026-10-06/VERIFIED.json').read_bytes())
    allowed={d['catalog_product_id'] for d in plan['diff'] if d['action']=='DELETE'}
    assert len(allowed)==89
    previous=preserved(marker,plan)
    with connection('universal_supplier_kami_rc') as conn:
        preflight(conn,marker)
        with conn.transaction():
            if args.apply:
                conn.execute("SET LOCAL lock_timeout='5s'")
                tables=[r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
                for table in tables:
                    mode='EXCLUSIVE' if table=='catalog_offer_selection' else 'SHARE'
                    conn.execute(sql.SQL('LOCK TABLE {} IN '+mode+' MODE').format(sql.Identifier(table)))
            else:
                conn.execute('SET TRANSACTION READ ONLY')
            current=rows(conn);missing=exact_missing(previous,current,allowed)
            if not missing:
                assert current==previous
                print(json.dumps({'status':'EXACT_RECOVERY_NOOP','INSERT':0,'UPDATE':0,'DELETE':0,'rows':len(current),'table_sha256':digest(current)}))
                save('SECOND_RUN_NOOP.json',{'status':'EXACT_RECOVERY_NOOP','INSERT':0,'UPDATE':0,'DELETE':0,'rows':len(current),'table_sha256':digest(current)})
                return
            assert len(current)==32 and len(missing)==89
            assert digest(current)==receipt['after_hashes']['catalog_offer_selection']
            save('EXACT_89_PREVIOUS_TUPLES.json',{'evidence_source':PRESERVED_DB,'pre_delete_table_SHA256':digest(previous),'fields':FIELDS,'rows':missing})
            save('PRE_DELETE_SNAPSHOT.json',{'rows':previous,'table_SHA256':digest(previous)})
            before=fingerprints(conn)
            save('PLAN.json',{'status':'READ_ONLY_EXACT_RECOVERY_PLAN_VERIFIED','current_rows':32,'missing_rows':89,
                'conflicting_existing_rows':0,'pre_delete_SHA256':digest(previous),'before_SHA256':digest(current),'before_fingerprints':before,
                'full_previous_tuples':'EXACT_89_PREVIOUS_TUPLES.json','evaluator_invocations':0,'HTTP':0})
            if not args.apply:
                print(json.dumps({'status':'READ_ONLY_EXACT_RECOVERY_PLAN_VERIFIED','current_rows':32,'missing_rows':89,'pre_delete_SHA256':digest(previous)}));return
            if (OUT/'VERIFIED.json').exists():raise ValueError('Verified recovery receipt exists; no second mutation')
            for record in missing:
                cursor=conn.execute("INSERT INTO catalog_offer_selection(catalog_product_id,selected_offer_id,selection_status,rule_version,evidence,evaluated_at) VALUES(%s,%s,%s,%s,%s::jsonb,%s::timestamptz)",
                    (record['catalog_product_id'],record['selected_offer_id'],record['selection_status'],record['rule_version'],json.dumps(record['evidence'],ensure_ascii=False),record['evaluated_at']))
                assert cursor.rowcount==1
            recovered=rows(conn)
            assert recovered==previous and digest(recovered)==plan['before_hashes']['catalog_offer_selection']
            assert Counter(r['selection_status'] for r in recovered)=={'selected':117,'no_eligible_offer':4}
            assert {r['catalog_product_id']:r for r in recovered if r['catalog_product_id'] not in allowed}=={r['catalog_product_id']:r for r in current}
            assert conn.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
            inside=fingerprints(conn)
            assert all(inside[k]==v for k,v in before.items() if k!='catalog_offer_selection')
            assert exact_missing(previous,recovered,allowed)==[]
    with connection('universal_supplier_kami_rc') as conn:
        preflight(conn,marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            independent=rows(conn);after=fingerprints(conn)
            assert independent==previous and digest(independent)==plan['before_hashes']['catalog_offer_selection']
            assert after==inside
            assert exact_missing(previous,independent,allowed)==[]
    report={'status':'SELECTION_PRE_DELETE_STATE_RECOVERED_VERIFIED','recorded_at':datetime.now(timezone.utc).isoformat(),
        'restored_rows':89,'total_rows':121,'selected':117,'no_eligible_offer':4,
        'pre_delete_table_sha256':digest(previous),'before_table_sha256':digest(current),'after_table_sha256':digest(independent),
        'before_fingerprints':before,'after_fingerprints':after,'independent_fresh_connection':True,'remaining32_rows_unchanged':True,
        'all_unrelated_public_tables_and_sequences_unchanged':True,'evidence_and_evaluated_at_exact':True,'SQL_tables_written':['catalog_offer_selection'],
        'SQL_INSERT':89,'SQL_UPDATE':0,'SQL_DELETE':0,'source_link_quarantine':0,'matching':0,'HTTP':0,'Sterbrust_ESOL_writes':0,
        'evaluator_invocations':0,'commit_push':0,'HEAD':git('rev-parse','HEAD'),'develop':git('rev-parse','develop'),'production':git('rev-parse','production'),
        'scope_STOP':'Do not proceed to quarantine/matching/selection evaluation or release work'}
    save('VERIFIED.json',report)
    print(json.dumps({k:v for k,v in report.items() if 'fingerprints' not in k}))


if __name__=='__main__':main()
