"""Verify Grok's pinned refresh without HTTP, SQL, merges or publication."""
from pathlib import Path
import sys,json,hashlib,subprocess
from collections import Counter
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.catalog_refresh_evidence import commercial_evidence,compact_identity,identity_key
from universal_supplier.beka_canonical_offline import canonical_view,SavedCandidateIndex,evaluate_row
COMMIT='a54c139e728e60aff2e7ae2e44a81c66cb9fa70b'
GROK=ROOT.parent/'sku-service-fresh-catalog-refresh-rc1'
OUT=ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02'
PREV=ROOT/'reports/RC_LOCAL/BEKA_CANONICAL_OFFLINE_2026-10-02'
def load(p): return json.loads(p.read_text(encoding='utf-8'))
def sha(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def git(*args): return subprocess.check_output(['git','-C',str(ROOT),*args])
def save(name,value):
    body=json.dumps(value,ensure_ascii=False,indent=2,default=str).encode('utf-8')
    p=OUT/name
    if p.exists():assert p.read_bytes()==body,'Preserve existing different artifact'
    else:p.write_bytes(body)
def main():
    check=load(PREV/'CHECKPOINT.json')
    assert sha(PREV/check['latest_matching'])==check['matching_sha256']
    manifest_path='catalog_refresh/evidence/CODEX_FRESH_CATALOG_REFRESH.json'
    paths=[manifest_path,'catalog_refresh/sterbrust_manifest.py','catalog_refresh/parse_listings.py',
           'catalog_refresh/evidence/sterbrust/STERBRUST_REGISTRY_IDENTITY.jsonl']
    for supplier in ('partner_st','optimum'):
        paths += [f'catalog_refresh/evidence/{supplier}/{name}' for name in
                  ('CATALOG.jsonl','FULL_MANIFEST.txt','DELTA.json','CAPTURE_INDEX.jsonl')]
    provenance={}
    for path in paths:
        committed=git('show',COMMIT+':'+path)
        assert (GROK/path).read_bytes()==committed,'Worktree evidence differs from pinned commit: '+path
        provenance[path]=hashlib.sha256(committed).hexdigest()
    manifest=load(GROK/manifest_path)
    fresh_path=GROK/'catalog_refresh/evidence/sterbrust/STERBRUST_REGISTRY_IDENTITY.jsonl'
    assert sha(fresh_path)==manifest['sterbrust']['identity_manifest_sha256']
    fresh={}
    with fresh_path.open(encoding='utf-8') as f:
        for line in f:
            r=json.loads(line);pid=r['sterbrust_product_id'];assert pid not in fresh;fresh[pid]=r
    assert len(fresh)==59500
    old_ids=set();changed=[];old_hash=hashlib.sha256()
    with (ROOT/'reports/STERBRUST_REGISTRY.jsonl').open('rb') as f:
        for line in f:
            old_hash.update(line);r=compact_identity(json.loads(line));pid=r['sterbrust_product_id']
            assert pid not in old_ids;old_ids.add(pid)
            if pid in fresh and identity_key(r)!=identity_key(fresh[pid]):changed.append(pid)
    assert old_hash.hexdigest()==manifest['sterbrust']['pinned_registry_sha256']
    assert old_ids==set(fresh) and not changed,'Fresh identity delta is not zero'
    current={};supplier_summaries={}
    for supplier,expected,por in (('partner_st',1225,0),('optimum',1353,704)):
        info=manifest[supplier];base=GROK/f'catalog_refresh/evidence/{supplier}'
        assert sha(base/'CATALOG.jsonl')==info['catalog_sha256']
        assert sha(base/'FULL_MANIFEST.txt')==info['manifest_sha256']
        assert sha(base/'CAPTURE_INDEX.jsonl')==info['captures']['index_sha256']
        delta=load(base/'DELTA.json');source_ids=set();normalized=[];price_conflicts=0
        for line in (base/'CATALOG.jsonl').read_text(encoding='utf-8').splitlines():
            r=json.loads(line);assert r['supplier_code']==supplier and r['source_identity'] not in source_ids
            source_ids.add(r['source_identity'])
            metadata_only=supplier=='optimum' and r.get('identity_conflict') is True and (
                'hidden_price' in r.get('alias_evidence',{}))
            # The pinned parser sets Optimum conflict ONLY on hidden-price
            # disagreement; its committed implementation is part of provenance.
            if metadata_only:price_conflicts+=1
            normalized.append(commercial_evidence(r,hidden_price_only=metadata_only))
        assert len(normalized)==expected
        states=Counter(r['price_state'] for r in normalized)
        assert states['price_on_request']==por
        assert not any(r['normalized_price'] is not None for r in normalized if r['price_state']!='numeric')
        assert not any(r['identity_conflict'] for r in normalized),'Actual identity conflict remains'
        if supplier=='optimum':assert price_conflicts==42 and info['identity_conflict_article']==0
        assert len(delta['changed'])==info['changed_count'] and len(delta['added'])==info['added_count']
        assert source_ids.issuperset(delta['added']) and not delta['missing_or_stale']
        current[supplier]=normalized
        supplier_summaries[supplier]={'verified_current_rows':expected,'provisional':supplier=='optimum',
           'new_source_identities':delta['added'],'reported_current_changes':len(delta['changed']),
           'price_states':dict(states),'hidden_price_only_cases':price_conflicts,
           'applied_to_rc':False,'postgres_replay_verified':False}
    # Fresh full raw stays in Grok's ignored reports; it is not copied/merged.
    full_path=GROK/manifest['sterbrust']['full_registry_jsonl_path']
    h=hashlib.sha256();targets={};full_ids=set()
    with full_path.open('rb') as f:
        for line in f:
            h.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id']);assert pid not in full_ids
            full_ids.add(pid)
            assert compact_identity(r)==fresh[pid],'Full/compact fresh evidence differs'
            if r.get('brand_raw') in ('Beka-Mak','BEKA-MAK','Beka Mak','BEKAMAK'): targets[pid]=canonical_view(r)
    assert full_ids==set(fresh) and h.hexdigest()==manifest['sterbrust']['full_registry_jsonl_sha256']
    index=SavedCandidateIndex(targets)
    previous=load(PREV/check['latest_matching'])
    byid={(r['source'],str(r['external_id'])):r for r in previous['rows']}
    audits=load(PREV/'ACCEPTED/CANONICAL_AUDIT.json')
    routed=[a for a in audits if a['after']!='EXISTING_CONFIRMED' and
            'EXACT_FULL_MODEL_NOT_IN_PINNED_REGISTRY' in a['blocking_reasons'] and
            'PRIOR_REVIEW_REQUIRES_OPERATOR' not in a['blocking_reasons']]
    assert len(routed)==24
    reevaluated=[]
    for audit in routed:
        result=evaluate_row(byid[(audit['source'],audit['external_id'])],index)
        result['next_route']='canonical_side_evidence'
        if 'PRIOR_REVIEW_REQUIRES_OPERATOR' in result['blocking_reasons']:result['next_route']='retained_hold'
        result['fresh_snapshot_checked']=True
        reevaluated.append(result)
    assert not any(r['retrieval']['exact_full_model_ids'] for r in reevaluated)
    hygiene=git('ls-tree','-rl',COMMIT,'catalog_refresh/evidence/sterbrust/STERBRUST_REGISTRY_IDENTITY.jsonl').decode().strip()
    summary={'commit':COMMIT,'manifest_sha256':provenance[manifest_path],'provenance_sha256':provenance,
        'sterbrust_unique_ids':59500,'identity_added':0,'identity_missing':0,'identity_changed':0,
        'fresh_raw_sha256':h.hexdigest(),'fresh_raw_external_path':str(full_path),
        'beka_fresh_registry_routed_checked':24,'beka_new_exact_candidates':0,'beka_confirmations_from_refresh':0,
        'beka_next_route_counts':dict(Counter(r['next_route'] for r in reevaluated)),
        'suppliers':supplier_summaries,'rc_preflight':'DB_UNAVAILABLE_connection_timeout',
        'rc_current_live_source_counts':None,'rc_db_writes':0,'new_supplier_http_gets':0,
        'consolidated_matching_pass_executed':False,'retained_80_reassessment_executed':False,
        'cross_source_new_grouping_executed':False,'final_exports_published':False,
        'release_finalization_blocked_by_optimum_urls':8,'identity_git_blob':hygiene,
        'heavy_blob_needed_in_release':False,'hygiene_action':'external immutable evidence + commit/SHA; no merge/copy/delete/rewrite'}
    OUT.mkdir(exist_ok=True,parents=True)
    save('EVIDENCE_VERIFIED.json',summary);save('BEKA_FRESH_ROUTED_AUDIT.json',reevaluated)
    save('CURRENT_COMMERCIAL_EVIDENCE.json',current)
    print(json.dumps({k:v for k,v in summary.items() if k!='provenance_sha256'},ensure_ascii=True,indent=2))
if __name__=='__main__':main()
