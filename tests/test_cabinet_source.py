"""Per-source UI wiring only. Every queue/connection is fake; no live work."""
from pathlib import Path
import json
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import cabinet_source as bridge

CODES = [r['SOURCE'] for r in bridge.source_batch.matrix_rows()]


@pytest.fixture
def backend(monkeypatch):
    calls = []
    def source(code):
        row = bridge.matrix_source(code)
        return dict(id=900+CODES.index(code),code=code,adapter=code,base_url='https://'+row['APPROVAL_HOST'],
            enabled=True,crawl={'host_policy_approval': {'host': row['APPROVAL_HOST'], 'reference': row['APPROVAL_REFERENCE']}},
            rate=json.loads(row['RATE_POLICY_JSON']))
    parent = dict(id=31,status='failed',resume_available=True,uncertain=False)
    monkeypatch.setattr(bridge, 'load_source', lambda q,c,j=None: (source(c), [parent]))
    monkeypatch.setattr(bridge, 'checked_role', lambda queue: ('rc_owner', False))
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM', 'rc_verify_vi_20261019')
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED', 'YES')
    monkeypatch.setenv('PGOPTIONS', '')
    class Queue:
        def __init__(self, config):
            assert config.port == 55461 and config.dbname == 'rc_verify_vi_20261019'
        def preview_source_canary(self, sid, limit, mode):
            calls.append(('preview',sid,limit,mode))
            row=bridge.matrix_source(CODES[sid-900])
            return {'confirmation_sha256':'a'*64,'snapshot':{'source_input':{'canary':{
                'max_discovery_pages':int(row['PAGES_DISCOVERY_PER_JOB']),
                'max_run_duration_seconds':int(row['REFRESH_JOB_SECONDS' if mode=='REFRESH' else 'DISCOVERY_JOB_SECONDS']),
                'safety_gate_size':int(row['SAFETY GATE'])}}}}
        def enqueue(self, sid, **values):
            calls.append(('enqueue',sid,values))
            return SimpleNamespace(job_id=91,status='queued')
        def source_resume_status(self, jid):
            calls.append(('resume_check',jid))
            return True
        def stop_source(self,sid,jid):
            calls.append(('stop',sid,jid))
    monkeypatch.setattr(bridge,'PostgresJobQueue',Queue)
    return calls, parent


@pytest.mark.parametrize('code', CODES)
@pytest.mark.parametrize('mode', ['REFRESH','DISCOVERY'])
def test_every_collector_previews_and_enqueues_via_existing_queue(backend, code, mode):
    calls,_=backend
    preview=bridge.inspect_source(code,mode,1)
    assert preview['ready'] and preview['preview']['confirmation_sha256']=='a'*64
    assert not any(c[0]=='enqueue' for c in calls)
    result=bridge.source_command('source-enqueue',dict(source=code,mode=mode,limit=1,confirmation_sha256='a'*64))
    assert result['job_id']==91
    values=calls[-1][2]
    assert values['source_mode']==mode and values['source_limit']==1 and values['source_preview_sha256']=='a'*64
    assert values['trigger_type']=='manual' and values['crawl_type']=='full'


@pytest.mark.parametrize('code', CODES)
def test_every_collector_resume_uses_existing_checkpoint_and_original_parent(backend, code):
    calls,_=backend
    assert bridge.inspect_source(code,'RESUME',job_id=31)['resume_verified']
    bridge.source_command('source-resume',dict(source=code,mode='RESUME',job_id=31))
    assert ('resume_check',31) in calls
    assert calls[-1][2]['resume_job_id']==31 and calls[-1][2]['source_mode']=='RESUME'
    assert 'source_limit' not in calls[-1][2]


@pytest.mark.parametrize('code', CODES)
def test_every_collector_stop_calls_existing_stop_without_policy_activation(backend, code):
    calls,parent=backend;parent['status']='running'
    bridge.source_command('source-stop',dict(source=code,job_id=31))
    assert calls[-1][0]=='stop' and calls[-1][2]==31
    assert not any(c[0]=='enqueue' for c in calls)


