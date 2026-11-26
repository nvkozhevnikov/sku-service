"""Full proposed-vs-accepted frozen impact gate. SQL and transports forbidden."""
from scripts.audit_inverse_kind import r,o,q,P,OUT as AUDIT
from scripts import qa_product_kind29_fresh as old
from sterbrust_matching import product_identity as identity
from dataclasses import asdict
from collections import Counter
from unittest.mock import patch
import copy
B=r.BASE/'INVERSE_KIND_97_BATCH_2026-10-16'

def main():
    assert not (B/'FULL_IMPACT.json').exists()
    for phase in [P,old.OUT,AUDIT]:
        for name,sha in r.load(phase/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((phase/name).read_bytes())==sha,('HISTORICAL_EVIDENCE_DRIFT',phase,name)
    target={(a['supplier'],a['index']):a for a in r.load(B/'AUTHORIZED_97_ROWS.json')['rows']}
    terminal_target={(a['supplier'],a['index']) for a in r.load(B/'AUTHORIZED_TERMINAL4.json')['rows']}
    decisions=r.load(AUDIT/'INVERSE_KIND_SEMANTIC_DECISIONS.json')['rows']
    operators={(a['supplier'],a['index']) for a in decisions if a['semantic_decision']=='OPERATOR_REVIEW_REQUIRED'}
    valid_non={(a['supplier'],a['index']) for a in decisions if a['semantic_decision']=='VALID_NON_MACHINE'}
    previous29={(a['supplier'],a['index']) for a in r.load(P/'PRODUCT_KIND_29_MANIFEST.json')['rows']}
    valid_machine={(a['supplier'],a['index']) for a in r.load(r.BASE/'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15/PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows'] if a['semantic_decision']=='VALID_MACHINE'}
    accepted={(a['supplier'],a['index']):a for fn in ['ALL_PERSISTED_KIND_INVENTORY.json','TERMINAL_INVERSE_KIND_INVENTORY.json'] for a in r.load(AUDIT/fn)['rows']}
    plan=copy.deepcopy(r.load(old.PLAN));rows=[];changed=set();termchanged=set();manifest=[];violations=[]
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('PRE_DB_SQL_FORBIDDEN')):
      for n,e in enumerate(plan['rows'],1):
        key=e['supplier'],e['index'];path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];journal=r.load(path)
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':
            assert o.extract(journal)==('NON_PRODUCT_CATEGORY_PAGE',None,None);continue
        a=accepted[key];m=journal['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes();body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
        assert r.digest(raw)==a['response_sha256']==m['response_sha256'] and r.digest(body)==a['evidence_sha256']==m['evidence_sha256']
        assert o.sanitise_for_charset(raw,m['charset'])==body
        calls=[];original=identity.classify_product_kind
        def spy(name,category='',*,own_description=''):
            answer=original(name,category,own_description=own_description);calls.append(dict(name=name,category=category,own_description=own_description,result=asdict(answer)));return answer
        with patch.object(identity,'classify_product_kind',new=spy):product=r.parse(e['supplier'],body,m['final_url'])
        assert len(calls)==1
        new=asdict(product);before=a['current_kind'];after=new['enrichment_evidence']['product_kind'];different=before!=after;persisted=e['outcome']=='PERSISTED'
        if different:(changed if persisted else termchanged).add(key)
        status,diag=o._card_gate(e['supplier'],raw.decode(m['charset'] or 'utf8','replace'),m['final_url'])
        outcome={'SUCCESS':'PERSISTED','AMBIGUOUS':'AMBIGUOUS','NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE'}.get(status.value,status.value)
        if status.value=='AMBIGUOUS' and 'MULTI_VARIANT' in diag:outcome='MULTI_VARIANT'
        assert outcome==e['outcome'] and status.value==m['status'] and list(diag)==m['diagnostics'],('OUTCOME_DRIFT',key)
        reverted=copy.deepcopy(new);reverted['enrichment_evidence']['product_kind']=before
        oldrecord=r.load(o.ROOT/a['db_evidence']['record'])['parsed_product'] if persisted else r.load(P/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json")['parsed_product']
        assert r.encoded(reverted)==r.encoded(oldrecord),('NON_KIND_PAYLOAD_DRIFT',key)
        if different and ((persisted and key not in target) or (not persisted and key not in terminal_target)):violations.append(dict(entry=e,old=before,new=after,reason='ADDITIONAL_PERSISTED_SCOPE' if persisted else 'ADDITIONAL_TERMINAL_SCOPE'))
        if key in target or key in terminal_target:
            if after['product_kind'] not in {'machine','lathe','milling_machine','drill_press','grinder','beveling_machine'}:violations.append(dict(entry=e,old=before,new=after,reason='EXPECTED_COMPLETE_MACHINE_NOT_REACHED'))
        if different and key in operators|valid_non|previous29|valid_machine|{('ltt_sib',941),('ltt_sib',958)}:violations.append(dict(entry=e,old=before,new=after,reason='PROTECTED_COHORT_CHANGED'))
        if key in target:
            revised=copy.deepcopy(journal);revised['product_payload_sha256']=r.digest(r.encoded(new));revised['reviewed_inverse_kind_correction']=dict(old_kind=before,new_kind=after,original_journal=e['journal'],original_journal_sha256=e['journal_sha256'],no_new_supplier_http=True)
            dest=B/'REVISED_ROWS'/e['supplier']/path.name;r.save(dest,revised);e.update(journal=str(dest.relative_to(o.ROOT)),journal_sha256=r.digest(dest.read_bytes()))
            db=a['db_evidence'];manifest.append(dict(supplier=e['supplier'],index=e['index'],external_id=a['external_id'],url=a['url'],title=a['title'],old_kind=before,new_kind=after,cluster=target[key]['primary_cluster'],response_sha256=a['response_sha256'],evidence_sha256=a['evidence_sha256'],original_observed_at=a['original_observed_at'],source_product_id=db['source_product_id'],offer_id=db['offer_id'],original_db_record=db['record'],original_db_record_sha256=db['record_sha256'],revised_journal=e['journal'],revised_journal_sha256=e['journal_sha256']))
        r.save(B/'PARSED_ROWS'/e['supplier']/f"{e['index']:06d}.json",dict(entry=e,parsed_product=new,classifier_inputs=calls[0],old_kind=before))
        rows.append(dict(supplier=e['supplier'],index=e['index'],persisted=persisted,old_kind=before,new_kind=after,kind_changed=different,outcome=outcome,non_kind_payload_unchanged=True))
        if n%200==0:print('INVERSE FULL IMPACT',n,'/2634',flush=True)
    result=dict(status='PASS' if not violations and changed==set(target) and termchanged==terminal_target else 'BLOCKED',scope=2634,persisted=2453,terminal_computed=180,category=1,changed_persisted=len(changed),changed_terminal=len(termchanged),additional_persisted=len(changed-set(target)),additional_terminal=len(termchanged-terminal_target),missing_targets=[list(k) for k in set(target)-changed],missing_terminal_targets=[list(k) for k in terminal_target-termchanged],identity_delta=0,title_delta=0,commercial_payload_delta=0,terminal_outcome_delta=0,operator_review106_changed=sum(k in changed for k in operators),valid_non_machine597_changed=sum(k in changed for k in valid_non),previous29_changed=sum(k in changed for k in previous29),valid_machine362_changed=sum(k in changed for k in valid_machine),rows=rows,violations=violations,network=network,db_started=False,checked_at=r.now())
    r.save(B/'FULL_IMPACT.json',result)
    assert result['status']=='PASS',('PRE_DB_IMPACT_BLOCKER',violations)
    assert len(rows)==2633 and len(manifest)==97 and Counter(a['supplier'] for a in manifest)==dict(ltt_msk=20,ltt_sib=11,ltt_yug=47,uss_stanko=19)
    r.save(B/'INVERSE_KIND_97_MANIFEST.json',dict(count=97,rows=manifest));r.save(B/'EFFECTIVE_ROW_PLAN.json',plan)
    print('FULL IMPACT PASS exact97/4; all protected cohorts and non-kind payloads unchanged',flush=True)

if __name__=='__main__':main()
