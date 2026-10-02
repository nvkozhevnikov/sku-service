"""Finish only the 80-hold audit; reuse accepted global pass, never rerun it."""
from pathlib import Path
import sys,json,hashlib
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.apply_fresh_refresh_rc import OUT,load
from scripts.match_fresh_refresh_consolidated import reassess_historical_hold,summary
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex
from sterbrust_matching.normalization import normalize_brand
FINAL=OUT/'FINAL_HOLD_RECONCILED'
def save(name,v):
    body=json.dumps(v,ensure_ascii=False,indent=2,default=str).encode()
    p=FINAL/name
    if p.exists():assert p.read_bytes()==body
    else:p.write_bytes(body)
def main():
    if FINAL.exists():raise RuntimeError('Final hold reconciliation already saved; resume checkpoint')
    predecessor=load(ROOT/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED/MATCHING_AFTER.json')
    original=[r for r in predecessor['rows'] if r['source'] in ('beka_mak','beka_mak_tr') and
              r.get('prior_review_retained') and r['classification']!='EXISTING_CONFIRMED']
    assert len(original)==80
    proof=load(OUT/'EVIDENCE_VERIFIED.json');h=hashlib.sha256();targets={}
    with Path(proof['fresh_raw_external_path']).open('rb') as f:
        for line in f:
            h.update(line);r=json.loads(line)
            if normalize_brand(r.get('brand_raw'))=='beka-mak':targets[str(r['sterbrust_product_id'])]=canonical_view(r)
    assert h.hexdigest()==proof['fresh_raw_sha256']
    index=SavedCandidateIndex(targets);audits=[reassess_historical_hold(r,index) for r in original]
    matching=load(OUT/'CONSOLIDATED_MATCHING.json');rows=matching['rows']
    byid={(r['source'],str(r['external_id'])):r for r in rows};changes=[]
    for audit in audits:
        key=audit['source'],audit['external_id'];r=byid[key]
        if audit['confirmed']:
            chosen=audit['confirmed'];before=r['classification']
            r.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=chosen['sterbrust_product_id'],
              sterbrust_name=chosen['name'],proposed_sterbrust_id='',full_model_confirmed=True,
              prior_review_retained=False,historical_hold_resolution=audit)
            changes.append({'source':key[0],'external_id':key[1],'before':before,'after':r['classification']})
    result=dict(matching['summary']);result.update(overall=summary(rows),
       by_source={s:summary([r for r in rows if r['source']==s]) for s in result['source_counts']},
       retained_80_reassessment=dict(Counter(a['hold_verdict'] for a in audits)),
       historical_hold_confirmations=len(changes),
       actual_human_required_proven=0,
       actual_human_note='Workflow/manual flags alone are not factual evidence; unresolved canonical evidence remains mandatory')
    # Prior Existing and source identities are immutable in this scope.
    for r in predecessor['rows']:
        current=byid[(r['source'],str(r['external_id']))]
        if r['classification']=='EXISTING_CONFIRMED':assert current==r
        for field in ('model','execution','name','source_url','external_id','properties','observed_properties'):
            assert current.get(field)==r.get(field)
    FINAL.mkdir()
    save('MATCHING_PROPOSALS.json',{**matching,'rows':rows,'summary':result})
    save('BEKA_RETAINED_HOLDS_AUDIT.json',audits);save('SUMMARY.json',result)
    print(json.dumps(result,ensure_ascii=True,indent=2))
if __name__=='__main__':main()
