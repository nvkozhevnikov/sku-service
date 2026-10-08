"""Conditional, KAMI-only matching lifecycle. No ingestion/selection writer.

One transaction; accepted decisions only. Old receipts remain immutable. A second
validation permits only the repository's documented audit timestamp changes.
"""
import argparse,hashlib,json,os,subprocess
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
from psycopg import sql
from scripts.propose_kami_policy_v2 import ROOT,OUT,BASE
from scripts.rehearse_kami_feature_restore import connection
from scripts.persist_kami_full_run1 import preflight,runtime_preflight
from scripts.run2_kami_offline import verify_migrations
from scripts.recover_kami_selection_predelete import fingerprints
from scripts.verify_kami_canary import digest
from universal_supplier.postgres import PostgresConfig,PostgresRepository
from universal_supplier.offer_selection import PostgresOfferSelectionStore,OfferSelectionPolicy


def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf8')


def hash_query(c,query,args=()):
    return c.execute("SELECT count(*),encode(sha256(convert_to(coalesce(string_agg(encode(sha256(convert_to(j::text,'UTF8')),'hex'),'' ORDER BY j::text),''),'UTF8')),'hex') FROM ("+query+") x",args).fetchone()


def projections(c,old_max):
    non_kami="SELECT to_jsonb(sp) j FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code<>'kami'"
    return {
        'source_facts':hash_query(c,"SELECT to_jsonb(sp)-'catalog_product_id'-'updated_at' j FROM source_products sp"),
        'non_kami_sources':hash_query(c,non_kami),
        'non_kami_matches':hash_query(c,"SELECT to_jsonb(pm) j FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code<>'kami'"),
        'old_kami_match_evidence':hash_query(c,"SELECT to_jsonb(pm)-'is_current'-'last_validated_at' j FROM product_matches pm JOIN source_products sp ON sp.id=pm.source_product_id JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='kami' AND pm.id<=%s",(old_max,)),
        'match_semantic_without_validation_time':hash_query(c,"SELECT to_jsonb(pm)-'last_validated_at' j FROM product_matches pm"),
        'sequences':c.execute("SELECT sequencename,last_value,increment_by,cache_size FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall()}


def accepted_map(c):
    return dict(c.execute("""SELECT sp.external_id,st.sterbrust_product_id FROM source_products sp
        JOIN suppliers s ON s.id=sp.supplier_id JOIN sterbrust_products st ON st.catalog_product_id=sp.catalog_product_id
        JOIN product_matches pm ON pm.source_product_id=sp.id AND pm.is_current
        WHERE s.code='kami' AND pm.auto_accepted AND pm.status IN ('EXACT_MATCH','HIGH_CONFIDENCE_MATCH')
          AND pm.catalog_product_id=sp.catalog_product_id"""))


def gates():
    git=lambda *a:subprocess.check_output(['git',*a],cwd=ROOT,text=True).strip()
    assert git('branch','--show-current')=='feature/universal-supplier-kami-rc1'
    assert git('rev-parse','HEAD')=='c6e26ec242899535dcef06f90a789a0a5c0110d0'
    for name,sha in [('develop','deefb595868d19ba471b9b943ff779194e231c26'),('production','36924836bb02965e857173773cdb70f302914643')]:
        assert git('rev-parse',name)==git('rev-parse','origin/'+name)==sha
    data=json.loads((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes())
    qa=json.loads((OUT/'V2_1_QA_VERIFIED.json').read_bytes())
    assert qa['status']=='CODEX_QA_INSPECTED_PASS' and qa['count']>=200 and qa['confirmed_false_positives']==0
    assert qa['proposal_sha256']==hashlib.sha256((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes()).hexdigest()
    assert qa['policy_code_sha256']==hashlib.sha256((ROOT/'universal_supplier/matching_policy_v2.py').read_bytes()).hexdigest()
    assert len(data['rows'])==5264 and len({r['external_id'] for r in data['rows']})==5264
    assert data['summary']['fixture_counts']=={'PASS':35}
    assert all(f['status']=='PASS' for f in data['fixtures'])
    for name in ('V2_1_TARGETED_TESTS.xml','V2_1_FOCUSED_TESTS.xml'):
        suites=ET.parse(OUT/name).getroot().findall('testsuite')
        assert suites and all(int(s.get(k,'0'))==0 for s in suites for k in ('failures','errors','skipped'))
    accepted=[r for r in data['rows'] if r['classification']=='EXISTING_CONFIRMED']
    assert all(r['audit']['confirmed'] and not r['audit']['blocking_reasons'] for r in accepted)
    return data,accepted


def verify_scope(c,before,projection,old_max,expected,*,second=False):
    after=fingerprints(c);post=projections(c,old_max)
    for table,value in before.items():
        if table not in ('source_products','product_matches','sequences'): assert after[table]==value,table+' changed'
    for key in ('source_facts','non_kami_sources','non_kami_matches','old_kami_match_evidence'):
        assert post[key]==projection[key],key+' changed'
    assert accepted_map(c)==expected,'Current accepted identities mismatch'
    assert c.execute("SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='kami'").fetchone()[0]==5264
    assert c.execute("SELECT count(*) FILTER(WHERE active),count(*) FROM offers o JOIN suppliers s ON s.id=o.supplier_id WHERE s.code='kami'").fetchone()==(0,5264)
    assert c.execute("SELECT enabled FROM suppliers WHERE code='kami'").fetchone()==(False,)
    assert c.execute("""SELECT count(*) FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        WHERE s.code='kami' AND sp.catalog_product_id IS NOT NULL""").fetchone()[0]==len(expected)
    bseq={r[0]:r[1:] for r in projection['sequences']};aseq={r[0]:r[1:] for r in post['sequences']}
    serial=c.execute("SELECT pg_get_serial_sequence('product_matches','id')").fetchone()[0].split('.')[-1]
    for name,values in bseq.items():
        if not second and name==serial:
            assert values[2]==1 and aseq[name]==(values[0]+len(expected)*values[1],values[1],values[2]),'Unexpected match sequence increment'
        else: assert aseq[name]==values,'Unrelated sequence drift: '+name
    if second:
        assert post['match_semantic_without_validation_time']==projection['match_semantic_without_validation_time']
        assert after['product_matches']['rows']==before['product_matches']['rows']
    return after,post


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--apply',action='store_true');parser.add_argument('--second-run',action='store_true');args=parser.parse_args()
    data,accepted=gates();expected={r['external_id']:str(r['sterbrust_product_id']) for r in accepted}
    marker=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    receipt_name='V2_1_SECOND_RUN_NOOP.json' if args.second_run else 'V2_1_APPLY_RECEIPT.json'
    if args.second_run:
        first=json.loads((OUT/'V2_1_APPLY_RECEIPT.json').read_bytes())
        assert first['status']=='KAMI_V2_1_APPLIED_INDEPENDENTLY_VERIFIED'
        second=json.loads((OUT/'V2_1_SECOND_PROPOSAL_DATA.json').read_bytes())
        second_map={r['external_id']:r['sterbrust_product_id'] for r in second['rows'] if r['sterbrust_product_id']}
        assert second_map==expected and second['summary']['counts']==data['summary']['counts']
        baseline=first['after_fingerprints']
    else:
        assert not (OUT/'V2_1_APPLY_RECEIPT.json').exists(),'Existing apply receipt: inspect instead of replaying first apply'
        baseline=json.loads((OUT/'DB_STATE_VERIFIED.json').read_bytes())['fingerprints']
    with connection('universal_supplier_kami_rc') as c:
        preflight(c,marker)
        with c.transaction():
            c.execute('SET TRANSACTION READ ONLY');verify_migrations(c);assert fingerprints(c)==baseline,'Fresh DB drift'
    secret=json.loads((Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005/runtime.json').read_bytes())['DB_PASSWORD']
    config=PostgresConfig('127.0.0.1',55451,'universal_supplier_kami_rc','kami_app',secret,'disable')
    repo=PostgresRepository(config);runtime_preflight(repo);c=repo.connection
    events=Counter();started=datetime.now(timezone.utc).isoformat()
    try:
        with c.transaction():
            if not args.apply:c.execute('SET TRANSACTION READ ONLY')
            else:
                c.execute("SET LOCAL lock_timeout='5s'");c.execute("SET LOCAL statement_timeout='180s'")
                c.execute('LOCK TABLE source_products,product_matches IN SHARE ROW EXCLUSIVE MODE')
            before=fingerprints(c);assert before==baseline,'Writer-context drift'
            assert c.execute('SELECT count(*) FROM review_decisions').fetchone()[0]==0,'Effective manual state changed: STOP'
            old_max=c.execute('SELECT coalesce(max(id),0) FROM product_matches').fetchone()[0]
            projection=projections(c,old_max)
            sources={r[0]:r[1:] for r in c.execute("SELECT sp.external_id,sp.name,sp.external_id_is_stable,sp.catalog_product_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code='kami'")}
            assert len(sources)==5264
            for r in accepted:
                assert sources[r['external_id']][0]==r['name'] and sources[r['external_id']][1] is True
                target=c.execute("""SELECT st.active,st.catalog_product_id,cp.id,
                    (SELECT count(*) FROM sterbrust_products other WHERE other.catalog_product_id=st.catalog_product_id)
                    FROM sterbrust_products st LEFT JOIN catalog_products cp ON cp.id=st.catalog_product_id
                    WHERE st.sterbrust_product_id=%s""",(r['sterbrust_product_id'],)).fetchone()
                assert target and target[0] is True and target[1] is not None and target[1]==target[2] and target[3]==1,'Missing/nonunique physical canonical target: '+r['sterbrust_product_id']
                if not args.second_run:assert sources[r['external_id']][2] is None
            if not args.apply:
                save('V2_1_APPLY_PREFLIGHT.json',dict(status='ALL_GATES_FRESH_DB_PASS_NOT_APPLIED',accepted=len(accepted),before_fingerprints=before,
                    projections=projection,policy_sha=qa_hash(),DB_writes=0,supplier_HTTP=0))
                print('PRE_APPLY_PASS',len(accepted));return
            for r in accepted:
                decision={'source_external_id':r['external_id'],'sterbrust_product_id':r['sterbrust_product_id'],
                    'status':'HIGH_CONFIDENCE_MATCH','match_method':'POLICY_V2_1_'+r['acceptance_path'],
                    'confidence_score':1,'auto_accepted':True,'evidence':r['audit']['confirmed']['evidence'],
                    'conflicts':{},'warnings':{'CANONICAL_NAME_REVIEW':True} if r['audit']['confirmed']['evidence']['canonical_name_review'] else {}}
                event=repo.persist_match(decision,started,supplier_code='kami');events[event]+=1
            assert events==({'VALIDATED_UNCHANGED':len(expected)} if args.second_run else {'INSERTED_EVENT':len(expected)})
            after,post=verify_scope(c,before,projection,old_max,expected,second=args.second_run)
        # COMMIT has finished; a fresh independent admin session rechecks all
        # receipts and table/sequence scope, not merely the writing session.
        with connection('universal_supplier_kami_rc') as independent:
            preflight(independent,marker)
            with independent.transaction():
                independent.execute('SET TRANSACTION READ ONLY')
                independent_after,independent_post=verify_scope(independent,before,projection,old_max,expected,second=args.second_run)
                assert independent_after==after
                policy=OfferSelectionPolicy.load(ROOT/'config/offer_selection.json')
                decisions=PostgresOfferSelectionStore(independent).evaluate_all(policy)
                selection_evaluation={'statuses':dict(Counter(d.selection_status for d in decisions)),
                    'evaluated_products':len(decisions),'selection_apply':False}
        receipt={'status':'V2_1_SECOND_RUN_SEMANTIC_NOOP_VERIFIED' if args.second_run else 'KAMI_V2_1_APPLIED_INDEPENDENTLY_VERIFIED',
            'started_at':started,'recorded_at':datetime.now(timezone.utc).isoformat(),'accepted_rows':len(expected),
            'events':dict(events),'INSERT':0 if args.second_run else len(expected),'UPDATE':0,
            'UNCHANGED':len(expected) if args.second_run else 0,'REVIEW':5264-len(expected),'CONFLICT':0,
            'unexpected_semantic_delta':0,'before_fingerprints':before,'after_fingerprints':after,
            'before_projections':projection,'after_projections':post,'independent_verification':True,
            'source_commercial_identity_fields_unchanged':True,'predecessor_namespaces_unchanged':True,
            'selection_table_unchanged':True,'selection_evaluator_readonly':selection_evaluation,
            'supplier_disabled':True,'offers_inactive':True,'supplier_HTTP':0,
            'audit_timestamp_updates_expected':bool(args.second_run),'exact_physical_noop':False if args.second_run else None,
            'logical_counts_definition':'INSERT=new decision history events; UPDATE=semantic changes on revalidation; UNCHANGED=semantically revalidated decisions',
            'audit_revalidations':len(expected) if args.second_run else 0,
            'DB_matching_write_only':True,'rule_version_storage_note':'Existing repository lifecycle column stage3b-1.0; V2.1 is pinned in method/evidence.',
            'proposal_sha256':hashlib.sha256((OUT/'V2_1_PROPOSAL_DATA.json').read_bytes()).hexdigest()}
        save(receipt_name,receipt);print(json.dumps({k:v for k,v in receipt.items() if k not in ('before_fingerprints','after_fingerprints','before_projections','after_projections')},ensure_ascii=False))
    finally:repo.close()


def qa_hash():
    return hashlib.sha256((ROOT/'universal_supplier/matching_policy_v2.py').read_bytes()).hexdigest()


if __name__=='__main__':main()
