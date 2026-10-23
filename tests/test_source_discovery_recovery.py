"""Offline replay of actual job39/run20 and its 104 durable SQL receipts."""
import base64
from copy import deepcopy
import gzip
import json
from pathlib import Path
import socket

import pytest
from universal_supplier.control_plane import source_recovery as recovery, source_batch as batch
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.control_plane.source_runtime import run_source, LEASED_HOST
from universal_supplier.control_plane.queue import CrawlJob
from test_source_recovery import real, denied, ProofCursor, RetryDB
from test_source_runtime import Backend
from test_host_fetch import Clock
from test_partner_pilot import CFG


@pytest.fixture
def discovery(real,monkeypatch,tmp_path):
    v=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/beka_job39_run20.json.gz').read_bytes()))
    manifest=real[1];item={'parent_job_id':39,'run_id':20,'supplier_id':v['parent'][1],'snapshot':v['parent'][4]}
    for key,data in v['data'].items():
        p=tmp_path/('discovery_'+key+'.json');p.write_text(json.dumps(data),encoding='utf-8')
        item[key]={'path':str(p),'sha256':recovery.file_sha(p)}
    manifest['matching_recoveries']={'39':item}
    manifest['cancelled_attempts']={str(r[4]['source_batch']['retry_parent_id']):{'id':r[0],'snapshot':r[4]} for r in v['cancelled_attempts']}
    monkeypatch.setattr(recovery,'approval',lambda:(manifest,real[2]))
    root=Path(__import__('os').environ['SOURCE_RUNNER_EVIDENCE_ROOT'])
    # Current job17 checkpoint advanced natively during technical child28.
    # Restore its saved current bytes, not the older pre-child fixture revision.
    refresh=root/'beka_mak/job-17/checkpoint.json'
    refresh.write_bytes(base64.b64decode(v['refresh_checkpoint_bytes_base64']))
    folder=root/'beka_mak/job-39';folder.mkdir(parents=True)
    for name in ('checkpoint','input','manifest'):(folder/(name+'.json')).write_bytes(base64.b64decode(v[name+'_bytes_base64']))
    node=root/'discovery/beka_mak/job-39';node.mkdir(parents=True)
    (node/'checkpoint.json').write_bytes(base64.b64decode(v['discovery_bytes_base64']))
    monkeypatch.setattr(socket.socket,'connect',denied);monkeypatch.setattr('psycopg.connect',denied)
    return v,manifest,real[2],item


def pure(value,item):
    d=value['data']
    return recovery.validate_discovery_saved(item,d['receipt'],d['verified'],d['before'],value['checkpoint'],value['discovery'],value['input'],value['parent'][4])


def test_actual104_durable_proof(discovery):
    v,m,sha,item=discovery
    assert pure(v,item)['total']==104
    assert v['data']['receipt']['result']['persistence']=={'VALIDATED_UNCHANGED':91}
    assert v['data']['verified']['sql_deltas']['review_cases']==1


@pytest.mark.parametrize('kind',['uncertain','unfinished','missing_product','missing_sql','run','mapping','bucket','manual_proof'])
def test_discovery_proof_tampering_denied(discovery,kind):
    v,_,_,item=discovery;v=deepcopy(v)
    if kind=='uncertain':v['checkpoint']['inflight']['unexpected']={}
    if kind=='unfinished':v['discovery']['discovery_finished']=False
    if kind=='missing_product':v['checkpoint']['terminal'].pop(next(iter(v['checkpoint']['terminal'])))
    if kind=='missing_sql':next(iter(v['checkpoint']['terminal'].values()))['state']='FAILED'
    if kind=='run':v['data']['receipt']['run_id']=19
    if kind=='mapping':v['data']['verified']['after']['mappings'][0][3]=-1
    if kind=='bucket':v['data']['verified']['buckets']['AUTO']=104
    if kind=='manual_proof':v['data']['verified']['protected_tables_exact']=False
    for cp in (v['checkpoint'],v['discovery']):
        state=deepcopy(cp);state.pop('checksum');cp['checksum']=digest(state)
    with pytest.raises(PermissionError):pure(v,item)


class DiscoveryCursor(ProofCursor):
    def execute(self,query,args=None):
        if 'pg_control_system' in query:
            t=batch.TARGET
            self.rows=[(t['dbname'],t['system_identifier'],'17.11',t['pgdata'],t['host'],t['port'],'rc_runtime',False,False)]
        elif 'SELECT status,crawl_run_id,settings_snapshot' in query:self.rows=[('partial',20,self.value['parent'][4])]
        elif 'count(DISTINCT c.id)' in query:self.rows=[(104,104,104)]
        elif 'sp.id,s.code,sp.external_id,sp.catalog_product_id' in query:self.rows=[tuple(r) for r in self.value['data']['verified']['after']['mappings']]
        else:
            d=self.value['data']['verified']
            if 'md5(coalesce(string_agg' in query:self.rows=[tuple(d['after']['hashes'][query.split(' FROM ')[1].split()[0]])]
            elif 'FROM product_matches' in query:self.rows=[tuple(r) for r in d['current_matches']]
            elif 'FROM product_identity_decisions' in query:self.rows=[tuple(r) for r in d['identity_decisions']]
            elif 'FROM review_cases' in query:self.rows=[tuple(r) for r in d['review_cases']]
            else:super().execute(query,args)


