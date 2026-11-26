"""Two native parsers/queue routes with fake HTTP and transaction contracts."""
from dataclasses import replace
from contextlib import contextmanager
from types import SimpleNamespace
from pathlib import Path
import json
import pytest
from test_source_runtime import setup,Backend,isolation
from universal_supplier.control_plane import adapter_registry
from universal_supplier.control_plane.source_resume import digest
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.control_plane.source_jobs import parse_existing
from universal_supplier.control_plane.rate_control import PARTNER_ST_DEFAULT_RATE_POLICY,OPTIMUM_DEFAULT_RATE_POLICY
from universal_supplier.control_plane.host_fetch import compile_policy

NATIVE=('partner_st','optimum')

def resume(job,result):
    return replace(job,id=2,settings_snapshot={**job.settings_snapshot,'crawl_settings':{
        **job.settings_snapshot['crawl_settings'],'collection_mode':'RESUME',
        'resume_owner_job_id':1,'resume_input_sha256':result['input_sha256']}})

@pytest.mark.parametrize('code',NATIVE)
@pytest.mark.parametrize('mode',['REFRESH','DISCOVERY'])
def test_native_modes_and_committed_resume_no_get_write_or_matching(code,mode):
    run,job,backend,calls,url=setup(code,mode)
    first=run();assert first['status']=='succeeded',first
    assert len(backend.writes)==1 and len(backend.matches)==1
    before=list(calls)
    second=run(resume(job,first))
    assert second['status']=='succeeded' and calls==before
    assert len(backend.writes)==1 and len(backend.matches)==1
    assert second['sql_committed']==1 and not second['deactivation_allowed']

@pytest.mark.parametrize('code',NATIVE)
@pytest.mark.parametrize('point',['before','after'])
def test_native_commit_interruption_reconciles_original_capture(code,point):
    backend=Backend();backend.fail=point
    run,job,backend,calls,url=setup(code,backend=backend)
    first=run();backend.fail=None;before=list(calls)
    second=run(resume(job,first))
    assert second['status']=='succeeded',second
    assert calls==before and len(backend.writes)==1 and len(backend.matches)==1

@pytest.mark.parametrize('code',NATIVE)
def test_native_uncertain_blocks_resume_before_http_or_sql(code):
    run,job,backend,calls,url=setup(code);first=run()
    p=Path(first['checkpoint']);state=json.loads(p.read_text());state.pop('checksum')
    state['terminal'].pop(url);state['inflight'][url]={'state':'UNCERTAIN'}
    state['checksum']=digest(state);p.write_text(json.dumps(state))
    before=list(calls);second=run(resume(job,first))
    assert 'UNCERTAIN' in second['termination_reason'] and not second['resume_available']
    assert calls==before and len(backend.writes)==1

@pytest.mark.parametrize('code',NATIVE)
@pytest.mark.parametrize('status',[403,429])
def test_native_http_stop_cannot_ingest(code,status):
    run,job,backend,calls,url=setup(code,status=status);result=run()
    assert result['termination_reason']=='SOURCE_BLOCKED_HTTP_'+str(status)
    assert not backend.writes and not backend.matches

@pytest.mark.parametrize('code',NATIVE)
def test_existing_native_entrypoint_dispatches_explicit_mode_only(monkeypatch,code):
    run,job,backend,calls,url=setup(code)
    import universal_supplier.control_plane.source_runtime as runtime
    monkeypatch.setattr(runtime,'run_source',lambda j,c:{'job':j,'config':c})
    runner=getattr(adapter_registry,'run_'+code+'_job')
    assert runner(job,'fake')['job'] is job

@pytest.mark.parametrize('code,policy',[('partner_st',PARTNER_ST_DEFAULT_RATE_POLICY),('optimum',OPTIMUM_DEFAULT_RATE_POLICY)])
def test_native_existing_policy_retained(code,policy):
    host='partner-st.ru' if code=='partner_st' else 'optimum.su'
    assert compile_policy(code,'https://'+host)==policy

@pytest.mark.parametrize('code',NATIVE)
def test_native_ledger_and_existing_bounded_ingest_are_one_transaction(code):
    run,job,fake,calls,url=setup(code);result=run();cap=fake.writes[0]
    product=parse_existing(code,Path(cap.evidence_ref).read_text(encoding='utf-8'),url)
    events=[]
    @contextmanager
    def tx():
        events.append('begin')
        try:yield
        except BaseException:events.append('rollback');raise
        else:events.append('commit')
    class Cursor:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,q,args):events.append((q,args));self.q=q
        def fetchone(self):
            if 'SELECT c.id' in self.q:return None
            if 'SELECT sp.id' in self.q:return (1,2,3)
            return (4,)
    def ingest(cards,at,**kw):
        assert events[0]=='begin' and 'commit' not in events
        assert kw=={'supplier_code':code,'bounded':True} and cards==[product]
        events.append('native_ingest');return {'new_products':0}
    backend=SourceBackend.__new__(SourceBackend);backend.code=code
    backend.repo=SimpleNamespace(connection=SimpleNamespace(transaction=tx,cursor=Cursor),ingest=ingest)
    assert backend.persist(product,cap)['ingest_counts']=={'new_products':0}
    assert events[-1]=='commit'
    assert sum(isinstance(x,tuple) and 'INSERT INTO' in x[0] for x in events)==2
