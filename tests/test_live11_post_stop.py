"""Actual stopped 41/42/43 evidence: zero re-fetch/re-ingest of COMMITTED."""
from copy import deepcopy
from pathlib import Path
import base64
import gzip
import json
import socket
import pytest

from universal_supplier.control_plane import source_recovery as r,source_batch as b
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.control_plane.source_runtime import run_source,LEASED_HOST
from universal_supplier.control_plane.queue import CrawlJob
from test_source_recovery import denied
from test_source_recovery import RetryDB
from test_source_runtime import Backend
from test_host_fetch import Clock
from test_partner_pilot import CFG


@pytest.fixture
def stopped(monkeypatch,tmp_path):
    from offline_fixture_clock import freeze_runtime_utc
    freeze_runtime_utc(monkeypatch)
    v=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz').read_bytes()))
    value=v['manifest'];sha='a'*64
    monkeypatch.setattr(socket.socket,'connect',denied);monkeypatch.setattr(socket,'getaddrinfo',denied)
    monkeypatch.setattr('psycopg.connect',denied)
    monkeypatch.setattr(r,'approval',lambda:(value,sha))
    for name in ('STERBRUST_SOURCE_RUNTIME_ENABLED','STERBRUST_ALLOW_SUPPLIER_HTTP','STERBRUST_ALLOW_SOURCE_WRITES'):monkeypatch.setenv(name,'YES')
    monkeypatch.setenv(r.ENV+'_FILE','offline-exact-confirmed-manifest');monkeypatch.setenv(r.ENV+'_SHA256',sha)
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({row['APPROVAL_HOST']:row['APPROVAL_DIGEST_SHA256'] for row in b.matrix_rows()}))
    root=tmp_path/'evidence';monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(root))
    bodies={}
    for key,cp in v['checkpoints'].items():
        item=value['post_stop']['parents'][key];folder=root/item['snapshot']['supplier_code']/('job-'+key);folder.mkdir(parents=True)
        # Preserve exact checkpoint bytes/SHA from the saved source for admission.
        (folder/'checkpoint.json').write_bytes(base64.b64decode(v['checkpoint_bytes'][key]))
        (folder/'input.json').write_text(json.dumps(v['inputs'][key],ensure_ascii=False),encoding='utf-8')
        for row in cp['terminal'].values():
            receipt=row['receipt'] if row['state']!='FAILED' else item['cached_failures'][row['receipt']['url']]['captured_receipt']
            fp=receipt.get('capture_fingerprint')
            if fp in v['cached_bodies']:bodies[receipt['original_capture_metadata']['evidence_ref']]=base64.b64decode(v['cached_bodies'][fp])
    original=Path.read_bytes
    monkeypatch.setattr(Path,'read_bytes',lambda p:bodies[str(p)] if str(p) in bodies else original(p))
    return v,value,sha,root


def test_one_extra_attempt_bound_to_parent_sha_and_scope(stopped):
    v,value,sha,_=stopped
    for key,item in value['post_stop']['parents'].items():
        proof={'kind':'post-stop','parent_job_id':int(key),'approval_sha256':sha}
        env=r.post_stop_envelope(item['snapshot'],3,3,proof)
        assert env['attempt']==4 and env['deadline']==item['snapshot']['source_batch']['deadline']
        r.extra_attempt_envelope(value,env)
        for count,latest in [(4,4),(2,3),(3,4)]:
            with pytest.raises(PermissionError):r.post_stop_envelope(item['snapshot'],count,latest,proof)
        changed=deepcopy(item['snapshot']);changed['crawl_settings']['max_products']+=1
        with pytest.raises(PermissionError):r.post_stop_envelope(changed,3,3,proof)
    with pytest.raises(PermissionError,match='ATTEMPTS_EXHAUSTED'):r.post_stop_envelope({},3,3,None)


