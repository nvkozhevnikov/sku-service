"""Authorized offline hotspot inventory; no source repair, SQL or HTTP."""
from scripts.disk_alias_batch import *
from scripts.execute_disk_alias10 import history
from scripts import disk_alias_metadata_correction as correction
from scripts.qa_ltt_uss_final_packets import source_packet
from sterbrust_matching import product_identity as identity
from sterbrust_matching.normalization import normalize_text
from unittest.mock import patch
from dataclasses import asdict
from collections import Counter
import inspect,re

H=r.BASE/'PRE_FINAL_HOTSPOT_SWEEP_2026-10-16'
F=S/'FRESH_FULL_QA'

def prepare():
    assert not H.exists();assert not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists()
    history();correction.validate_gate()
    seal=r.load(S/'FINAL_ARTIFACT_SEAL.json')
    # Validate every consumed accepted-phase file against its final seal.
    names=['FINAL_CHECKPOINT.json','EFFECTIVE_ROW_PLAN.json','DISK_ALIAS_REVISED_BASELINE.json','AFTER_ALL_VERIFICATION.json','SHUTDOWN.json','DISK_ALIAS_10_MANIFEST.json','FULL_IMPACT.json']
    for n in names:assert r.digest((S/n).read_bytes())==seal[n],n
    cp=r.load(S/'FINAL_CHECKPOINT.json');assert cp['SAFE_TO_POWER_OFF']=='YES' and cp['RUN2_COMPLETED'] and cp['FULL_QA_VERIFIED'] is False
    base=r.load(S/'DISK_ALIAS_REVISED_BASELINE.json');after=r.load(S/'AFTER_ALL_VERIFICATION.json')
    for k in ['fingerprints','sequences','supplier_namespaces','product_offer_projections','special_products','sib_units','totals']:assert r.encoded(base[k])==r.encoded(after[k]),k
    r.save(H/'PREFLIGHT.json',dict(status='PASS',accepted_phase=str(S.relative_to(o.ROOT)),consumed_file_seal={n:seal[n] for n in names},accepted_seal_sha256=r.digest((S/'FINAL_ARTIFACT_SEAL.json').read_bytes()),business_code_seal=r.load(S/'DISK_ALIAS_10_CODE_SEAL.json'),gated_files=correction.validate_gate()['files'],database_started=False,sql_connections=0,db_writes=0,source_writes=0,supplier_http=0,full_qa_resumed=False,checked_at=r.now()))
    r.save(H/'GIT_BEFORE.json',q.git())
    r.save(H/'REPOSITORY_RULES.json',dict(evidence_class='REPOSITORY-VERIFIED',table_taxon=asdict(identity.TAXON_BY_KIND['table']),consumable_taxon=asdict(identity.TAXON_BY_KIND['consumable']),existing_targets={k:asdict(identity.TAXON_BY_KIND[k]) for k in ['power_feeder','knife','fixture','accessory','chiller']},spare='Existing source-ingestion adapter kind, not core TAXON_BY_KIND entry',classifier_source=inspect.getsource(identity._first_taxon)+inspect.getsource(identity._title_category_kind)+inspect.getsource(identity.classify_product_kind),adapter_source=(o.ROOT/'universal_supplier/adapters/ltt_uss_html.py').read_text('utf-8')))
    print('HOTSPOT offline preflight PASS; SQL/HTTP prohibited',flush=True)

