"""Explicit canonical apply/assembly/dump gates. Never supplier HTTP/import.

User-facing operations are KAMI-only. Aggregate/advisory evidence is separate.
"""
import argparse,hashlib,json,os,subprocess
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from xml.etree import ElementTree as ET
import psycopg
from psycopg import sql
from scripts.reconcile_kami_selection import connection,PRIVATE
from scripts.persist_kami_full_run1 import preflight
from scripts.recover_kami_selection_predelete import fingerprints,digest
from scripts.run2_kami_offline import verify_migrations
from universal_supplier.offer_selection import PostgresOfferSelectionStore,OfferSelectionPolicy
from universal_supplier.yml_feed import PostgresFeedStore,FeedPolicy,selected_row_violations
from universal_supplier.xml_export import generate_canonical_xml,FALSE_MATCH_GUARDS
from universal_supplier.effective_identity import effective_identity_accepted,effective_manual_mapping_sql
from universal_supplier.proposed_offer_selection import input_hash
from universal_supplier.operation_release import plan_operations,operation_xml,operation_manifest
from universal_supplier.release_export import summarize

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'reports/KAMI_INTEGRATION_2026-10-05'
OUT=BASE/'FINAL_RELEASE_2026-10-07'
RELEASE=ROOT/'release/KAMI_FEATURE_RC_2026-10-07'
MARKER=json.loads((BASE/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
POLICY=OfferSelectionPolicy.load(ROOT/'config/offer_selection.json')
RESTORE_DB='universal_supplier_kami_release_restore_20261007'


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def save(path,value):path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def selections(c):return [r[0] for r in c.execute('SELECT to_jsonb(t) FROM catalog_offer_selection t ORDER BY to_jsonb(t)::text')]


def guard(c):
    preflight(c,MARKER)
    assert len(verify_migrations(c))==15
    assert subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()=='feature/universal-supplier-kami-rc1'
    for branch,expected in [('develop','deefb595868d19ba471b9b943ff779194e231c26'),('production','36924836bb02965e857173773cdb70f302914643')]:
        assert subprocess.check_output(['git','rev-parse',branch],cwd=ROOT,text=True).strip()==expected


def tests_gate():
    for name in ('LIFECYCLE.xml','FOCUSED.xml'):
        tree=ET.parse(OUT/name)
        assert not list(tree.iter('failure')) and not list(tree.iter('error')) and not list(tree.iter('skipped')),name


def winner_gate(c):
    snapshot=PostgresFeedStore(c).load_snapshot()
    forbidden={'NO_CURRENT_ACCEPTED_MATCH','MATCH_CATALOG_PRODUCT_MISMATCH','SOURCE_CATALOG_PRODUCT_MISMATCH',
               'SUPPLIER_RELATIONSHIP_MISMATCH','DISABLED_OR_MISSING_SUPPLIER','INACTIVE_OR_MISSING_SELECTED_OFFER','INACTIVE_OR_MISSING_SOURCE_PRODUCT'}
    violations=selected_row_violations(snapshot.selected_rows,FeedPolicy.load(ROOT/'config/yml_feed.json'),len(snapshot.selected_rows))
    assert not [v for v in violations if v.split(':')[0] in forbidden]
    bad={(s,e) for s,e,_ in FALSE_MATCH_GUARDS}
    assert not [r for r in snapshot.selected_rows if (r.supplier_code,str(r.supplier_external_id)) in bad]
    return {'selected':len(snapshot.selected_rows),'commercial_exclusions':dict(Counter(v.split(':')[0] for v in violations))}


def apply():
    tests_gate();baseline=json.loads((OUT/'PREFLIGHT.json').read_bytes())['fingerprints']
    with connection() as c:
        guard(c)
        with c.transaction():
            c.execute('SET TRANSACTION ISOLATION LEVEL SERIALIZABLE')
            before=fingerprints(c);assert before==baseline,'State drift before apply'
            c.execute('LOCK TABLE catalog_offer_selection IN SHARE ROW EXCLUSIVE MODE')
            store=PostgresOfferSelectionStore(c);decisions=store.evaluate_all(POLICY)
            assert len({d.catalog_product_id for d in decisions})==len(decisions)
            save(OUT/'EXPECTED_CANONICAL_DECISIONS.json',[asdict(d) for d in decisions])
            old={r['catalog_product_id']:r for r in selections(c)}
            first=store.apply(decisions)
            actual=selections(c);by_id={r['catalog_product_id']:r for r in actual}
            assert set(by_id)==set(old)|{d.catalog_product_id for d in decisions}
            for d in decisions:
                r=by_id[d.catalog_product_id]
                assert (r['selected_offer_id'],r['selection_status'],r['rule_version'],r['evidence'])==(d.selected_offer_id,d.selection_status,d.rule_version,d.evidence)
            structural=winner_gate(c)
            second=store.apply(store.evaluate_all(POLICY));assert second['changed']==0
            after=fingerprints(c)
            assert all(after[t]==v for t,v in before.items() if t!='catalog_offer_selection'),'Unrelated mutation; ROLLBACK'
            delta={'KEEP':sum(by_id[k]==r for k,r in old.items()),'UPDATE':sum(by_id[k]!=r for k,r in old.items()),
                   'INSERT':len(set(by_id)-set(old)),'DELETE':0}
    with connection() as c,c.transaction():
        c.execute('SET TRANSACTION READ ONLY');guard(c)
        assert fingerprints(c)==after
        receipt={'status':'LIVE_VERIFIED_PERSISTED_CANONICAL_SELECTION','first_apply':first,'second_apply':second,'delta':delta,
                 'persisted_table_sha256':digest(selections(c)),'fingerprints':after,'winner_gates':structural,
                 'rules':POLICY.version,'before_selection_sha256':digest(list(old.values())),'unrelated_tables_unchanged':True}
        save(OUT/'CANONICAL_RECEIPT.json',receipt)
    print(json.dumps({k:v for k,v in receipt.items() if k!='fingerprints'}))


def canonical_rows(c):
    q=c.execute(f"""SELECT s.code AS source,p.external_id,p.id AS source_product_id,p.name,p.brand_raw AS brand,p.sku,p.source_url,
          p.catalog_product_id AS source_catalog_product_id,p.active AS source_active,s.enabled AS supplier_enabled,
          o.id AS offer_id,o.price,o.currency,o.price_type,o.active AS offer_active,o.availability_normalized AS availability,o.quantity,
          pm.status AS match_status,pm.catalog_product_id AS match_catalog_id,pm.evidence AS matching_evidence,
          sb.sterbrust_product_id,sb.name AS sterbrust_name,sb.active AS canonical_active,
          {effective_manual_mapping_sql('p.id','p.catalog_product_id')} AS manual_effective,
          cs.selected_offer_id AS winner_id
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
        LEFT JOIN offers o ON o.source_product_id=p.id AND o.offer_kind='default'
        LEFT JOIN product_matches pm ON pm.source_product_id=p.id AND pm.is_current
        LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=p.catalog_product_id
        LEFT JOIN catalog_offer_selection cs ON cs.catalog_product_id=p.catalog_product_id AND cs.selection_status='selected'
        ORDER BY s.code,p.external_id""")
    rows=[dict(zip([x.name for x in q.description],r)) for r in q.fetchall()]
    for r in rows:
        r['external_id']=str(r['external_id'])
        proof={'source_product_id':r['source_product_id'],'source_catalog_product_id':r['source_catalog_product_id'],
               'catalog_product_id':r['source_catalog_product_id'],'current_match_status':r['match_status'],'current_match_catalog_product_id':r['match_catalog_id']}
        if r['manual_effective']:proof.update(effective_manual_source_product_id=r['source_product_id'],effective_manual_catalog_product_id=r['source_catalog_product_id'])
        accepted=effective_identity_accepted(**proof) and bool(r['canonical_active']) and bool(r['sterbrust_product_id'])
        r['classification']='EXISTING_CONFIRMED' if accepted else 'CONFLICT' if r['match_status']=='CONFLICT' else 'REVIEW'
        r['effective_identity_evidence']=proof;r['selection_authority']='PERSISTED_CANONICAL_SQL'
        r['selected_offer']=accepted and r['winner_id']==r['offer_id'];r['selection_eligible_verified']=r['selected_offer']
        r['price_state']='numeric' if r['price'] is not None else 'missing'
        r['evidence_ref']='persisted_sql/product_matches/source='+str(r['source_product_id'])
        if not accepted:r['sterbrust_product_id']=None
    return rows


def artifacts(c,*,verify_only=False):
    receipt=json.loads((OUT/'CANONICAL_RECEIPT.json').read_bytes())
    assert digest(selections(c))==receipt['persisted_table_sha256'],'Stale/superseded receipt'
    rows=canonical_rows(c)
    advisory=json.loads((BASE/'MATCHING/MATCHING_ACCEPTED.json').read_bytes())['rows']
    assert len(rows)==len(advisory) and {(r['source'],r['external_id']) for r in rows}=={(r['source'],str(r['external_id'])) for r in advisory}
    # No ready KAMI SQL state/current accepted FULL proof exists. Do not borrow
    # historical predecessor FULL proposals as operation authority or re-match.
    plan=plan_operations(rows,[],{},scope=('kami',))
    inputs={'CANONICAL_ROWS':input_hash(rows),'SELECTION_RECEIPT':sha(OUT/'CANONICAL_RECEIPT.json'),
            'ADVISORY_QA_ONLY':sha(BASE/'MATCHING/MATCHING_ACCEPTED.json')}
    manifest=operation_manifest(plan,inputs)
    bodies={name:operation_xml(plan,name.removesuffix('.xml'),inputs) for name in manifest['files']}
    if verify_only:
        assert manifest==json.loads((RELEASE/'FINAL_XML_MANIFEST.json').read_bytes())
        assert all(body==(RELEASE/name).read_bytes() for name,body in bodies.items())
        return {'status':'READONLY_REGENERATED_OPERATION_COUNTS_HASHES_IDENTICAL','counts':manifest['files']}
    RELEASE.mkdir(parents=True,exist_ok=True)
    for name,body in bodies.items():
        if (RELEASE/name).exists():assert (RELEASE/name).read_bytes()==body,'No overwrite changed artifact'
        (RELEASE/name).write_bytes(body)
    save(RELEASE/'FINAL_XML_MANIFEST.json',manifest);save(RELEASE/'OPERATION_PLAN.json',plan)
    (RELEASE/'accepted').mkdir(exist_ok=True)
    save(RELEASE/'accepted/MATCHING_ACCEPTED.json',{'authority':'PERSISTED_CANONICAL_SQL','rows':rows})
    save(RELEASE/'ADVISORY_MATCHING_QA.json',{'authority':'ADVISORY_QA_ONLY','rows':advisory})
    scope={'user_facing':'KAMI_ONLY_DELTA','kami_rows':sum(r['source']=='kami' for r in rows),
           'predecessor_rows':sum(r['source']!='kami' for r in rows),'aggregate_rows':len(rows),
           'canonical_summary':summarize(rows),'advisory_summary':summarize(advisory),'selected_canonical':receipt['winner_gates']['selected'],
           'new_count':len(plan['new']),'price_updates_count':len(plan['prices']),
           'current_price_GET_required':False,'reason':'No KAMI existing/accepted FULL operation candidates; no current price comparison needed',
           'excluded_counts':dict(Counter(r['reason'] for r in plan['excluded']))}
    save(RELEASE/'RELEASE_SCOPE.json',scope)
    internal=generate_canonical_xml(PostgresFeedStore(c).load_snapshot())
    (RELEASE/'INTERNAL_CANONICAL_PREVIEW.xml').write_bytes(internal.data)
    save(RELEASE/'INTERNAL_FEED_QA.json',internal.manifest)
    freeze={'authority':'PERSISTED_CANONICAL_SQL','summary':summarize(rows),'advisory_summary':summarize(advisory),
            'advisory_selection_count':len(json.loads((BASE/'SELECTION_RECONCILIATION_2026-10-06/SELECTION_PERSISTED.json').read_bytes())['selected']),
            'operation_counts':{'NEW_PRODUCTS':len(plan['new']),'PRICE_UPDATES':len(plan['prices'])},'release_scope':'KAMI_ONLY_DELTA',
            'selected_offers':receipt['winner_gates']['selected'],'advisory_selection':'historical28 QA only; not reranked',
            'freeze_date':'2026-10-07','input_sha256':{'MATCHING_ACCEPTED.json':sha(RELEASE/'accepted/MATCHING_ACCEPTED.json')},
            'selection_table_sha256':receipt['persisted_table_sha256'],'import_authorized':False,'visual_QA':'DEFERRED_BY_OPERATOR'}
    save(RELEASE/'FREEZE_MANIFEST.json',freeze)
    qa=['# KAMI operation QA','', 'READY_ARTIFACT / NOT_IMPORT_AUTHORIZED. Scope: KAMI-only delta.',
        '', '## NEW PRODUCTS',str(len(plan['new']))+' operations. Only current accepted scoped FULL proof; no predecessor NEW borrowed.',
        '', '## PRICE UPDATES',str(len(plan['prices']))+' operations. No current Sterbrust price guessed; comparison evidence required; Sterbrust GET0.',
        '', '## Canonical vs advisory',json.dumps(scope,ensure_ascii=False,indent=2),
        '', '## EXCLUDED / REVIEW','Full scoped per-identity ledger: EXCLUDED_REVIEW.md. Aggregate feed is INTERNAL evidence, not either user-facing XML.',
        'Internal preview applies unchanged commercial guards; unknown price roles are excluded, never invented.']
    (RELEASE/'QA_REPORT.md').write_text('\n'.join(qa),encoding='utf-8')
    lines=['# Excluded scoped KAMI rows','', '| Source | Identity | Name | Reason |','|---|---|---|---|']
    for r in plan['excluded']:
        if r['source']=='kami':lines.append('| '+' | '.join(str(r.get(k,'')).replace('|','\\|').replace('\n',' ') for k in ('source','external_id','name','reason'))+' |')
    (RELEASE/'EXCLUDED_REVIEW.md').write_text('\n'.join(lines),encoding='utf-8')
    save(RELEASE/'SHA256_MANIFEST.json',{p.name:{'sha256':sha(p),'size':p.stat().st_size} for p in RELEASE.iterdir() if p.is_file() and p.name!='SHA256_MANIFEST.json'})
    save(OUT/'ASSEMBLY_VERIFIED.json',{'scope':scope,'xml_manifest':manifest,'receipt_SHA':inputs['SELECTION_RECEIPT'],'hidden_matching':False,'hidden_reselection':False,'supplier_HTTP':0,'Sterbrust_GET':0})
    print(json.dumps({'scope':scope,'files':manifest['files']},ensure_ascii=False))


def assemble():
    with connection() as c,c.transaction():
        c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY');guard(c)
        assert fingerprints(c)==json.loads((OUT/'CANONICAL_RECEIPT.json').read_bytes())['fingerprints']
        artifacts(c)


def dump_restore():
    assert json.loads((OUT/'ASSEMBLY_VERIFIED.json').read_bytes())['xml_manifest']['status']=='READY_ARTIFACT / NOT_IMPORT_AUTHORIZED'
    disposition=json.loads((OUT/'FULL_NONPASS_CLASSIFICATION.json').read_bytes())
    assert not any(r['classification'] in {'UNKNOWN','CURRENT_DEFECT'} for r in disposition['items'])
    binary=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin'
    target=ROOT/'release/database/universal_supplier_kami_final_2026-10-07.dump'
    assert not target.exists(),'Never overwrite dump'
    os.environ['PGPASSFILE']=str(PRIVATE/'pgpass.conf')
    with connection() as c:
        guard(c);expected=fingerprints(c)
        assert expected==json.loads((OUT/'CANONICAL_RECEIPT.json').read_bytes())['fingerprints']
        assert not c.execute('SELECT EXISTS(SELECT 1 FROM pg_database WHERE datname=%s)',(RESTORE_DB,)).fetchone()[0]
        target.parent.mkdir(exist_ok=True)
        def run(args,name):
            with (OUT/name).open('wb') as log:code=subprocess.run(args,stdout=log,stderr=subprocess.STDOUT).returncode
            assert code==0,'Native PostgreSQL command failed; preserve log, STOP'
        run([str(binary/'pg_dump.exe'),'-h','127.0.0.1','-p','55451','-U','kami_admin','-d','universal_supplier_kami_rc','-Fc','-f',str(target)],'dump.log')
        run([str(binary/'pg_restore.exe'),'--list','--file',str(OUT/'DUMP_TOC.list'),str(target)],'restore_list.log')
        c.execute(sql.SQL('CREATE DATABASE {} OWNER kami_app TEMPLATE template0').format(sql.Identifier(RESTORE_DB)))
        run([str(binary/'pg_restore.exe'),'-h','127.0.0.1','-p','55451','-U','kami_admin','-d',RESTORE_DB,'--role','kami_app','--no-owner','--no-privileges','--exit-on-error','--single-transaction',str(target)],'restore.log')
    from scripts.rehearse_kami_feature_restore import connection as clone_connection
    with clone_connection(RESTORE_DB) as c,c.transaction():
        c.execute('SET TRANSACTION READ ONLY')
        assert c.execute('SELECT current_database(),inet_server_port(),system_identifier::text FROM pg_control_system()').fetchone()==(RESTORE_DB,55451,MARKER['system_identifier'])
        assert len(verify_migrations(c))==15 and fingerprints(c)==expected
        assert c.execute("SELECT count(*) FROM pg_constraint WHERE contype='f' AND NOT convalidated").fetchone()[0]==0
        winner_gate(c);reproduced=artifacts(c,verify_only=True)
    with connection() as c,c.transaction():
        c.execute('SET TRANSACTION READ ONLY');assert fingerprints(c)==expected
    result={'status':'FINAL_DUMP_RESTORE_VERIFIED','dump':str(target),'size':target.stat().st_size,'sha256':sha(target),
            'pg_restore_list':'PASS','restored_database':RESTORE_DB,'all_tables_sequences_identical':True,'FK_migrations':'PASS',
            'source_unchanged':True,'operations_reproduced':reproduced,'drop_restore_old_evidence':False}
    save(OUT/'DUMP_RESTORE_VERIFIED.json',result);print(json.dumps(result))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=('apply','assemble','dump-restore'));args=parser.parse_args()
    {'apply':apply,'assemble':assemble,'dump-restore':dump_restore}[args.phase]()