def test_cached_failure_is_only_derived_not_checkpoint_mutation(stopped):
    v,value,_,_=stopped;cp=v['checkpoints']['41'];before=deepcopy(cp);item=value['post_stop']['parents']['41']
    receipts=r.post_stop_receipts('beka_mak_tr',41,digest(item['snapshot']['source_input']),cp)
    assert cp==before and len(receipts)==1
    receipt=next(iter(receipts.values()))
    assert receipt['state']=='HTTP_CAPTURED_NOT_COMMITTED' and receipt['external_id']=='613vt'
    bad=deepcopy(cp);next(row for row in bad['terminal'].values() if row['state']=='FAILED')['receipt']['reason']='other'
    with pytest.raises(PermissionError):r.post_stop_receipts('beka_mak_tr',41,digest(item['snapshot']['source_input']),bad)
    with pytest.raises(PermissionError):r.post_stop_receipts('other',41,digest(item['snapshot']['source_input']),cp)


@pytest.mark.parametrize('owner,expected_old,expected_offline',[(41,58,1),(42,105,2)])
def test_real_cached_recovery_never_refetches_or_reingests_committed(stopped,monkeypatch,owner,expected_old,expected_offline):
    v,value,sha,root=stopped;item=value['post_stop']['parents'][str(owner)];snapshot=deepcopy(item['snapshot'])
    proof={'kind':'post-stop','parent_job_id':owner,'run_id':item['run_id'],'approval_sha256':sha,
           'input_sha256':digest(snapshot['source_input']),'runtime_sha256':item['runtime_sha256'],
           'allowed_cached_uncertain':sorted(item.get('cached_failures',{}))}
    r.admission_runtime(v['runtime'][str(owner)],proof)
    monkeypatch.setattr(r,'runtime_proof',lambda *a:proof)
    snapshot['source_batch']=r.post_stop_envelope(snapshot,3,3,proof)
    snapshot['source_batch']['recovery']={k:proof[k] for k in ('kind','parent_job_id','run_id','approval_sha256')}
    snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=owner,resume_input_sha256=digest(snapshot['source_input']))
    backend=Backend();cp=v['checkpoints'][str(owner)]
    oldfps={row['receipt']['capture_fingerprint'] for row in cp['terminal'].values() if row['state']=='COMMITTED'}
    backend.probe=lambda e,receipt:'COMMITTED' if receipt and receipt.get('capture_fingerprint') in oldfps|set(backend.saved) else 'NOT_FOUND' if receipt else 'UNKNOWN'
    original_persist=backend.persist
    child_id=51+owner-41
    def persist(product,capture):
        assert capture.fingerprint not in oldfps
        result=original_persist(product,capture)
        if len(backend.writes)==expected_offline:
            (root/item['snapshot']['supplier_code']/('job-'+str(child_id))/'STOP_REQUESTED').write_text('OPERATOR_OFFLINE_REPLAY_STOP_BEFORE_NEW_HTTP',encoding='utf-8')
        return result
    backend.persist=persist
    job=CrawlJob(child_id,item['supplier_id'],snapshot['supplier_code'],snapshot['adapter_name'],'https://'+snapshot['domain'],'manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=denied,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['status']=='partial',result  # Real remaining frontier is preserved.
    assert result['request_metrics']['requests_total']==0 and len(backend.writes)==expected_offline
    assert result['sql_committed']==expected_old+expected_offline and result['sql_committed_this_job']==expected_offline
    assert result['remaining']==len(cp['frontier']) and not backend.matches


class Cursor:
    def __init__(self,item,value):self.item=item;self.value=value;self.rows=[];self.changed=None
    def execute(self,query,args=None):
        item=self.item
        if 'pg_control_system' in query:
            t=b.TARGET;self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'],'rc_runtime',False,False)]
        elif 'SELECT status,crawl_run_id,settings_snapshot' in query:self.rows=[(item['status'],item['run_id'],item['snapshot'])]
        elif 'md5(coalesce(string_agg' in query:
            table=query.split(' FROM ')[1].split()[0]
            self.rows=[tuple((item['source_hashes'] if args else self.value['post_stop']['manual_hashes'])[table])]
        elif 'sp.id,s.code,sp.external_id,sp.catalog_product_id' in query:self.rows=[tuple(v) for v in self.value['post_stop']['mappings1282']]
        elif 'SELECT metadata' in query:self.rows=[({'source_runtime':self.runtime},)]
        else:raise AssertionError(query)
        if self.changed and self.changed in query:self.rows=[]
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return self.rows


def test_full_post_stop_admission_checks_fresh_sql_and_checkpoint(stopped,monkeypatch):
    v,value,sha,_=stopped;item=value['post_stop']['parents']['41'];q=Cursor(item,value);q.runtime=v['runtime']['41']
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    proof=r.validate_matching(q,item['snapshot'],41,config=CFG)
    assert proof['kind']=='post-stop' and proof['run_id']==22
    q.changed='sp.id,s.code,sp.external_id,sp.catalog_product_id'
    with pytest.raises(PermissionError,match='MAPPINGS_CHANGED'):r.validate_matching(q,item['snapshot'],41,config=CFG)


def test_unknown_uncertain_or_missing_manual_proof_denied(stopped):
    v,value,sha,_=stopped;item=value['post_stop']['parents']['42']
    rt=deepcopy(v['runtime']['42']);rt['resume_uncertain']=['https://vekprom.ru/unknown/']
    proof={'kind':'post-stop','runtime_sha256':digest(rt)}
    with pytest.raises(PermissionError,match='RUNTIME_OWNER_OR_RECEIPTS_CHANGED'):r.admission_runtime(rt,proof)
    value['post_stop']['manual_hashes'].pop('review_decisions')
    with pytest.raises(PermissionError,match='PROOF_INCOMPLETE'):r.post_stop_item(value,42)


def test_other_parser_changes_still_need_review(monkeypatch):
    monkeypatch.setattr(b,'current_head',lambda:'new-head')
    monkeypatch.setattr(r.subprocess,'check_output',lambda *a,**kw:'universal_supplier/adapters/bekamak_official.py\n')
    monkeypatch.setattr(r,'file_sha',lambda *a:'0'*64)
    with pytest.raises(PermissionError,match='UNREVIEWED_PARSER_CHANGE'):r.compatibility(Path('.'),'old','engine')


def test_native_queue_extra_attempt_keeps_parent_and_scope(stopped,monkeypatch):
    from universal_supplier.control_plane.queue import PostgresJobQueue
    v,value,sha,_=stopped;item=value['post_stop']['parents']['41']
    base=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/beka_job17_run18.json.gz').read_bytes()))
    db=RetryDB((base,value,sha));db.value['runtime']=v['runtime']['41']
    for root in value['cancelled_attempts'].values():
        snap=root['snapshot'];db.rows[root['id']]=['cancelled',None,snap,snap['source_input']['canary']['supplier_id']]
    for key,saved in value['post_stop']['parents'].items():db.rows[int(key)]=[saved['status'],saved['run_id'],deepcopy(saved['snapshot']),saved['supplier_id']]
    queue=PostgresJobQueue(CFG);queue._connect=db._connect;before=deepcopy(db.rows[41])
    proof={'kind':'post-stop','parent_job_id':41,'run_id':22,'approval_sha256':sha,'input_sha256':digest(item['snapshot']['source_input']),
           'runtime_sha256':item['runtime_sha256'],'allowed_cached_uncertain':list(item['cached_failures'])}
    monkeypatch.setattr(r,'validate_matching',lambda *a,**kw:proof)
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    result=queue.enqueue(item['supplier_id'],trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=41,recovery_sha256=sha)
    child=db.rows[result.job_id][2]
    assert result.status=='queued' and db.rows[41]==before and db.inserted==1
    assert child['source_batch']['attempt']==4 and child['source_batch']['post_stop_recovery']['parent_job_id']==41
    assert b.phase_scope(child)==b.phase_scope(before[2])


def test_cancelled_parent_cannot_be_recovered_after_start(stopped):
    _,value,sha,_=stopped;item=value['post_stop']['parents']['44'];q=Cursor(item,value)
    assert r.validate_post_stop(q,item['snapshot'],44,CFG,True,value,sha) is None
    q.changed='SELECT status,crawl_run_id,settings_snapshot'
    with pytest.raises(PermissionError,match='PARENT_CHANGED'):r.validate_post_stop(q,item['snapshot'],44,CFG,True,value,sha)
