"""Resume the CLOSED Beka ledger; only saved canonical proposals, no HTTP/SQL."""
from pathlib import Path
import sys, json, hashlib
from collections import Counter
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.beka_canonical_offline import NAMESPACES, canonical_view, SavedCandidateIndex, apply_proposals, blocker_route
from universal_supplier.offline_review_resolution import reason_clusters
from universal_supplier.characteristic_evidence import build_evidence, equipment_scope
from sterbrust_matching.normalization import normalize_brand

BASE = ROOT / 'reports/RC_LOCAL/BEKA_OPERATOR_CLOSED_2026-10-01'
OUT = ROOT / 'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02/ACCEPTED'
PIN = '35d7c90efbdbf407241f915f77f65c65b35161ee1db97e3b28658ba99b63e214'
def load(path): return json.loads(path.read_text(encoding='utf-8'))
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''): h.update(block)
    return h.hexdigest()
def encoded(value): return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True, default=str).encode('utf-8')
def write(path, value): path.write_bytes(encoded(value))
def counts(rows):
    c=Counter(r['classification'] for r in rows)
    ready=sum(r.get('readiness',{}).get('identity_ready',False) for r in rows)
    return {'Existing':c['EXISTING_CONFIRMED'], 'READY':ready, 'Review':len(rows)-c['EXISTING_CONFIRMED']-c['CONFLICT']-ready,'Conflict':c['CONFLICT']}

def main():
    checkpoint=load(BASE/'CHECKPOINT.json')
    original_path=BASE/checkpoint['latest_matching']
    assert sha(original_path)==checkpoint['matching_sha256'], 'Closed checkpoint changed'
    original=load(original_path); rows=original['rows']
    beka=[r for r in rows if r['source'] in NAMESPACES]
    assert len(beka)==187 and counts(beka)==checkpoint['beka']
    assert counts(rows)==checkpoint['commercial']
    brands={normalize_brand(r.get('brand')) for r in beka}
    targets={}; registry_h=hashlib.sha256(); seen=set()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as f:
        for line in f:
            registry_h.update(line); item=json.loads(line); pid=str(item['sterbrust_product_id'])
            assert pid not in seen, 'Duplicate registry ID'
            seen.add(pid)
            if normalize_brand(item.get('brand_raw')) in brands: targets[pid]=canonical_view(item)
    assert len(seen)==59500 and registry_h.hexdigest()==PIN, 'Pinned registry changed'
    index=SavedCandidateIndex(targets)
    # Learning cohort is exactly the already confirmed 62 Beka rows. Never
    # learn from a proposed promotion or the remaining reviews/conflicts.
    pairs=[]
    for row in beka:
        if row['classification']!='EXISTING_CONFIRMED': continue
        target=targets[str(row['sterbrust_product_id'])]
        pairs.append({**row,'equipment_type':equipment_scope(row['name'],row.get('source_category','')),
                      'source_properties':row.get('observed_properties',[]),
                      'sterbrust_properties':target['observed_properties'], 'full_model_confirmed':True})
    assert len(pairs)==62
    # Reuse only prior pairs whose exact confirmed source/canonical decision
    # still exists in this newer checkpoint. REVIEW/NEW are never training.
    current={(r['source'],str(r['external_id'])):r for r in rows if r['classification']=='EXISTING_CONFIRMED'}
    prior_pair_path=ROOT/'reports/RC_LOCAL/OFFLINE_REVIEW_COMPLETE_2026-10-01/UPDATED_CONFIRMED_PAIR_EVIDENCE.json'
    for pair in load(prior_pair_path):
        key=pair['source'],str(pair['external_id'])
        if key not in current or key[0] in NAMESPACES: continue
        if str(pair.get('sterbrust_product_id'))!=str(current[key].get('sterbrust_product_id')): continue
        if pair.get('classification')!='EXISTING_CONFIRMED' or not pair.get('full_model_confirmed'): continue
        pairs.append(pair)
    learned=build_evidence(pairs)
    after,audits,changes=apply_proposals(rows,index)
    replay,_,replay_changes=apply_proposals(after,index)
    assert replay==after and not replay_changes, 'Replay not exact no-op'
    identity_fields=('source','external_id','model','execution','source_url','name','brand','properties','observed_properties','enrichment','evidence_ref')
    for old,new in zip(rows,after):
        for field in identity_fields: assert old.get(field)==new.get(field), 'Source identity changed: '+field
        if old['source'] not in NAMESPACES or old['classification']=='EXISTING_CONFIRMED': assert old==new
    routes=Counter(blocker_route(a) for a in audits)
    after_beka=counts([r for r in after if r['source'] in NAMESPACES])
    output={**original,'rows':after,'canonical_offline_scope':'Beka only; CLOSED source ledger unchanged',
            'summary':{'commercial':counts(after),'beka':after_beka}}
    verified={'evidence_level':'REPOSITORY-VERIFIED / PACKAGED-EVIDENCE; not live SQL',
              'input_matching_sha256':checkpoint['matching_sha256'],'registry_jsonl_sha256':PIN,
              'registry_products':len(seen),'learning_confirmed_pairs':62,
              'learning_total_current_confirmed_pairs':len(pairs),
              'prior_confirmed_pair_file_sha256':sha(prior_pair_path),
              'before_beka':counts(beka),'after_beka':counts([r for r in after if r['source'] in NAMESPACES]),
              'before_commercial':counts(rows),'after_commercial':counts(after),
              'changes':changes,'blocker_routes':dict(routes),'replay_exact_no_op':True,
              'all_source_fields_unchanged':True,'original_existing_and_intervesp_unchanged':True,
              'http_requests':0,'database_operations':0,'final_xml_xlsx_replaced':False,
              'synonym_mappings_observed':len(learned['synonym_mappings']),
              'synonym_mappings_safe':sum(m['verdict']=='SAFE' for m in learned['synonym_mappings']),
              'reason_clusters':reason_clusters([a for a in audits if not a['confirmed']])}
    files={'MATCHING_AFTER.json':output,'CANONICAL_AUDIT.json':audits,'CANONICAL_SAVED_EVIDENCE.json':targets,
           'CONFIRMED_ONLY_CHARACTERISTIC_EVIDENCE.json':learned,
           'FRESH_SNAPSHOT_REQUIRED.json':[a for a in audits if 'EXACT_FULL_MODEL_NOT_IN_PINNED_REGISTRY' in a['blocking_reasons']],
           'VERIFIED.json':verified}
    if OUT.exists():
        assert all((OUT/name).read_bytes()==encoded(value) for name,value in files.items()), 'Preserve differing previous output'
    else:
        OUT.mkdir(parents=True)
        for name,value in files.items(): write(OUT/name,value)
    print(json.dumps(verified,ensure_ascii=True,indent=2))

if __name__=='__main__': main()
