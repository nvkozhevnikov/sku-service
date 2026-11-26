"""Explain frozen churn, persisted auto cohort and canonical-ID preservation."""
from collections import Counter,defaultdict
import csv,json,hashlib,sys
from rc_prepare_database import OUT,TARGET,connect
sys.path.insert(0,str(OUT.parents[1]))

def main():
    folder=OUT/'MATCHING_DRY_RUN'
    a=json.loads((folder/'before.json').read_text(encoding='utf-8'));b=json.loads((folder/'after.json').read_text(encoding='utf-8'))
    key=lambda r:(r['supplier_code'],r['external_id'])
    before={key(r):r for r in a['rows'] if r['pass_mode']=='with_existing_links'}
    after={key(r):r for r in b['rows'] if r['pass_mode']=='with_existing_links'}
    from universal_supplier.effective_identity import accepted_current_mapping_sql
    with connect(TARGET) as c:
        c.execute('SET default_transaction_read_only=on')
        sql=f"SELECT s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id WHERE sp.active AND EXISTS(SELECT 1 FROM product_matches pm WHERE {accepted_current_mapping_sql('sp.id','sp.catalog_product_id','pm')}) ORDER BY s.code,sp.external_id"
        current=c.execute(sql).fetchall()
        projection_sha=hashlib.sha256(json.dumps(current,default=str,separators=(',',':')).encode()).hexdigest()
    risk=[];reasons=Counter();by_source=Counter();promotions=[]
    for code,external,catalog,canonical in current:
        k=(code,external)
        if after[k]['auto_accepted']!='TRUE':
            risk.append({'supplier':code,'external_id':external,'persisted_sterbrust_id':canonical,'before_auto':before[k]['auto_accepted'],'after_status':after[k]['status'],'after_method':after[k]['match_method'],'new_candidate_id':after[k]['sterbrust_product_id'],'reasons':after[k]['conflicts']})
    for k,left in before.items():
        right=after[k]
        if left['auto_accepted']=='TRUE' and right['auto_accepted']!='TRUE':
            conflicts=json.loads(right['conflicts']);reasons.update(conflicts.keys() or [right['match_method']]);by_source[k[0]]+=1
        if left['auto_accepted']!='TRUE' and right['auto_accepted']=='TRUE':
            promotions.append({'supplier':k[0],'external_id':k[1],'name':right['name'],'model':right['model'],'old_method':left['match_method'],'new_method':right['match_method'],'new_id':right['sterbrust_product_id'],'old_conflicts':left['conflicts'],'new_conflicts':right['conflicts']})
    for name,rows in [('PERSISTED_AUTO_REVIEW_QUEUE.csv',risk),('AUTO_PROMOTIONS_NOT_APPLIED.csv',promotions)]:
        with (folder/name).open('w',encoding='utf-8-sig',newline='') as f:
            w=csv.DictWriter(f,fieldnames=list(rows[0]) if rows else ['supplier','external_id']);w.writeheader();w.writerows(rows)
    summary={'persisted_current_auto':len(current),'after_not_auto':len(risk),'new_patch_auto_to_not_auto':sum(r['before_auto']=='TRUE' for r in risk),'already_not_auto_before_patch':sum(r['before_auto']!='TRUE' for r in risk),'all_with_links_auto_downgrades_by_source':dict(by_source),'all_with_links_downgrade_conflict_keys':dict(reasons),'promotions_with_links':len(promotions),'promotion_methods':dict(Counter(r['old_method']+' -> '+r['new_method'] for r in promotions)),'physical_catalog_and_sterbrust_id_projection_sha256':projection_sha,'canonical_ids_written':0,'manual_decisions_in_snapshot':a['manual_decisions'],'manual_proof_scope':'No real manual rows in this copied snapshot. Synthetic manual-preservation tests passed; not a replay of real manual decisions.','release_gate':'NOT_VERIFIED_PENDING_LINUX_AND_CHURN_REVIEW'}
    (folder/'PERSISTED_COHORT_SUMMARY.json').write_text(json.dumps(summary,indent=2),encoding='utf-8');print(json.dumps(summary,indent=2))
if __name__=='__main__':main()