def inventory():
    assert not (H/'ALL_CURRENT_ROWS.json').exists();seal=r.load(S/'FINAL_ARTIFACT_SEAL.json')
    base=r.load(S/'DISK_ALIAS_REVISED_BASELINE.json');pairs={p['id']:(p,v) for p,v in base['product_offer_projections']}
    candidates={k:[] for k in ['FEEDER_FAMILY','TABLE_STEM','CONSUMABLE_CATEGORY']};allrows=[];category=[];counts=Counter()
    classifier=identity.classify_product_kind;prior=identity._title_category_kind
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('HOTSPOT_SQL_PROHIBITED')):
      for n,e in enumerate(r.load(S/'EFFECTIVE_ROW_PLAN.json')['rows'],1):
        path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];j=r.load(path)
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':assert o.extract(j)[0]==e['outcome'];category.append(e);continue
        m=j['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes();body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(raw)==m['response_sha256']==response['response_sha256'] and r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
        calls=[];priors=[]
        def trace(name,category='',*,own_description=''):
            result=classifier(name,category,own_description=own_description);calls.append(dict(name=name,category=category,own_description=own_description,result=asdict(result)));return result
        def trace_prior(name,category=''):
            result=prior(name,category);priors.append(asdict(result));return result
        with patch.object(identity,'classify_product_kind',trace),patch.object(identity,'_title_category_kind',trace_prior):parsed=asdict(r.parse(e['supplier'],body,m['final_url']))
        saved=S/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json";rel=str(saved.relative_to(S));assert r.digest(saved.read_bytes())==seal[rel]
        assert r.encoded(parsed)==r.encoded(r.load(saved)['parsed_product']) and len(calls)==len(priors)==1
        status,diag=o._card_gate(e['supplier'],raw.decode(m['charset'] or 'utf8','replace'),m['final_url'])
        outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diag:outcome='MULTI_VARIANT'
        assert outcome==e['outcome'] and list(diag)==m['diagnostics']
        kind=parsed['enrichment_evidence']['product_kind'];persisted=outcome=='PERSISTED';counts['persisted' if persisted else 'terminal']+=1
        a=dict(entry=e,title=parsed['name'],source_category=parsed['source_category'],current_kind=kind,classifier_inputs=calls[0],title_category_prior=priors[0],persisted=persisted,outcome=outcome,price=parsed['price'],availability=parsed['availability'],payload_sha256=r.digest(r.encoded(parsed)),response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],semantic_decision=None,source_evidence_class='PACKAGED-EVIDENCE',db_evidence_class='PACKAGED-EVIDENCE_LAST_VERIFIED_SQL_NOT_NEW_SQL',interpretation_class='INFERENCE')
        if persisted:
            record=F/'PERSISTED_ROWS'/e['supplier']/path.name;rel=str(record.relative_to(S));assert r.digest(record.read_bytes())==seal[rel];db=r.load(record)
            assert r.encoded(parsed)==r.encoded(db['parsed_product']);p,v=pairs[db['product']['id']]
            assert r.encoded((db['product'],db['offer']))==r.encoded((p,v)) and kind==p['raw_data']['enrichment_evidence']['product_kind']==v['raw_data']['enrichment_evidence']['product_kind']
            a['db_evidence']=dict(source_product_id=p['id'],offer_id=v['id'],record=str(record.relative_to(o.ROOT)),record_sha256=r.digest(record.read_bytes()))
        groups=[]
        if re.search(r'автоподатчик\w*',normalize_text(a['title'])):groups.append('FEEDER_FAMILY')
        if kind['product_kind']=='table' or re.search(r'\bстол\w*',normalize_text(kind['matched_alias'])):groups.append('TABLE_STEM')
        if kind['product_kind']=='consumable':groups.append('CONSUMABLE_CATEGORY')
        if groups:
            packet=source_packet(e);dest=H/'SOURCE_PACKETS'/e['supplier']/f"{e['index']:06d}.json";r.save(dest,packet)
            a.update(source_packet=str(dest.relative_to(H)),source_packet_sha256=r.digest(dest.read_bytes()),candidate_families=groups)
            for group in groups:candidates[group].append(a)
        allrows.append(a)
        if n%300==0:print('HOTSPOT INVENTORY',n,'/2634',dict((k,len(v)) for k,v in candidates.items()),flush=True)
      assert counts=={'persisted':2453,'terminal':180} and len(category)==1
      r.save(H/'ALL_CURRENT_ROWS.json',dict(rows=allrows,total=2633,category_separate=category,current_payload_delta=0,terminal_outcome_delta=0,counts=counts))
      for group,rows in candidates.items():r.save(H/(group+'_INVENTORY.json'),dict(status='COMPLETE_INVENTORY_PENDING_SEMANTIC_REVIEW',count=len(rows),persisted_count=sum(a['persisted'] for a in rows),terminal_count=sum(not a['persisted'] for a in rows),by_supplier=dict(Counter(a['entry']['supplier'] for a in rows)),current_kind_distribution=dict(Counter(a['current_kind']['product_kind'] for a in rows)),rows=rows))
      r.save(H/'HTTP_SQL_ZERO_PROOF.json',dict(**network,sql_connections=0,db_writes=0,source_writes=0))
    print('HOTSPOT INVENTORY COMPLETE',dict((k,len(v)) for k,v in candidates.items()),flush=True)

def show(group):
    for a in r.load(H/(group+'_INVENTORY.json'))['rows']:
        packet=r.load(H/a['source_packet']);t=packet['content_text'];i=t.find('Назначение');purpose=t[i:i+1200] if i>=0 else t[:1200]
        print(a['entry']['supplier'],a['entry']['index'],'ID'+a['entry']['external_id'],a['outcome'],'|'+a['title'],'|'+str(a['current_kind']),'|category='+a['source_category'],'|purpose='+purpose,'|price='+str(a['price']),'|stock='+str(a['availability']))

if __name__=='__main__':
    import sys
    mode=sys.argv[1]
    if mode=='prepare':prepare()
    elif mode=='inventory':inventory()
    else:show(mode)
