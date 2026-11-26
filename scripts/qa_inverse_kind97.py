"""New read-only QA after inverse97; never resumes prior semantic samples."""
from scripts.execute_inverse_kind97 import B,r,o,q,P,old,correction,historical
from collections import Counter
from scripts.qa_ltt_uss_final_packets import source_packet
from sterbrust_matching.product_identity import TAXON_BY_KIND
F=B/'FRESH_FULL_QA'

def history():
    correction.validate_gate();historical()
    for path,sha in r.load(B/'INVERSE_KIND_97_CODE_SEAL.json').items():assert r.digest((o.ROOT/path).read_bytes())==sha,('CODE_DRIFT',path)

q.OUT=F;q.PLAN=B/'EFFECTIVE_ROW_PLAN.json';q.BASELINE=F/'BASELINE_WITH_COUNTERS.json';q.history=history

def preflight():
    assert not F.exists();history();run=r.load(B/'RUN2/RESULT.json');assert run['status']=='PASS' and run['scope']==2634 and run['exact_noop_persisted']==2453
    base=r.load(B/'INVERSE_KIND_REVISED_BASELINE.json');r.save(q.BASELINE,dict(base,sources=run['sources']))
    r.save(F/'OFFLINE_PREFLIGHT.json',dict(status='PASS',fresh_from_zero=True,prior_passes_used=False,new_baseline_sha256=r.digest((B/'INVERSE_KIND_REVISED_BASELINE.json').read_bytes()),current_code_seal_sha256=r.digest((B/'INVERSE_KIND_97_CODE_SEAL.json').read_bytes()),checked_at=r.now()))
    r.save(F/'TEST_RECEIPT.json',dict(r.load(B/'TEST_RECEIPT.json'),test_execution='Current final code pre-DB gate',xml_sha256=r.digest((B/'FINAL_TESTS.xml').read_bytes())))
    r.save(F/'GIT_BEFORE.json',q.git());print('Fresh final inverse97 QA preflight PASS',flush=True)

def packets():
    assert r.load(F/'STRUCTURAL_AUDIT.json')['rows_checked']==2634
    assert not (F/'FRESH_400_SAMPLE_MANIFEST.json').exists()
    mandatory={(a['supplier'],a['index']) for path in [B/'INVERSE_KIND_97_MANIFEST.json',P/'PRODUCT_KIND_29_MANIFEST.json'] for a in r.load(path)['rows']}
    mandatory|={('uss_stanko',i) for i in q.FIXED}|{('ltt_msk',i) for i in [64,358,359]}|{('ltt_sib',i) for i in [941,958,959,1085]}|{('ltt_yug',386),('uss_stanko',350)}
    units={a[0] for a in r.load(B/'INVERSE_KIND_REVISED_BASELINE.json')['sib_units']};pop={s:[] for s in r.SOURCES}
    seed='FRESH_FINAL_INVERSE97_QA_V1\0'+r.digest((B/'INVERSE_KIND_REVISED_BASELINE.json').read_bytes())
    for e in r.load(q.PLAN)['rows']:
        if e['outcome']!='PERSISTED':continue
        path=F/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name;db=r.load(path);p=db['parsed_product'];kind=p['enrichment_evidence']['product_kind']
        assert kind==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
        tags=[str(p[k]) for k in ['source_category','source_brand','availability']]+[p['price']['state'],p['price']['currency'],kind['product_kind']]
        must=(e['supplier'],e['index']) in mandatory or p['price']['currency']=='USD' or (e['supplier']=='ltt_sib' and e['external_id'] in units) or (e['supplier']=='ltt_yug' and e['external_id'] in {'312','297'})
        pop[e['supplier']].append(dict(e,persisted_record=str(path.relative_to(F)),mandatory=must,tags=tags,rank=r.digest((seed+'\0'+e['supplier']+'\0'+e['external_id']).encode())))
    selected=[]
    for supplier,population in pop.items():
        chosen=[e for e in population if e['mandatory']];rest=[e for e in population if not e['mandatory']];freq=Counter(t for e in population for t in e['tags']);covered={t for e in chosen for t in e['tags']}
        while len(chosen)<100:
            best=min(rest,key=lambda e:(-sum(1/freq[t] for t in e['tags'] if t not in covered),e['rank']));chosen.append(best);rest.remove(best);covered.update(best['tags'])
        selected.extend(sorted(chosen,key=lambda e:e['rank']))
    assert len(selected)==400
    r.save(F/'FRESH_400_SAMPLE_MANIFEST.json',dict(total=400,counts=dict(Counter(e['supplier'] for e in selected)),seed=seed,rows=selected,prior_semantic_pass_used=False,status='SELECTED_NOT_REVIEWED'))
    with q.guard.guard() as network:
        for e in selected:r.save(F/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name,source_packet(e))
        r.save(F/'PACKET_HTTP_ZERO_PROOF.json',network)
    print('New400 sample/packets ready,100/source; no semantic approvals',flush=True)

def show(supplier,start=0,end=100):
    from scripts import qa_ltt_uss_latest_semantic_facts as facts
    facts.OUT=F
    entries=[e for e in r.load(F/'FRESH_400_SAMPLE_MANIFEST.json')['rows'] if e['supplier']==supplier]
    for e in entries[start:end]:
        f=facts.read(e);packet=r.load(F/f['packet']);f['own_source_text']=packet['content_text'];f['semantic_decision']=None
        r.save(F/'SEMANTIC_FACTS'/supplier/f"{e['index']:06d}.json",f)
        print(f"{e['index']} ID{e['external_id']}|{f['title']}|{f['kind']['product_kind']}|{f['source_price']['raw']} ->{f['parsed_price']['current_price']} {f['parsed_price']['currency']}|{f['source_stocks']} ->{f['parsed_availability']}|issues={f['issues']}")

if __name__=='__main__':
    import sys
    mode=sys.argv[1]
    if mode=='preflight':preflight()
    elif mode=='structural':q.structural()
    elif mode=='packets':packets()
    else:show(mode,int(sys.argv[2]) if len(sys.argv)>2 else 0,int(sys.argv[3]) if len(sys.argv)>3 else 100)