def test_digest_cap_mode_namespace_and_resume_scope_cannot_be_widened(backend):
    calls,_=backend
    for values in [dict(source='kami',mode='REFRESH',limit=6501),
                   dict(source='unknown',mode='REFRESH',limit=1),
                   dict(source='kami',mode='RESUME',limit=1,job_id=31)]:
        with pytest.raises(ValueError):
            bridge.source_command('source-enqueue',values)
    with pytest.raises(PermissionError,match='PREVIEW_CHANGED'):
        bridge.source_command('source-enqueue',dict(source='kami',mode='REFRESH',limit=1,confirmation_sha256='b'*64))
    assert not any(c[0]=='enqueue' for c in calls)


def test_uncertain_resume_never_reaches_enqueue(backend):
    calls,parent=backend;parent['resume_available']=False;parent['uncertain']=True
    with pytest.raises(PermissionError,match='UNCERTAIN'):
        bridge.source_command('source-resume',dict(source='kami',mode='RESUME',job_id=31))
    assert not any(c[0]=='enqueue' for c in calls)


def test_unapproved_and_superuser_sessions_cannot_bypass_role_guard(backend,monkeypatch):
    calls,_=backend
    monkeypatch.delenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM')
    with pytest.raises(PermissionError,match='SESSION_NOT_APPROVED'):
        bridge.source_command('source-enqueue',dict(source='kami',mode='REFRESH',limit=1))
    monkeypatch.setenv('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM','rc_verify_vi_20261019')
    monkeypatch.setattr(bridge,'checked_role',lambda queue: ('rc_owner',True))
    for action in ('source-enqueue','source-stop','source-resume'):
        with pytest.raises(PermissionError,match='ROLE_PREREQUISITE'):
            bridge.source_command(action,dict(source='kami',job_id=31))
    assert not calls


def test_disabled_host_policy_and_budget_fail_closed(backend,monkeypatch):
    calls,_=backend
    original=bridge.load_source
    def disabled(*args):
        source,jobs=original(*args);source['enabled']=False;return source,jobs
    monkeypatch.setattr(bridge,'load_source',disabled)
    assert 'DISABLED' in bridge.inspect_source('kami','REFRESH',1)['reason']
    with pytest.raises(PermissionError):
        bridge.source_command('source-enqueue',dict(source='kami',mode='REFRESH',limit=1))
    def changed_policy(*args):
        source,jobs=original(*args);source['rate']={};return source,jobs
    monkeypatch.setattr(bridge,'load_source',changed_policy)
    assert 'HOST_POLICY' in bridge.inspect_source('kami','DISCOVERY',1)['reason']
    monkeypatch.setattr(bridge,'load_source',original)
    monkeypatch.setattr(bridge.PostgresJobQueue,'preview_source_canary',lambda *args: {
        'snapshot':{'source_input':{'canary':{'max_discovery_pages':999999,
        'max_run_duration_seconds':1,'safety_gate_size':1}}}})
    assert bridge.inspect_source('kami','DISCOVERY',1)['reason']=='SOURCE_BUDGET_EXCEEDS_LIVE11_MATRIX'
    assert not any(c[0]=='enqueue' for c in calls)


def test_projection_preserves_zero_unknown_and_blocks_uncertain():
    row=(31,'failed',{'crawl_settings':{'collection_mode':'REFRESH'}},None,17,None,None,None,
         {'qa_tag':'B0_SQL_QA_SYNTHETIC_NOT_HTTP','source_runtime':{'processed':0,'sql_committed':0,'remaining':None,
                            'resume_available':True,'resume_uncertain':True},'progress':{'http_attempts':3}})
    job=bridge.project_job(row)
    assert job['processed']==0 and job['sql_receipts']==0 and job['remaining'] is None
    assert job['http_progress']=={'http_attempts':3} and not job['resume_available']
    assert job['provenance']=='SYNTHETIC_NOT_LIVE'


def test_job_monitor_is_read_only_even_after_runtime_admission_is_removed(backend,monkeypatch):
    calls,_=backend
    monkeypatch.delenv('STERBRUST_SOURCE_RUNTIME_ENABLED')
    result=bridge.inspect_source('kami',job_id=31)
    assert result['reason']=='SOURCE_JOB_STATUS' and result['jobs'][0]['id']==31
    assert not result['ready'] and not calls
