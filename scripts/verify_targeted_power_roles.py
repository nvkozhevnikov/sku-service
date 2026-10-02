"""Recheck only the one proposed promotion after a stricter power-role boundary."""
from pathlib import Path
import sys,json
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.integrate_targeted_nine import PRE,OUT,load,save
from universal_supplier.offline_review_resolution import cnc_features
from universal_supplier.priority_evidence_verification import verify_candidate,visible_page
from universal_supplier.beka_canonical_offline import canonical_view
def main():
    mappings=load(OUT/'CONFIRMED_ONLY_TYPED_MAPPINGS.json');valid=[];rejected=[]
    for m in mappings:
        dummy=[{'name':m['source_property'],'value':'1'}]
        (valid if cnc_features(dummy,m['scope']) else rejected).append(m)
    assert any('S6' in m['source_property'] for m in rejected)
    save('ROLE_SAFE_TYPED_MAPPINGS.json',valid)
    save('MAPPING_ROLE_AUDIT.json',{'rejected':[{'source_property':m['source_property'],'canonical_property':m['canonical_property'],
        'reason':'S6 duty label is not generic main-spindle power or Cont./Max'} for m in rejected],
        'training_scope':'accepted Existing only;35 exact X-travel examples',
        'X_example_count':sum(len(m['examples']) for m in valid if m['key']=='axis_x_travel' and m['verdict']=='SAFE')})
    promotion=load(OUT/'PROMOTIONS.json')[0];row=next(r for r in load(PRE/'MATCHING_ACCEPTED.json')['rows'] if r['external_id']==promotion['external_id'] and r['source']==promotion['source'])
    raw=load(PRE/'RAW_PRIORITY_CANONICAL.json')['147497'];target=canonical_view(raw)
    target['product_url']=raw['product_url']
    target['equivalent_identity_ids']=['147497']  # already globally verified, not a new retrieval choice
    proof=verify_candidate(row,target,visible_page(Path(promotion['evidence_ref']).read_bytes()),valid,promotion['evidence_ref'])
    assert proof==promotion['proof'] and proof['confirmed']
    assert {p['key'] for p in proof['evidence']['typed_characteristics']['agreements']}=={'max_processing_length','axis_x_travel'}
    save('POWER_ROLES_FINAL_VERIFIED.json',{'one_promotion_identical_after_stricter_role_guard':True,
        'used_anchors':['max_processing_length','axis_x_travel'],'power_used_for_acceptance':False,
        'manufacturer105_copied_to_both_sides':False,'S6_equals_Cont_Max':False})
    print('PASS: S6 mapping rejected;147497 unchanged strict proof using two geometry anchors only')
if __name__=='__main__':main()
