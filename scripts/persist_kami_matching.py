"""Persist verified KAMI-only decisions through the existing lineage API."""
import hashlib,json,os
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import psycopg
from scripts.persist_kami_full_run1 import preflight,runtime_preflight
from scripts.verify_kami_canary import fingerprint
from universal_supplier.postgres import PostgresConfig,PostgresRepository


def main():
    root=Path(__file__).resolve().parents[1];base=root/'reports/KAMI_INTEGRATION_2026-10-05';directory=base/'MATCHING'
    verified=json.loads((directory/'VERIFIED.json').read_bytes())
    assert verified['status']=='REPOSITORY_VERIFIED_KAMI_ONLY_MATCHING_PROPOSALS'
    path=directory/'KAMI_MATCHING_PROPOSALS.json';assert hashlib.sha256(path.read_bytes()).hexdigest()==verified['artifact_hashes'][path.name]
    rows=json.loads(path.read_bytes())['rows'];assert len(rows)==5264 and {r['source'] for r in rows}=={'kami'}
    # Current accepted proposal is all REVIEW. Do not generalize a future
    # canonical-link mutation from this expressly scoped persistence operation.
    assert {r['classification'] for r in rows}=={'REVIEW'}
    private=Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'
    cfg=json.loads((private/'runtime.json').read_bytes());assert (cfg['DB_HOST'],str(cfg['DB_PORT']),cfg['DB_NAME'])==('127.0.0.1','55451','universal_supplier_kami_rc')
    password=(private/'pgpass.conf').read_text().strip().split(':',4)[4]
    marker=json.loads((base/'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    baseline=json.loads((base/'VARIANT_CANARY_VERIFIED.json').read_bytes())['baseline_hashes']
    os.environ.update(cfg)
    with psycopg.connect(host='127.0.0.1',port=55451,dbname=cfg['DB_NAME'],user='kami_admin',password=password,autocommit=True,connect_timeout=5) as admin:
        preflight(admin,marker);assert fingerprint(admin,True)==baseline
        repo=PostgresRepository(PostgresConfig.from_env())
        try:
            runtime_preflight(repo)
            with repo.connection.transaction():
                before=repo.connection.execute("SELECT count(*) FROM product_matches m JOIN source_products p ON p.id=m.source_product_id JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='kami'").fetchone()[0]
                if before:raise RuntimeError('Existing KAMI persisted decisions: STOP, do not revalidate/rewrite blindly')
                applied=Counter()
                for index,row in enumerate(rows,1):
                    preflight(admin,marker)
                    decision={'source_external_id':row['external_id'],'status':'REVIEW','match_method':'kami-frozen-strict-v1',
                              'sterbrust_product_id':None,'confidence_score':0,'auto_accepted':False,
                              'evidence':{'classification':'REVIEW','proposal':row,'verified_inputs':verified['inputs']},
                              'conflicts':{},'warnings':{'blocking_reasons':row['kami_matching_evidence'].get('blocking_reasons',[])}}
                    applied[repo.persist_match(decision,datetime.now(timezone.utc).isoformat(),supplier_code='kami')]+=1
                    if index%1000==0:print(json.dumps({'KAMI_REVIEW_decisions_in_transaction':index}),flush=True)
                assert fingerprint(repo.connection,True)==baseline
                actual=repo.connection.execute("SELECT count(*),count(*) FILTER(WHERE m.is_current),bool_or(m.auto_accepted) FROM product_matches m JOIN source_products p ON p.id=m.source_product_id JOIN suppliers s ON s.id=p.supplier_id WHERE s.code='kami'").fetchone()
                assert actual==(5264,5264,False)
            report={'status':'LIVE_VERIFIED_KAMI_REVIEW_DECISIONS_PERSISTED','applied':dict(applied),'current_REVIEW':5264,
                    'canonical_links_written':0,'activation_changes':0,'old_namespaces_preserved':True,
                    'persistence_API':'existing PostgresRepository.persist_match; lineage rule column stage3b-1.0, method kami-frozen-strict-v1',
                    'HTTP':0,'input_SHA':hashlib.sha256(path.read_bytes()).hexdigest()}
            (directory/'DB_PERSISTED.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)
        finally:repo.close()


if __name__=='__main__':main()
