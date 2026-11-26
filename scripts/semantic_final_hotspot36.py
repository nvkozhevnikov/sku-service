"""Fresh own-source facts and explicit human-in-the-loop review ledger."""
from scripts.final_hotspot36 import S,T,H,r,o
from scripts import qa_ltt_uss_latest_semantic_facts as facts
F=S/'FRESH_FULL_QA'

def entries(source):return [e for e in r.load(F/'FRESH_400_SAMPLE_MANIFEST.json')['rows'] if e['supplier']==source]

def show(source,start,end):
    facts.OUT=F
    for e in entries(source)[start:end]:
        f=facts.read(e);packet=r.load(F/f['packet']);text=packet['content_text'];pos=text.find('Назначение')
        if pos<0:pos=max(0,text.find(f['parsed_title']))
        f.update(own_source_text=text,own_purpose_excerpt=text[pos:pos+1600],source_category=r.load(F/e['persisted_record'])['parsed_product']['source_category'],semantic_decision=None,prior_passes_used=False)
        r.save(F/'SEMANTIC_FACTS'/source/f"{e['index']:06d}.json",f)
        print(f"{e['index']} ID{e['external_id']}|{f['title']}|{f['kind']['product_kind']}/{f['kind']['confidence']}|{f['source_price']['raw']} =>{f['parsed_price']['current_price']} {f['parsed_price']['currency']}|{f['source_stocks']} =>{f['parsed_availability']}|{f['own_purpose_excerpt'][:210]}|issues={f['issues']}")

def accept(source,start,end,reason):
    p=F/'SEMANTIC_REVIEW_PROGRESS.json';review=r.load(p) if p.exists() else dict(status='IN_PROGRESS',total=400,rows=[],prior_passes_used=False)
    seen={(a['supplier'],a['index']) for a in review['rows']}
    for e in entries(source)[start:end]:
        key=e['supplier'],e['index'];assert key not in seen,'Do not double-review/overwrite'
        path=F/'SEMANTIC_FACTS'/source/f"{e['index']:06d}.json";f=r.load(path);assert not f['issues'],'Resolve own-source discrepancy before acceptance'
        f.update(semantic_decision='PASS',semantic_reason=reason,evidence_classes=dict(source='PACKAGED-EVIDENCE',database='LIVE-VERIFIED_CURRENT_QA',interpretation='INFERENCE'),prior_passes_used=False)
        r.save(path,f);review['rows'].append(dict(supplier=source,index=e['index'],external_id=e['external_id'],decision='PASS',reason=reason,path=str(path.relative_to(F)),sha256=r.digest(path.read_bytes())))
    review.update(reviewed=len(review['rows']),passed=sum(a['decision']=='PASS' for a in review['rows']),failed=sum(a['decision']=='FAIL' for a in review['rows']),unreviewed=400-len(review['rows']))
    review['by_source']={s:sum(a['supplier']==s for a in review['rows']) for s in r.SOURCES};r.save(p,review);print('FRESH SEMANTIC',review['reviewed'],'/400',review['by_source'],flush=True)

if __name__=='__main__':
    import sys
    show(sys.argv[1],int(sys.argv[2]),int(sys.argv[3]))