def test_fresh104_sql_verifier_and_stale_review(discovery,monkeypatch):
    v,_,sha,item=discovery
    monkeypatch.setattr('universal_supplier.control_plane.source_controls.verify_resume',lambda *a:None)
    q=DiscoveryCursor(v)
    proof=recovery.validate_matching(q,v['parent'][4],39,config=CFG)
    assert proof['run_id']==20 and len(proof['external_ids'])==104
    original=q.execute
    def stale(query,args=None):
        original(query,args)
        if 'FROM review_cases WHERE' in query:q.rows=[]
    q.execute=stale
    with pytest.raises(PermissionError,match='REVIEW_CASES_CHANGED'):recovery.validate_matching(q,v['parent'][4],39,config=CFG)


def test_original109_checkpoint_drift_blocks_discovery_admission(discovery,monkeypatch):
    v,_,_,_=discovery
    root=Path(__import__('os').environ['SOURCE_RUNNER_EVIDENCE_ROOT'])
    (root/'beka_mak/job-17/checkpoint.json').write_text('{}',encoding='utf-8')
    with pytest.raises(PermissionError,match='REFRESH_CHECKPOINT_CHANGED'):
        recovery.validate_matching(DiscoveryCursor(v),v['parent'][4],39,config=CFG)


def test_actual104_resume_no_transport_ingest_or_rematching(discovery,monkeypatch):
    v,_,sha,item=discovery
    proof={'kind':'matching-only','parent_job_id':39,'run_id':20,'approval_sha256':sha,
           'input_sha256':digest(v['input']),'result':pure(v,item),'external_ids':v['data']['receipt']['external_ids']}
    recovery.admission_runtime(v['runtime'],proof)
    monkeypatch.setattr(recovery,'runtime_proof',lambda *a:proof)
    backend=Backend();fps={r['receipt']['capture_fingerprint'] for r in v['checkpoint']['terminal'].values()}
    backend.probe=lambda e,r:'COMMITTED' if r and r['capture_fingerprint'] in fps else 'UNKNOWN'
    backend.persist=denied;backend.matching=denied
    snapshot=deepcopy(v['parent'][4]);snapshot['crawl_settings'].update(collection_mode='RESUME',resume_owner_job_id=39,resume_input_sha256=digest(v['input']))
    snapshot['source_batch'].update(attempt=2,recovery={k:proof[k] for k in ('kind','parent_job_id','run_id','approval_sha256')})
    job=CrawlJob(40,v['parent'][1],'beka_mak','beka_mak','https://beka-mak.su','manual','full','running',None,snapshot)
    token=LEASED_HOST.set(job.domain)
    try:result=run_source(job,CFG,backend_factory=lambda *a:backend,client_factory=denied,clock=Clock())
    finally:LEASED_HOST.reset(token)
    assert result['status']=='succeeded',result
    assert result['sql_committed']==result['processed']==104
    assert result['request_metrics']['requests_total']==result['sql_committed_this_job']==result['parsed_this_job']==result['remaining']==0
    assert result['identity_post_processing']['matching_reused'] and result['identity_post_processing']['recovery_parent_job_id']==39
    assert not backend.writes and not backend.matches


def test_cancelled_attempt2_scope_and_final_attempt_bound(discovery):
    v,m,sha,_=discovery
    for expected in m['parents'][1:]:
        previous=m['cancelled_attempts'][str(expected['id'])]
        attempts=[(previous['id'],previous['snapshot']),(expected['id'],expected['snapshot'])]
        new=recovery.retry_stopped_snapshot(m,expected,attempts,sha)
        assert new['source_batch']['attempt']==3 and batch.phase_scope(new)==batch.phase_scope(expected['snapshot'])
        assert new['source_batch']['deadline']==expected['snapshot']['source_batch']['deadline']
        assert recovery.retry_stopped_snapshot(m,expected,[(999,new)]+attempts,sha) is None
        bad=deepcopy(attempts);bad[0][1]['source_input']['entries']=[]
        with pytest.raises(PermissionError):recovery.retry_stopped_snapshot(m,expected,bad,sha)
        with pytest.raises(PermissionError):recovery.retry_stopped_snapshot(m,expected,[(1000,new),(999,new)]+attempts,sha)


def test_retry10_attempt3_atomic_and_never_retries_started_roots(discovery,real):
    v,m,_,_=discovery;db=RetryDB(real)
    for row in v['cancelled_attempts']:db.rows[row[0]]=[row[2],row[3],deepcopy(row[4]),row[1]]
    before=deepcopy(db.rows)
    db.rows[v['cancelled_attempts'][3][0]][1]=99
    with pytest.raises(PermissionError,match='CANCELLED_STARTED_OR_CHANGED'):recovery.retry_cancelled(db)
    assert db.inserted==0
    db.rows=deepcopy(before);db.fail_on=3
    with pytest.raises(RuntimeError,match='OFFLINE_INSERT_FAILURE'):recovery.retry_cancelled(db)
    assert db.rows==before and db.inserted==0
    db.fail_on=None
    ids=recovery.retry_cancelled(db)
    assert len(ids)==10 and db.inserted==10 and recovery.retry_cancelled(db)==ids
    assert all(db.rows[j][2]['source_batch']['attempt']==3 for j in ids)
    db.rows[ids[0]][0]='cancelled'
    with pytest.raises(PermissionError,match='RETRY_REQUIRES_DECISION'):recovery.retry_cancelled(db)
