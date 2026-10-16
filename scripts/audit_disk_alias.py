"""Targeted disk alias audit only: frozen inputs; SQL and HTTP prohibited."""
from scripts.verify_inverse97_resume import r,o,B,N,P,AUDIT,history,same
from scripts import qa_ltt_uss_latest as q
from scripts.qa_ltt_uss_final_packets import source_packet
from sterbrust_matching import product_identity as identity
from sterbrust_matching.normalization import normalize_text
from unittest.mock import patch
from dataclasses import asdict
from collections import Counter
import re,inspect,ast

D=r.BASE/'DISK_ALIAS_IMPACT_SWEEP_2026-10-16'
PATTERNS=identity.TAXON_BY_KIND['disk'].aliases
DISK_PRIORITY=next(i for i,t in enumerate(identity.PRODUCT_TAXONOMY) if t.kind=='disk')

def matches(text):
    normalized=normalize_text(text)
    return [dict(pattern=p,text=m.group(),start=m.start(),end=m.end(),context=normalized[max(0,m.start()-90):m.end()+120]) for p in PATTERNS for m in re.finditer(p,normalized,re.I)]

def prepare():
    assert not D.exists(),'Never overwrite a prior audit'
    assert not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists()
    history()
    for name,sha in r.load(N/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((N/name).read_bytes())==sha,('LATEST_EVIDENCE_DRIFT',name)
    baseline=r.load(B/'INVERSE_KIND_REVISED_BASELINE.json');current=r.load(N/'AFTER_ALL_VERIFICATION.json')
    same({k:current[k] for k in ['fingerprints','sequences','supplier_namespaces','product_offer_projections','special_products','sib_units','totals']},baseline)
    assert r.load(N/'RUN2/RESULT.json')['status']=='PASS' and r.load(N/'FINAL_CHECKPOINT.json')['SEMANTIC_REVIEWED']==16
    disk_index=next(i for i,t in enumerate(identity.PRODUCT_TAXONOMY) if t.kind=='disk')
    rules=[]
    for i,p in enumerate(PATTERNS):
      rules.append(dict(pattern=p,alias_priority_zero_based=i,taxon_priority_zero_based=disk_index,mode='re.search(pattern, normalize_text(input), re.I); first taxon, first alias wins',semantics={'\\bдиск\\w*\\b':'Any Unicode word starting диск, including adjective дисковая','\\bкруг\\w*\\b':'Any Unicode word starting круг, including круглый, круглая, круглопильный, круглопалочный','\\bcbn\\b':'Whole token cbn, case-insensitive','\\bsdc\\b':'Whole token sdc, case-insensitive'}[p],adjective_forms_can_match=i<2,host_application_text_can_match=True,category_can_influence=True,corroborating_predicates='No own disk noun, cutting/abrasive function or material predicate required by alias itself. Outer sold-head splitting, complete-machine priority/override, earlier taxon priority and adapter spare/tool overrides may suppress result.',call_sites=['product_identity._first_taxon: ordered alias search','_title_category_kind: own head, sold span, whole name, category fallback','_complete_processing_machine_head: prefix veto via _first_taxon'],own_description='Not searched by disk selection; inventory includes its literal matches as input-only evidence'))
    r.save(D/'DISK_ALIAS_RULES.json',dict(status='REPOSITORY_VERIFIED',aliases=rules,taxon=asdict(identity.TAXON_BY_KIND['disk']),all_product_taxonomy=[asdict(t) for t in identity.PRODUCT_TAXONOMY],additional_taxonomy=[asdict(t) for t in identity.SOLD_OBJECT_TAXONOMY],adapter_only_kinds=['spare','tooling'],heating_element_kind_exists=False,spare_kind_note='Existing adapter source_evidence emits spare/sold_title/HIGH for ролик*,запчаст*,ремень*. It is not registered in TAXON_BY_KIND. Its existing source-ingestion meaning is spare component; do not claim a missing heating subtype exists.',call_chain='r.parse -> regional adapter -> ltt_uss_html.source_evidence -> classify_product_kind -> _title_category_kind -> _first_taxon; non-machine prior may be replaced by _complete_processing_machine_head; source_evidence may replace with spare/tooling',category_fallback='Only after name/head/sold-span searches; MEDIUM confidence',machine_guard='If own head contains станок inflection, preferred machine aliases searched in full title before general taxa; final owned-complete-processing construction can override non-machine prior',description_role='Used by certain sold-object refinements, not by the disk regex searches',implementation_source=inspect.getsource(identity._first_taxon)+inspect.getsource(identity._title_category_kind)+inspect.getsource(identity.classify_product_kind)))
    files=r.load(B/'INVERSE_KIND_97_CODE_SEAL.json')
    r.save(D/'PREFLIGHT.json',dict(status='PASS',current_code=files,latest_evidence_seal_sha256=r.digest((N/'FINAL_ARTIFACT_SEAL.json').read_bytes()),baseline_sha256=r.digest((B/'INVERSE_KIND_REVISED_BASELINE.json').read_bytes()),database_started=False,sql_connections=0,source_writes=0,db_writes=0,full_qa_resumed=False,checked_at=r.now()))
    r.save(D/'GIT_BEFORE.json',q.git());print('Disk alias offline preflight PASS',flush=True)

def inventory():
    assert r.load(D/'PREFLIGHT.json')['status']=='PASS' and not (D/'DISK_ALIAS_CORPUS_HITS.json').exists()
    plan=r.load(B/'EFFECTIVE_ROW_PLAN.json')['rows'];rows=[];candidates=[];category=[];counts=Counter();last=None
    original=identity.classify_product_kind;first=identity._first_taxon;title_kind=identity._title_category_kind
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('SQL_NOT_AUTHORIZED_DISK_OFFLINE_AUDIT')):
      for n,e in enumerate(plan,1):
        last=e;j=r.load(o.ROOT/e['journal']);assert r.digest((o.ROOT/e['journal']).read_bytes())==e['journal_sha256']
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':
          assert o.extract(j)[0]==e['outcome'];category.append(e);continue
        calls=[];scans=[];prior=[]
        def trace_first(text):
          answer=first(text);frame=inspect.currentframe().f_back
          winner=answer[0].kind if answer[0] else None
          winner_pos=next((i for i,t in enumerate(identity.PRODUCT_TAXONOMY) if t.kind==winner),len(identity.PRODUCT_TAXONOMY))
          scans.append(dict(caller=frame.f_code.co_name,line=frame.f_lineno,input=text,winner=winner,matched_alias=answer[1],disk_matches=matches(text),disk_patterns_evaluated=winner_pos>=DISK_PRIORITY))
          return answer
        def trace_prior(name,category=''):
          result=title_kind(name,category);prior.append(asdict(result));return result
        def trace_classifier(name,category='',*,own_description=''):
          result=original(name,category,own_description=own_description)
          calls.append(dict(name=name,category=category,own_description=own_description,result=asdict(result)));return result
        m=j['result'];body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes();assert r.digest(body)==m['evidence_sha256']
        with patch.object(identity,'_first_taxon',trace_first),patch.object(identity,'_title_category_kind',trace_prior),patch.object(identity,'classify_product_kind',trace_classifier):
          product=r.parse(e['supplier'],body,m['final_url'])
        outcome,validated_product,capture=o.extract(j)
        if validated_product is not None:assert r.encoded(asdict(product))==r.encoded(asdict(validated_product))
        assert len(calls)==1 and len(prior)==1
        parsed=asdict(product);saved=r.load(B/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json")
        assert r.encoded(parsed)==r.encoded(saved['parsed_product']) and calls[0]==saved['classifier_inputs']
        kind=parsed['enrichment_evidence']['product_kind'];inputs=calls[0];hits={field:matches(inputs[field]) for field in ['name','category','own_description']}
        item=dict(entry=e,title=product.name,source_category=product.source_category,current_kind=kind,classifier_inputs=inputs,title_category_prior=prior[0],input_matches=hits,executed_taxon_scans=scans,any_configured_alias_match=any(hits.values()),currently_disk=kind['product_kind']=='disk',persisted=outcome=='PERSISTED',outcome=outcome,payload_sha256=r.digest(r.encoded(parsed)),response_sha256=j['result']['response_sha256'],evidence_sha256=j['result']['evidence_sha256'],source_class='PACKAGED-EVIDENCE',semantic_decision=None)
        item['disk_lexical_hit_in_executed_scan']=any(s['disk_matches'] for s in scans)
        item['higher_priority_winner_with_disk_match']=any(s['disk_matches'] and s['winner'] not in {None,'disk'} for s in scans)
        item['disk_selected_then_overridden']=any(p['product_kind']=='disk' for p in prior) and kind['product_kind']!='disk'
        if item['persisted']:
          record_path=N/'FRESH_FULL_QA/PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name;record=r.load(record_path)
          assert r.encoded(parsed)==r.encoded(record['parsed_product'])
          assert kind==record['product']['raw_data']['enrichment_evidence']['product_kind']==record['offer']['raw_data']['enrichment_evidence']['product_kind']
          item['db_evidence']=dict(source_product_id=record['product']['id'],offer_id=record['offer']['id'],record=str(record_path.relative_to(o.ROOT)),record_sha256=r.digest(record_path.read_bytes()),evidence_class='PACKAGED-EVIDENCE_LAST_FRESH_READONLY_SQL_NOT_NEW_SQL')
        rows.append(item);counts['persisted' if item['persisted'] else 'terminal']+=1
        if item['any_configured_alias_match'] or item['currently_disk']:
          packet=source_packet(e);dest=D/'SOURCE_PACKETS'/e['supplier']/f"{e['index']:06d}.json";r.save(dest,packet)
          item.update(source_packet=str(dest.relative_to(D)),source_packet_sha256=r.digest(dest.read_bytes()));candidates.append(item)
        if n%200==0:print('DISK INVENTORY',n,'/2634 candidates',len(candidates),flush=True)
      assert counts=={'persisted':2453,'terminal':180} and len(category)==1 and len(rows)==2633
      r.save(D/'ALL_INPUTS.json',dict(count=2633,rows=rows,category_separate=category))
      r.save(D/'DISK_ALIAS_CORPUS_HITS.json',dict(status='COMPLETE_INVENTORY_NOT_SEMANTIC_APPROVAL',persisted_inputs=2453,terminal_inputs=180,total_candidates=len(candidates),persisted_candidates=sum(a['persisted'] for a in candidates),terminal_candidates=sum(not a['persisted'] for a in candidates),currently_disk=sum(a['currently_disk'] for a in candidates),by_supplier=dict(Counter(a['entry']['supplier'] for a in candidates)),matches_in_unused_own_description_included=True,rows=candidates,category_separate=category,network=network))
    print('Complete disk inventory',len(candidates),'candidates',flush=True)

def show(supplier,start=0,end=10000):
    rows=[a for a in r.load(D/'DISK_ALIAS_CORPUS_HITS.json')['rows'] if a['entry']['supplier']==supplier]
    for a in rows[start:end]:
      p=r.load(D/a['source_packet']);s=p['content_text'];i=s.find('Назначение');excerpt=s[i:i+500] if i>=0 else s[:500]
      print(f"{a['entry']['index']} ID{a['entry']['external_id']} {a['outcome']}|{a['title']}|{a['current_kind']['product_kind']}:{a['current_kind']['matched_alias']}|{excerpt}")

if __name__=='__main__':
    import sys
    mode=sys.argv[1]
    if mode=='prepare':prepare()
    elif mode=='inventory':inventory()
    else:show(mode,int(sys.argv[2]) if len(sys.argv)>2 else 0,int(sys.argv[3]) if len(sys.argv)>3 else 10000)
