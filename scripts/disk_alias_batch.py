"""Exact disk10+terminal1 transition, frozen full impact before any database."""
from scripts import run_ltt_uss_run1 as r
from scripts import run_ltt_uss_run2_offline as o
from scripts import qa_ltt_uss_latest as q
from dataclasses import asdict
from collections import Counter
from unittest.mock import patch
import copy,shutil
B=r.BASE/'INVERSE_KIND_97_BATCH_2026-10-16'
N=r.BASE/'INVERSE97_VERIFY_ONLY_RESUME_2026-10-16'
D=r.BASE/'DISK_ALIAS_IMPACT_SWEEP_2026-10-16'
S=r.BASE/'DISK_ALIAS_10_BATCH_2026-10-16'
P=r.BASE/'PRODUCT_KIND_29_RESUME_2026-10-16'
AUTH_SHA='20eb6f657de9635eb8aa815b9e1a9076d30d6e1ab7216f8ca68108aeb4054fe2'

def historical():
    for phase in [B,N,D]:
      for n,sha in r.load(phase/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((phase/n).read_bytes())==sha,('HISTORICAL_DRIFT',phase,n)
    assert r.digest((S/'AUTHORIZED_10_ROWS.json').read_bytes())==AUTH_SHA
    for n,sha in r.load(S/'ACCEPTED_ALL_CODE_SEAL.json').items():assert r.digest((S/'ARCHIVED_ACCEPTED_CODE'/n).read_bytes())==sha,('ACCEPTED_ARCHIVE_DRIFT',n)

def seal():
    assert not (S/'DISK_CODE_PROVENANCE.json').exists();historical()
    before=r.load(S/'ACCEPTED_BUSINESS_SEAL.json');after={n:r.digest((o.ROOT/n).read_bytes()) for n in before}
    assert [n for n in before if before[n]!=after[n]]==['sterbrust_matching/product_identity.py']
    for n in after:
      dest=S/'ARCHIVED_DISK_CODE'/n;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(o.ROOT/n,dest)
    r.save(S/'DISK_ALIAS_10_CODE_SEAL.json',after)
    r.save(S/'DISK_CODE_PROVENANCE.json',dict(baseline_name='INVERSE_KIND_97_ACCEPTED_CODE_SEAL',proposed_name='DISK_ALIAS_10_CODE_SEAL',before=before,after=after,changed_business_paths=['sterbrust_matching/product_identity.py'],authorization_sha256=AUTH_SHA,prior_code_seal_sha256=r.digest((B/'INVERSE_KIND_97_CODE_SEAL.json').read_bytes()),audit_seal_sha256=r.digest((D/'FINAL_ARTIFACT_SEAL.json').read_bytes())))
    print('DISK_PROVENANCE_SHA',r.digest((S/'DISK_CODE_PROVENANCE.json').read_bytes()),flush=True)

def impact():
    historical();assert not (S/'FULL_IMPACT.json').exists()
    allowed={(a['entry']['supplier'],a['entry']['index']):a for a in r.load(S/'AUTHORIZED_10_ROWS.json')['rows']}
    plan=copy.deepcopy(r.load(B/'EFFECTIVE_ROW_PLAN.json')['rows']);changed=set();terminal=set();manifest=[];resultrows=[];counts=Counter()
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('PRE_DB_SQL_PROHIBITED')):
      for n,e in enumerate(plan,1):
        key=e['supplier'],e['index'];path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];j=r.load(path)
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':assert o.extract(j)[0]==e['outcome'];counts['category']+=1;continue
        m=j['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes();body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(raw)==m['response_sha256']==response['response_sha256'] and r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
        status,diag=o._card_gate(e['supplier'],raw.decode(m['charset'] or 'utf8','replace'),m['final_url']);outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diag:outcome='MULTI_VARIANT'
        assert outcome==e['outcome'] and status.value==m['status'] and list(diag)==m['diagnostics']
        p=asdict(r.parse(e['supplier'],body,m['final_url']));old=r.load(B/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json")['parsed_product'];oldkind=old['enrichment_evidence']['product_kind'];newkind=p['enrichment_evidence']['product_kind'];delta=oldkind!=newkind
        reverted=copy.deepcopy(p);reverted['enrichment_evidence']['product_kind']=oldkind;assert r.encoded(reverted)==r.encoded(old),('NON_KIND_DELTA',key)
        persisted=outcome=='PERSISTED';counts['persisted' if persisted else 'terminal']+=1
        if delta:(changed if persisted else terminal).add(key)
        if persisted and delta:
          assert key in allowed,('ADDITIONAL_PERSISTED_KIND_DELTA',key)
          a=allowed[key];assert oldkind==a['current_kind'] and newkind['product_kind']==a['expected_existing_kind']
          revised=copy.deepcopy(j);revised['product_payload_sha256']=r.digest(r.encoded(p));revised['reviewed_disk_alias_correction']=dict(old_kind=oldkind,new_kind=newkind,original_journal=e['journal'],original_journal_sha256=e['journal_sha256'],no_new_supplier_http=True)
          dest=S/'REVISED_ROWS'/e['supplier']/path.name;r.save(dest,revised);e.update(journal=str(dest.relative_to(o.ROOT)),journal_sha256=r.digest(dest.read_bytes()))
          db=a['db_evidence'];manifest.append(dict(supplier=key[0],index=key[1],external_id=e['external_id'],url=e['url'],title=p['name'],old_kind=oldkind,new_kind=newkind,source_product_id=db['source_product_id'],offer_id=db['offer_id'],original_db_record=db['record'],original_db_record_sha256=db['record_sha256'],evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],original_observed_at=m['observed_at'],revised_journal=e['journal'],revised_journal_sha256=e['journal_sha256']))
        if not persisted and delta:
          assert key==('uss_stanko',225) and newkind==dict(product_kind='unknown',matched_alias='',source='none',confidence='UNKNOWN'),('ADDITIONAL_TERMINAL_KIND_DELTA',key,newkind)
        r.save(S/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json",dict(entry=e,parsed_product=p,old_kind=oldkind))
        resultrows.append(dict(supplier=key[0],index=key[1],persisted=persisted,outcome=outcome,old_kind=oldkind,new_kind=newkind,kind_changed=delta,non_kind_payload_unchanged=True))
        if n%200==0:print('DISK FULL IMPACT',n,'/2634',flush=True)
    assert changed==set(allowed) and terminal=={('uss_stanko',225)} and counts=={'persisted':2453,'terminal':180,'category':1}
    current={(a['supplier'],a['index']):a for a in resultrows};protect={}
    for label,cohort in r.load(D/'PROTECTED_COHORTS_RISK.json')['cohorts'].items():
      for a in cohort['rows']:
        row=current[a['supplier'],a['index']];assert not row['kind_changed'] and row['new_kind']==a['current_kind']
      protect[label]=cohort['count']
    for label,records in [('TRUE_DISK137',r.load(D/'DISK_ALIAS_VALID_REGRESSION_COHORT.json')['rows']),('CONTEXT93',[a for a in r.load(D/'DISK_ALIAS_SEMANTIC_DECISIONS.json')['rows'] if a['current_non_disk_winner_preserved']])]:
      assert len(records)==(137 if label=='TRUE_DISK137' else 93)
      for a in records:assert not current[a['entry']['supplier'],a['entry']['index']]['kind_changed']
      protect[label]=len(records)
    for index in [941,958]:assert not current['ltt_sib',index]['kind_changed']
    r.save(S/'DISK_ALIAS_10_MANIFEST.json',dict(operation='PRODUCT_KIND_METADATA_CORRECTION',count=10,authorization_sha256=AUTH_SHA,rows=manifest))
    r.save(S/'EFFECTIVE_ROW_PLAN.json',dict(rows=plan,total=2634))
    r.save(S/'FULL_IMPACT.json',dict(status='PASS',scope=2634,counts=counts,changed_persisted=10,changed_terminal=1,additional_persisted_kind_delta=0,additional_terminal_kind_delta=0,non_kind_payload_delta=0,identity_delta=0,title_delta=0,price_delta=0,currency_delta=0,availability_delta=0,commercial_delta=0,terminal_outcome_delta=0,protected=protect,sib941958_unchanged=True,rows=resultrows,network=network,checked_at=r.now()))
    print('FULL IMPACT PASS exactly10+1; all protected unchanged',flush=True)

if __name__=='__main__':
    import sys
    {'seal':seal,'impact':impact}[sys.argv[1]]()
