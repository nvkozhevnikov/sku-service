"""Immutable authorized36, explicit code provenance and full frozen impact."""
from scripts.disk_alias_batch import r,o,q,B,N,D,P
from scripts import disk_alias_batch as old
from scripts.audit_pre_final_hotspots import H
from dataclasses import asdict
from unittest.mock import patch
from collections import Counter
import copy,shutil
T=old.S
S=r.BASE/'FINAL_HOTSPOT36_BATCH_2026-10-16'
AUTH_SHA='6e799165b6eb923bd6df5a0988ae8b745d2159187765c28f3768716c1f4356d5'

def historical():
    old.historical()
    for phase in [T,H]:
      for n,sha in r.load(phase/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((phase/n).read_bytes())==sha,('HISTORICAL_DRIFT',phase,n)
    assert r.digest((S/'AUTHORIZED_36_ROWS.json').read_bytes())==AUTH_SHA
    for n,sha in r.load(S/'ACCEPTED_ALL_FILE_SEAL.json').items():assert r.digest((S/'ARCHIVED_ACCEPTED_CODE'/n).read_bytes())==sha,('ARCHIVE_DRIFT',n)

def seal():
    historical();assert not (S/'CODE_PROVENANCE.json').exists()
    before=r.load(S/'ACCEPTED_BUSINESS_SEAL.json');after={n:r.digest((o.ROOT/n).read_bytes()) for n in before}
    changed=sorted(n for n in before if before[n]!=after[n]);assert changed==['sterbrust_matching/product_identity.py','universal_supplier/adapters/ltt_uss_html.py']
    for n in after:
      dest=S/'ARCHIVED_PROPOSED_CODE'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(o.ROOT/n,dest)
    r.save(S/'FINAL_HOTSPOT_36_CODE_SEAL.json',after)
    r.save(S/'CODE_PROVENANCE.json',dict(baseline='DISK_ALIAS_10_CODE_SEAL',proposal='FINAL_HOTSPOT_36_CODE_SEAL',before=before,after=after,changed_business_paths=changed,authorization_sha256=AUTH_SHA,previous_provenance_sha256=r.digest((T/'DISK_CODE_PROVENANCE.json').read_bytes()),audit_seal_sha256=r.digest((H/'FINAL_ARTIFACT_SEAL.json').read_bytes())))
    print('PROVENANCE_SHA',r.digest((S/'CODE_PROVENANCE.json').read_bytes()),flush=True)

def impact():
    historical();assert not (S/'FULL_IMPACT.json').exists()
    allowed={(a['supplier'],a['row']):a for a in r.load(S/'AUTHORIZED_36_ROWS.json')['rows']}
    plan=copy.deepcopy(r.load(T/'EFFECTIVE_ROW_PLAN.json')['rows']);changed=set();terminal=set();manifest=[];results=[];counts=Counter()
    inputs={(a['entry']['supplier'],a['entry']['index']):a for a in r.load(H/'ALL_CURRENT_ROWS.json')['rows']}
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('PRE_DB_SQL_PROHIBITED')):
      for n,e in enumerate(plan,1):
        key=e['supplier'],e['index'];path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];j=r.load(path)
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':assert o.extract(j)[0]==e['outcome'];counts['category']+=1;continue
        m=j['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes();body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(raw)==m['response_sha256']==response['response_sha256'] and r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
        status,diag=o._card_gate(e['supplier'],raw.decode(m['charset'] or 'utf8','replace'),m['final_url']);outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diag:outcome='MULTI_VARIANT'
        assert outcome==e['outcome'] and status.value==m['status'] and list(diag)==m['diagnostics']
        p=asdict(r.parse(e['supplier'],body,m['final_url']));before=r.load(T/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json")['parsed_product'];oldkind=before['enrichment_evidence']['product_kind'];newkind=p['enrichment_evidence']['product_kind'];delta=oldkind!=newkind
        reverted=copy.deepcopy(p);reverted['enrichment_evidence']['product_kind']=oldkind;assert r.encoded(reverted)==r.encoded(before),('NON_KIND_DELTA',key)
        persisted=outcome=='PERSISTED';counts['persisted' if persisted else 'terminal']+=1
        if delta:(changed if persisted else terminal).add(key)
        if delta:
          assert persisted and key in allowed,('UNAUTHORIZED_KIND_DELTA_STOP_BEFORE_DB',key)
          a=allowed[key];assert oldkind==a['current_kind'] and newkind['product_kind']==a['expected_existing_kind']
          revised=copy.deepcopy(j);revised['product_payload_sha256']=r.digest(r.encoded(p));revised['reviewed_final_hotspot36_correction']=dict(old_kind=oldkind,new_kind=newkind,original_journal=e['journal'],original_journal_sha256=e['journal_sha256'],no_new_supplier_http=True)
          dest=S/'REVISED_ROWS'/e['supplier']/path.name;r.save(dest,revised);e.update(journal=str(dest.relative_to(o.ROOT)),journal_sha256=r.digest(dest.read_bytes()))
          db=a['db_evidence'];manifest.append(dict(supplier=key[0],index=key[1],external_id=e['external_id'],url=e['url'],title=p['name'],old_kind=oldkind,new_kind=newkind,source_product_id=db['source_product_id'],offer_id=db['offer_id'],original_db_record=db['record'],original_db_record_sha256=db['record_sha256'],evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],original_observed_at=m['observed_at'],revised_journal=e['journal'],revised_journal_sha256=e['journal_sha256']))
        r.save(S/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json",dict(entry=e,parsed_product=p,old_kind=oldkind,classifier_inputs=inputs[key]['classifier_inputs']))
        results.append(dict(supplier=key[0],index=key[1],persisted=persisted,outcome=outcome,old_kind=oldkind,new_kind=newkind,kind_changed=delta,non_kind_payload_unchanged=True))
        if n%250==0:print('HOTSPOT FULL IMPACT',n,'/2634',len(changed),'changes',flush=True)
      assert changed==set(allowed) and not terminal and counts==dict(persisted=2453,terminal=180,category=1)
      current={(a['supplier'],a['index']):a for a in results};protected={}
      for label,c in r.load(H/'HOTSPOT_PROTECTED_COHORTS.json')['cohorts'].items():
        for a in c['rows']:
          e=a['entry'];v=current[e['supplier'],e['index']];assert not v['kind_changed'] and v['new_kind']==a['current_kind'],('PROTECTED_DELTA',label,e)
        protected[label]=c['count']
      for index in [941,958]:assert not current['ltt_sib',index]['kind_changed']
      r.save(S/'FINAL_HOTSPOT36_MANIFEST.json',dict(operation='PRODUCT_KIND_METADATA_CORRECTION',count=36,authorization_sha256=AUTH_SHA,rows=manifest))
      r.save(S/'EFFECTIVE_ROW_PLAN.json',dict(rows=plan,total=2634))
      r.save(S/'FULL_IMPACT.json',dict(status='PASS',scope=2634,counts=counts,changed_persisted=36,changed_terminal=0,additional_persisted_kind_delta=0,terminal_kind_delta=0,identity_delta=0,title_delta=0,price_delta=0,currency_delta=0,availability_delta=0,commercial_payload_delta=0,non_kind_payload_delta=0,terminal_outcome_delta=0,protected=protected,sib941958_unchanged=True,rows=results,network=network,checked_at=r.now()))
    print('FULL IMPACT PASS EXACT36/terminal0/nonkind0/all protected unchanged',flush=True)

if __name__=='__main__':
    import sys
    {'seal':seal,'impact':impact}[sys.argv[1]]()
