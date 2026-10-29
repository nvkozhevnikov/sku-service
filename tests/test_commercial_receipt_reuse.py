"""Actual 161–168 frozen receipts; no supplier HTTP, ingest, or SQL writes."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import gzip,json
import pytest
from universal_supplier.control_plane import commercial_continuation as cc,commercial_refresh as cr
from universal_supplier.control_plane.source_resume import digest

F=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/commercial_receipts_20261028.json.gz').read_bytes()))

@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def denied(*a,**k):raise AssertionError('OFFLINE_NO_HTTP_SQL_OR_INGEST')
    monkeypatch.setattr('psycopg.connect',denied)
    monkeypatch.setattr('urllib.request.OpenerDirector.open',denied)
    monkeypatch.setattr('requests.Session.send',denied)


def saved(tmp_path,code,change=None):
    case=deepcopy(F['cases'][code]);job=deepcopy(next(j for j in F['jobs'] if j[1]==code))
    if change:change(case,job)
    cp=case['checkpoint'];cp.pop('checksum');cp['checksum']=digest(cp)
    folder=tmp_path/code;folder.mkdir()
    for name,value in case.items():(folder/(name+'.json')).write_text(json.dumps(value),encoding='utf-8')
    known={v['receipt']['original_capture_metadata']['evidence_ref']:v['receipt']['original_capture_metadata']['evidence_sha256']
        for v in F['cases'][code]['checkpoint']['terminal'].values()}
    def verified_capture(folder,cap):
        cc.require(known.get(cap['evidence_ref'])==cap['evidence_sha256'],'CONTINUATION_CAPTURE_BYTES_CHANGED')
    expected=[{'source_product_id':e['source_product_id'],'source_external_id':e['external_id'],'url':e['source_url']}
        for e in F['old']['suppliers'][code]['targets']]
    def check():return cc.check_receipt_files(folder,code,job[0],job[3],job[2],job[5]['source_runtime'],expected,F['old']['engine'],verify=verified_capture)
    return case,job,check


@pytest.mark.parametrize('code',list(F['cases']))
def test_actual_full_source_accounting_retains_597_commits_353_q_and_320_unrequested(tmp_path,code):
    _,_,check=saved(tmp_path,code);p=check()
    native=next(j for j in F['native_proof']['jobs'] if j['source']==code)
    assert (p['disposition'],p['committed'],p['quarantined'])==(native['disposition'],native['committed'],native['quarantined'])
    assert p['quarantine_ids']==native['quarantine_ids']


@pytest.mark.parametrize('change,reason',[
    (lambda c,j:c['checkpoint']['inflight'].update({'unknown':{}}),'PHASE_NOT_ACCOUNTED'),
    (lambda c,j:c['checkpoint']['frontier'].append('unknown'),'PHASE_NOT_ACCOUNTED'),
    (lambda c,j:c['checkpoint']['terminal'].pop(next(iter(c['checkpoint']['terminal']))),'TERMINAL_SCOPE_CHANGED'),
    (lambda c,j:j[5]['source_runtime'].update(resume_uncertain=['unknown']),'PHASE_NOT_ACCOUNTED'),
    (lambda c,j:j[5]['source_runtime']['identity_post_processing'].update(status='failed'),'MATCHING_NOT_DURABLE'),
    (lambda c,j:next(iter(c['checkpoint']['terminal'].values()))['receipt']['matching_receipt'].update(run_id=999),'MATCHING_NOT_DURABLE'),
    (lambda c,j:next(iter(c['checkpoint']['terminal'].values()))['receipt'].update(sql_verified=False),'MATCHING_NOT_DURABLE'),
    (lambda c,j:c['input']['entries'][0].update(url='https://outside.test/'),'FROZEN_INPUT_CHANGED'),
    (lambda c,j:c['checkpoint'].update(job_id=999),'CHECKPOINT_IDENTITY_CHANGED'),
    (lambda c,j:c['result'].update(sql_committed=0),'RESULT_COUNTS_CHANGED'),
    (lambda c,j:next(iter(c['checkpoint']['terminal'].values()))['receipt']['original_capture_metadata'].update(evidence_sha256='0'*64),'CAPTURE_BYTES_CHANGED'),
])
def test_no_cached_uncertain_scope_matching_or_receipt_drift_is_treated_as_completed(tmp_path,change,reason):
    _,_,check=saved(tmp_path,'beka_mak',change)
    with pytest.raises(PermissionError,match=reason):check()


def test_quarantine_without_actual_rollback_or_absence_proof_stops(tmp_path):
    def change(c,j):
        r=next(v['receipt'] for v in c['checkpoint']['terminal'].values() if v['state']=='QUARANTINED')
        r['isolation']['rejection_proof']['rollback_verified']=False
    _,_,check=saved(tmp_path,'kami',change)
    with pytest.raises(PermissionError,match='QUARANTINE_NOT_PROVEN'):check()


def receipts(tmp_path):
    result={}
    by_id={e['source_product_id']:e for r in F['old']['suppliers'].values() for e in r['targets']}
    for code in F['cases']:
        _,_,check=saved(tmp_path,code)
        for fp,r in check()['receipts'].items():r['offer_id']=by_id[r['source_product_id']]['offer_id'];result[fp]=r
    return result


@pytest.mark.parametrize('mutate',[
    lambda rows:rows.pop(),
    lambda rows:rows.append(deepcopy(rows[0])),
    lambda rows:rows[0].__setitem__(1,999999),
    lambda rows:rows[0].__setitem__(2,'other-external'),
    lambda rows:rows[0].__setitem__(3,'https://outside.test/'),
    lambda rows:rows[0].__setitem__(5,'2025-01-01T00:00:00+00:00'),
    lambda rows:rows[0].__setitem__(6,'0'*64),
    lambda rows:rows[0].__setitem__(8,999999),
    lambda rows:rows[0].__setitem__(9,False),
])
def test_real_sql_receipts_must_have_exact_identity_time_capture_and_offer(tmp_path,mutate):
    r=receipts(tmp_path);rows=deepcopy(F['sql_rows'])
    cc.check_sql_receipts(r,rows)
    mutate(rows)
    with pytest.raises(PermissionError,match='SQL_RECEIPT'):cc.check_sql_receipts(r,rows)


def test_actual353_quarantines_cannot_be_replayed_as_sql_commits(tmp_path):
    r=receipts(tmp_path);q=next((fp,row) for fp,row in r.items() if row['state']=='QUARANTINED')
    fp,row=q
    extra=[fp,row['source_product_id'],row['external_id'],row['url'],row['url'],row['observed_at'],row['response_sha256'],row['evidence_sha256'],row['offer_id'],True]
    with pytest.raises(PermissionError,match='SQL_RECEIPT_MISMATCH'):cc.check_sql_receipts(r,[*F['sql_rows'],extra])


def value():
    v=deepcopy(F['old']);v['continuation']=deepcopy(F['native_proof']);v['protected_jobs']=v['continuation']['protected_jobs'];return v


def test_scheduler_enqueues_only320_unrequested_not597_committed_or353_quarantine():
    from universal_supplier.control_plane.scheduler import Scheduler
    v=value();calls=[]
    class DB:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def cursor(self):return self
        def execute(self,sql,args):assert sql.lstrip().startswith('SELECT')
        def fetchone(self):return None
        def preview_source_canary(self,sid,n,mode):
            assert (sid,n,mode)==(v['suppliers']['intervesp']['supplier_id'],320,'REFRESH')
            return {'confirmation_sha256':'exact'}
        def enqueue(self,sid,**kw):calls.append((sid,kw));return SimpleNamespace(enqueued=True,job_id=169)
    db=DB();s=SimpleNamespace(value=v,sha='new-exact-approval',check=lambda:None,source_reason=lambda code:None)
    scheduler=Scheduler(None,db,automation=s);scheduler._connect=lambda:db
    assert cr.tick(scheduler,None)==[169]
    assert len(calls)==1 and calls[0][1]['source_limit']==320 and calls[0][1]['source_mode']=='REFRESH'
    assert not scheduler.blocked and not scheduler.refresh_finished


def test_even_direct_preview_and_worker_job_cannot_run_accounted_sources(monkeypatch):
    v=value();s=SimpleNamespace(value=v)
    monkeypatch.setattr('universal_supplier.control_plane.automation_execution.require_active',lambda:s)
    with pytest.raises(PermissionError,match='ACCOUNTED_SOURCE_CANNOT_RUN'):cr.preview(None,1,('kami',))
    with pytest.raises(PermissionError,match='ACCOUNTED_SOURCE_CANNOT_RUN'):cr.validate_snapshot({'supplier_code':'kami'},s)


@pytest.mark.parametrize('field',['identity_confirmed','active'])
def test_accounted_quarantine_never_allows_identity_or_active_guard_bypass(monkeypatch,field):
    e=deepcopy(F['old']['suppliers']['kami']['targets'][0]);r={**e,'identity_confirmed':True,'active':True,'blocking_quarantine':True}
    monkeypatch.setattr('universal_supplier.preview_store.load',lambda *a,**k:{'records':[r]})
    cr.scope_rows(None,[e],accounted_quarantine={e['source_product_id']})
    r[field]=False
    with pytest.raises(PermissionError,match='IDENTITY_OR_QUARANTINE'):cr.scope_rows(None,[e],accounted_quarantine={e['source_product_id']})


def test_unproven_quarantine_still_blocks_pending_source(monkeypatch):
    e=deepcopy(F['old']['suppliers']['intervesp']['targets'][0]);r={**e,'identity_confirmed':True,'active':True,'blocking_quarantine':True}
    monkeypatch.setattr('universal_supplier.preview_store.load',lambda *a,**k:{'records':[r]})
    with pytest.raises(PermissionError,match='IDENTITY_OR_QUARANTINE'):cr.scope_rows(None,[e],accounted_quarantine={-1})


@pytest.mark.parametrize('field',['execution_until','not_before','targets','policy'])
def test_new_exact_grant_cannot_silently_change_original_window_scope_or_policy(tmp_path,monkeypatch,field):
    v=value();old=tmp_path/'previous.json';old.write_text(json.dumps(F['old']),encoding='utf-8');v['continuation']['previous_path']=str(old)
    if field in ('not_before','execution_until'):v[field]='2026-11-01T13:00:00Z'
    elif field=='targets':v['suppliers']['intervesp']['targets'].pop()
    else:v['suppliers']['intervesp']['policy']['min_request_interval_seconds']=0.01
    monkeypatch.setattr(cc,'receipt_proof',lambda *a:v['continuation'])
    reason='EXPLICIT_WINDOW_APPROVAL_REQUIRED' if field in ('not_before','execution_until') else 'ORIGINAL_CONTRACT_CHANGED'
    with pytest.raises(PermissionError,match=reason):cc.validate_live(None,v)


def test_parser_delta_needs_separate_review(monkeypatch):
    monkeypatch.setattr(cc.subprocess,'check_output',lambda *a,**k:'universal_supplier/adapters/bekamak.py\n')
    with pytest.raises(PermissionError,match='ENGINE_COMPATIBILITY'):cc.compatibility(F['old'])


def window_value():
    v=value();p=v['continuation']
    v.update(not_before='2026-10-29T10:00:00Z',execution_until='2026-10-29T14:00:00Z',
        transport_tls={'format':'public-certifi-tls/v1','verify_mode':'CERT_REQUIRED','check_hostname':True,'ca_bundle_sha256':'a'*64})
    v['execution_reauthorization']={'format':cc.WINDOW_KIND,'original_not_before':p['not_before'],
        'original_execution_until':p['execution_until'],'not_before':v['not_before'],
        'execution_until':v['execution_until'],'scope_sha256':v['scope_sha256'],'requires_operator_confirmation':True}
    return v


def test_explicit_bounded_window_preserves_original_evidence_and_all1270_targets():
    v=window_value();p=v['continuation'];before=deepcopy(v['suppliers'])
    cr.validate(v);cc.validate_window(v,p['not_before'],p['execution_until'])
    assert p['execution_until']=='2026-10-28T13:00:00Z' and v['execution_until']=='2026-10-29T14:00:00Z'
    assert v['suppliers']==before and sum(len(r['targets']) for r in before.values())==1270
    assert cc.reused_sources(v)=={'beka_mak','kami','ltt_msk','ltt_sib','partner_st','uss_stanko','vekprom'}


@pytest.mark.parametrize('mutation,reason',[
    (lambda v:v.pop('execution_reauthorization'),'EXPLICIT_WINDOW_APPROVAL'),
    (lambda v:v['execution_reauthorization'].update(requires_operator_confirmation=False),'EXPLICIT_WINDOW_APPROVAL'),
    (lambda v:v['execution_reauthorization'].update(original_execution_until='2026-10-29T00:00:00Z'),'EXPLICIT_WINDOW_APPROVAL'),
    (lambda v:v.pop('transport_tls'),'VERIFIED_TLS_POLICY_REQUIRED'),
    (lambda v:v['transport_tls'].update(check_hostname=False),'VERIFIED_TLS_POLICY_REQUIRED'),
    (lambda v:v['transport_tls'].update(verify_mode='CERT_NONE'),'VERIFIED_TLS_POLICY_REQUIRED'),
])
def test_no_implicit_deadline_or_tls_guard_override(mutation,reason):
    v=window_value();mutation(v)
    with pytest.raises(PermissionError,match=reason):cc.validate_window(v,v['continuation']['not_before'],v['continuation']['execution_until'])


@pytest.mark.parametrize('start,end',[
    ('2026-10-29T10:00:00Z','2026-10-30T10:00:01Z'),
    ('2026-10-29T10:00:00Z','2026-10-29T10:00:00Z'),
    ('2026-10-27T10:00:00Z','2026-10-27T14:00:00Z'),
    ('2026-10-29T10:00:00','2026-10-29T14:00:00'),
])
def test_invalid_new_window_stops_without_any_io(start,end):
    v=window_value();v.update(not_before=start,execution_until=end)
    v['execution_reauthorization'].update(not_before=start,execution_until=end)
    with pytest.raises(PermissionError,match='NEW_WINDOW_INVALID'):cc.validate_window(v,v['continuation']['not_before'],v['continuation']['execution_until'])


@pytest.mark.parametrize('missing_ancestor,changed_code,reason',[
    (True,False,'PINNED_INTEGRATION_MISSING'),(False,True,'INTEGRATION_CODE_CHANGED'),(False,False,None)])
def test_only_exact_pinned_unified_integration_is_compatible(monkeypatch,missing_ancestor,changed_code,reason):
    name='universal_supplier/control_plane/runtime_config.py'
    monkeypatch.setattr(cc.subprocess,'check_output',lambda *a,**k:name+'\n')
    monkeypatch.setattr(cc.subprocess,'call',lambda args,**k:int(missing_ancestor) if args[1]=='merge-base' else int(changed_code))
    monkeypatch.setattr(cc,'file_sha',lambda path:'a'*64)
    if reason:
        with pytest.raises(PermissionError,match=reason):cc.compatibility(F['old'])
    else:assert cc.compatibility(F['old'])=={name:'a'*64}


@pytest.mark.parametrize('drift,reason',[
    ('none',None),('old_job','ORIGINAL_PROTECTED_JOBS_CHANGED'),
    ('new_mapping','PHYSICAL_MAPPING_COUNT_CHANGED'),('input','JOB_INPUT_OR_POLICY_CHANGED'),
])
def test_full_native_admission_checks_saved_jobs_and_physical_mapping_baseline(tmp_path,monkeypatch,drift,reason):
    old=tmp_path/'old.json';old.write_text(json.dumps(F['old']),encoding='utf-8')
    jobs=deepcopy(F['jobs']);protected=deepcopy(F['native_proof']['protected_jobs']);maps=deepcopy(F['all_mappings'])
    for j in jobs:
        folder=tmp_path/'reports/source-runner-evidence'/j[1]/f'job-{j[0]}';folder.mkdir(parents=True)
        for name,v in F['cases'][j[1]].items():(folder/(name+'.json')).write_text(json.dumps(v),encoding='utf-8')
        j[5]['source_runtime']['checkpoint']=str(folder/'checkpoint.json')
    if drift=='old_job':protected[0][1]='tampered'
    if drift=='new_mapping':maps.append([99999,1,'foreign',99999])
    if drift=='input':jobs[0][4]['source_input']['canary']['max_products']+=1
    class DB:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def execute(self,sql,args=()):
            assert sql.lstrip().startswith(('SELECT','BEGIN','ROLLBACK'))
            if 'status IN' in sql:self.rows=[]
            elif 'SELECT j.id,s.code' in sql:self.rows=jobs
            elif 'SELECT cap.capture_fingerprint' in sql:self.rows=F['sql_rows']
            elif 'SELECT id,supplier_id' in sql:self.rows=maps
            elif 'row_to_json(j)' in sql:self.rows=protected
            elif 'row_to_json(pm)' in sql:self.rows=[(i,'matching-pin') for i in range(597)]
            else:self.rows=[]
            return self
        def fetchall(self):return self.rows
    monkeypatch.setattr(cc,'ROOT',tmp_path);monkeypatch.setattr(cc,'guarded_connect',lambda *a,**k:DB())
    monkeypatch.setattr(cc,'compatibility',lambda old:{})
    monkeypatch.setattr(cc.Session,'check_mappings',lambda *a:None)  # native SELECT check separately retained
    checked=cc.check_receipt_files
    monkeypatch.setattr(cc,'check_receipt_files',lambda *a,**k:checked(*a,**k,verify=lambda *a:None))
    if reason:
        with pytest.raises(PermissionError,match=reason):cc.receipt_proof(None,old,cc.file_sha(old))
    else:
        proof=cc.receipt_proof(None,old,cc.file_sha(old))
        assert (proof['committed'],proof['quarantined'],proof['not_requested'])==(597,353,320)
