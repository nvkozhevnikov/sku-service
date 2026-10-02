"""Verify exactly eight saved URLs; no HTTP, SQL writes or global matching."""
from pathlib import Path
import sys, json, hashlib, subprocess
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from universal_supplier.matching_closure import close_saved_rows
PRE=ROOT/'reports/RC_LOCAL/FRESH_REFRESH_INTEGRATION_2026-10-02'
OUT=ROOT/'reports/RC_LOCAL/OPTIMUM_COMPLETE_CLOSURE_2026-10-02'
GROK=ROOT.parent/'sku-service-fresh-catalog-refresh-rc1'
COMMIT='ba2b3f2cf01520febfe8e328312b972f9661569b'
PIN='a54c139e728e60aff2e7ae2e44a81c66cb9fa70b'


def load(p):return json.loads(p.read_text(encoding='utf-8'))
def sha(body):return hashlib.sha256(body).hexdigest()
def git_bytes(commit,path):return subprocess.check_output(['git','-C',str(GROK),'show',commit+':'+path])
def save(name,v):
    p=OUT/name;body=json.dumps(v,ensure_ascii=False,indent=2).encode('utf-8')
    if p.exists():assert p.read_bytes()==body
    else:p.write_bytes(body)


def main():
    assert not (OUT/'CHECKPOINT.json').exists(), 'Already accepted; resume checkpoint'
    prior=load(PRE/'ACCEPTED_MATCHING_PROPOSALS.json')
    checks=load(PRE/'ARTIFACT_SHA256.json')
    assert all(sha((PRE/p).read_bytes())==s for p,s in checks.items())
    assert len(prior['rows'])==4407
    base='catalog_refresh/evidence/optimum/'
    verified={}
    paths=[base+p for p in ('CATALOG.jsonl','DISCOVERY.json','GAP_REVIEW.json','ERRORS.json','STALE_SITEMAP.json','gap_pages/fetch.json','gap_pages/redirects.json')]
    paths+=['catalog_refresh/evidence/CODEX_FRESH_CATALOG_REFRESH.json','catalog_refresh/optimum_gap.py','catalog_refresh/parse_listings.py','catalog_refresh/identity.py']
    for p in paths:
        body=git_bytes(COMMIT,p);assert body==(GROK/p).read_bytes()
        verified[p]=sha(body)
    for p in ('CATALOG.jsonl','ERRORS.json','FULL_MANIFEST.txt'):
        assert git_bytes(PIN,base+p)==git_bytes(COMMIT,base+p)
    catalog=[json.loads(line) for line in git_bytes(COMMIT,base+'CATALOG.jsonl').splitlines() if line.strip()]
    identities={str(r['source_identity']) for r in catalog};assert len(catalog)==len(identities)==1353
    byid={str(r['source_identity']):r for r in catalog}
    olddiscovery=json.loads(git_bytes(PIN,base+'DISCOVERY.json'))
    discovery=load(GROK/base/'DISCOVERY.json');ledger=load(GROK/base/'GAP_REVIEW.json')
    fetched=load(GROK/base/'gap_pages/fetch.json');redirects=load(GROK/base/'gap_pages/redirects.json')
    expected=set(olddiscovery['sitemap_only_product_like'])
    assert len(expected)==8==len(fetched)==len(redirects)==len(ledger['pages'])
    assert {r['url'] for r in fetched}=={r['url'] for r in redirects}=={r['requested_url'] for r in ledger['pages']}==expected
    assert discovery['verdict']=='COMPLETE' and discovery['unresolved_product_urls']==0
    assert discovery['catalog_rows_added_by_gap_review']==0 and ledger['catalog_rows_added']==0
    sys.path.insert(0,str(GROK))
    from catalog_refresh.optimum_gap import review_page
    replay=[];rawhashes={}
    redirect={r['url']:r for r in redirects};catalogurls={r['product_url'] for r in catalog}
    for capture in fetched:
        spec=redirect[capture['url']];page=GROK/base/'gap_pages'/capture['file'];body=page.read_bytes()
        assert len(body)==capture['bytes'] and capture['status']==200
        assert spec['location']==capture['final_url'] and spec['redirect_status'] in (301,302)
        rawhashes[page.relative_to(GROK).as_posix()]=sha(body)
        replay.append(review_page(body.decode('utf-8'),capture['url'],spec['redirect_status'],
            capture['final_url'],identities,catalogurls,tuple(spec.get('model_tokens') or [])))
    assert replay==ledger['pages'], 'Saved HTML does not reproduce ledger'
    stale=[r for r in replay if r['verdict']=='STALE/404'];alias=[r for r in replay if r['alias_of']]
    assert len(stale)==7 and len(alias)==1
    assert all(r['http_status'] in (301,302) and r['final_http_status']==200 and not r['visible_article'] for r in stale)
    a=alias[0];target=byid[a['alias_of']['source_identity']]
    assert a['alias_of']['source_identity']=='5390'
    assert target['visible_article']==a['visible_article']=='3321160'
    assert target['product_url']==a['final_url'] and target['visible_name']==a['visible_h1']
    assert 'GBS 150' in a['visible_h1'] and 'optigrind_gb_s150/' in a['requested_url']
    assert all(not r['added_to_catalog'] for r in replay)
    current={str(r['external_id']) for r in prior['rows'] if r['source']=='optimum'}
    assert current==identities, 'Accepted RC proposals do not equal confirmed supplier identity set'
    OUT.mkdir(exist_ok=True)
    save('OPTIMUM_COMPLETE_VERIFIED.json',{'commit':COMMIT,'evidence_level':'REPOSITORY-VERIFIED',
        'unique_products':1353,'new_source_identities':0,'unresolved_product_urls':0,
        'stale_redirect_to_section':7,'redirect_alias_existing':a,'pages':replay,
        'verified_commit_hashes':verified,'saved_html_hashes':rawhashes,'http_requests_this_scope':0,
        'sql_ingestion_repeated':False,'global_matching_repeated':False,
        'stale_label_note':'Seven are category redirects, not fresh HTTP404; neither proves canonical absence'})
    accepted=deepcopy(prior)
    for r in accepted['rows']:
        if r['source']=='optimum':r['provisional']=False
    accepted['summary']['optimum_provisional']=False
    accepted['summary']['optimum_discovery']='COMPLETE'
    accepted['optimum_discovery_provenance']={'commit':COMMIT,'file':'OPTIMUM_COMPLETE_VERIFIED.json'}
    for before,after in zip(prior['rows'],accepted['rows']):
        expectedrow=deepcopy(before)
        if before['source']=='optimum':expectedrow['provisional']=False
        assert after==expectedrow
    save('MATCHING_ACCEPTED.json',accepted)
    closure=close_saved_rows(accepted['rows'])
    assert closure==close_saved_rows(accepted['rows'])
    save('MATCHING_CLOSURE.json',closure)
    save('SUMMARY.json',{**accepted['summary'],'closure_routes':closure['evidence_routes'],
        'closure_terminal_counts':closure['terminal_counts'],'new_confirmations':0,'decision_replay_exact_noop':True,
        'unchanged_existing':454,'unchanged_conflict':259,'new_groups':0,'ready_to_create_identity':0})
    print(json.dumps({'accepted':True,'optimum':'COMPLETE','products':1353,
        'closure_terminal_counts':closure['terminal_counts'],'closure_routes':closure['evidence_routes']}))


if __name__=='__main__':main()
