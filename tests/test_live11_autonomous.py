"""24h controller/approval/report checks; never HTTP, enqueue or business SQL."""
from copy import deepcopy
from datetime import datetime,timedelta,timezone
from pathlib import Path
from types import SimpleNamespace
import gzip,json,os
import pytest
from test_live11_r10_dispatch import extension
from test_source_runtime import isolation
from test_host_fetch import Clock
from universal_supplier.control_plane import source_autonomy as auto, source_recovery as recovery
from universal_supplier.control_plane.source_hourly import save_report,throughput,checkpoint


def proposed(value,tmp_path):
    extension(value,tmp_path)
    p={'format':'live11-one-shot/v1','batch_id':value['batch_id'],
       'previous_approval_sha256':'2ba5bfd133ae54fb468567655622c784980d5db848bc3318846582eb5a1bca12',
       'original_started_at':value['started_at'],'original_deadline':value['deadline'],
       'matrix_sha256':value['matrix_sha256'],'scope_hashes':value['scope_hashes'],'input_hashes':value['input_hashes'],
       'extra_refresh_parents':list(range(119,128)),'refresh_max_attempt':12,'discovery_max_attempt':4,
       'max_runtime_seconds':86400,'shutdown_reserve_seconds':300,'report_interval_seconds':3600,
       'minimum_free_bytes':5*1024**3,'launch_not_after':'2026-10-27T11:27:36+00:00',
       'execution_not_after':'2026-10-28T11:27:36+00:00'}
    path=tmp_path/'policy.json';path.write_text(json.dumps(p),encoding='utf-8')
    value['continuation']['autonomous']={'path':str(path),'sha256':recovery.file_sha(path)}
    value['compatibility']={'head':'exact-head'}
    return p,path


def controller(tmp_path,monkeypatch,*,snapshot=None,free=20*1024**3):
    value={'batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','continuation':{},'settings_before':[]}
    proposed(value,tmp_path);clock=Clock();now=datetime(2026,10,26,13,tzinfo=timezone.utc)
    monkeypatch.delenv(auto.SESSION_ENV,raising=False);monkeypatch.delenv(auto.SESSION_ENV+'_SHA256',raising=False)
    monkeypatch.setenv(recovery.ENV+'_SHA256','b'*64)
    result={'at':now.isoformat(),'sources':[],'active_jobs':[],'settings_restored_exact':True}
    c=auto.Controller(None,value,'b'*64,tmp_path/'run',now=now,monotonic=clock.monotonic,
        disk_usage=lambda p:SimpleNamespace(free=free),snapshot=snapshot or (lambda *a,**k:deepcopy(result)),utcnow=lambda:now)
    return c,value,clock


@pytest.mark.parametrize('field,new',[('max_runtime_seconds',86401),('shutdown_reserve_seconds',0),
    ('report_interval_seconds',7200),('minimum_free_bytes',0),('refresh_max_attempt',13),('discovery_max_attempt',5),
    ('extra_refresh_parents',[119]),('batch_id','other'),('execution_not_after','2026-10-29T11:27:36+00:00'),
    ('scope_hashes',['other']),('previous_approval_sha256','wrong')])
def test_proposed_policy_cannot_expand_permission(tmp_path,field,new):
    v={'batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','continuation':{}}
    p,path=proposed(v,tmp_path);p[field]=new;path.write_text(json.dumps(p),encoding='utf-8')
    v['continuation']['autonomous']['sha256']=recovery.file_sha(path)
    with pytest.raises(PermissionError):auto.policy(v)


def test_one_shot_claim_bound_to_exact_sha_and_original_window(tmp_path,monkeypatch):
    c,value,clock=controller(tmp_path,monkeypatch);original=(value['started_at'],value['deadline'])
    assert auto.execution_deadline(value).isoformat()=='2026-10-27T11:27:36+00:00'
    with pytest.raises(PermissionError,match='ONE_SHOT'):auto.context(value,required=True)
    s=c.claim();assert auto.context(value,required=True)==s
    assert datetime.fromisoformat(s['hard_until'])-c.started==timedelta(hours=24)
    assert auto.execution_deadline(value)==c.hard-timedelta(minutes=5)
    assert (value['started_at'],value['deadline'])==original
    with pytest.raises(FileExistsError):c.claim()
    path=Path(os.environ[auto.SESSION_ENV]);path.write_text('{}')
    with pytest.raises(PermissionError,match='SESSION_CHANGED'):auto.context(value)


def test_monotonic_clock_stops_even_after_wall_clock_moves_back(tmp_path,monkeypatch):
    c,_,clock=controller(tmp_path,monkeypatch);c.utcnow=lambda:c.started-timedelta(hours=12)
    clock.value=86099;assert c.reason() is None
    clock.value=86100;assert c.reason()=='AUTONOMOUS_TIME_LIMIT'
    with pytest.raises(RuntimeError):c.check()


def test_wall_clock_forward_stops_early(tmp_path,monkeypatch):
    c,_,_=controller(tmp_path,monkeypatch);c.utcnow=lambda:c.work
    assert c.reason()=='AUTONOMOUS_TIME_LIMIT'


def test_low_disk_and_operator_pause_fail_closed(tmp_path,monkeypatch):
    c,_,_=controller(tmp_path,monkeypatch,free=1)
    assert c.reason()=='AUTONOMOUS_DISK_LOW'
    c,_,_=controller(tmp_path,monkeypatch)
    c.folder.mkdir();(c.folder/'STOP_REQUESTED').write_text('pause')
    assert c.reason()=='OPERATOR_STOP'


def test_power_request_released_after_exception_without_display_or_plan_change():
    calls=[]
    with pytest.raises(ValueError):
        with auto.keep_awake(lambda flags:calls.append(flags) or 1):raise ValueError('test')
    assert calls==[0x80000001,0x80000000]
    with pytest.raises(RuntimeError):
        with auto.keep_awake(lambda flags:0):raise AssertionError('must not enter')


def test_hourly_failure_stops_and_preserves_pending_restoration(tmp_path,monkeypatch):
    def bad(*a,**k):raise OSError('sensitive connection detail must not be printed')
    c,_,_=controller(tmp_path,monkeypatch,snapshot=bad);c.claim()
    with pytest.raises(OSError):c.report('RUNNING')
    assert c.reason()=='AUTONOMOUS_REPORT_OR_SQL_FAILED'
    v=json.loads((c.folder/'hourly/latest.json').read_text())
    assert v['postgres']=='UNVERIFIED' and v['original1282_exact']=='UNVERIFIED'
    assert 'sensitive' not in json.dumps(v) and not v['sources']
    assert '__SESSION__' in (c.folder/'hourly/latest.csv').read_text() and 'UNVERIFIED' in (c.folder/'hourly/latest.csv').read_text()
    assert (c.folder/'RESTORATION_PENDING.json').exists()


def test_final_report_restored_vs_pending(tmp_path,monkeypatch):
    c,_,_=controller(tmp_path,monkeypatch);c.claim();c.finish()
    assert not (c.folder/'RESTORATION_PENDING.json').exists()
    assert json.loads((c.folder/'FINAL.json').read_text())['settings_restored'] is True


def test_hourly_files_and_rate_do_not_invent_progress(tmp_path):
    rows=[{'source':str(i),'committed':0,'unique_products':0,'AUTO':0,'REVIEW':0,'CONFLICT':0,'NEW':0,
           'quarantine':0,'errors':0,'remaining_urls':10,'urls_per_hour':None,'eta_hours_estimate':None,'phases':[]} for i in range(11)]
    value={'at':'2026-10-26T13:00:00+00:00','sources':rows,'original1282_exact':True}
    save_report(tmp_path,value,0)
    assert len((tmp_path/'latest.csv').read_text().splitlines())==12
    assert json.loads((tmp_path/'latest.json').read_text())['sources'][0]['eta_hours_estimate'] is None
    assert throughput(3009,2948,3600)==61
    assert throughput(3009,3009,3600)==0 and throughput(3009,3009,0) is None
    file=tmp_path/'cp.json';file.write_text(json.dumps({'checksum':'bad','terminal':{}}))
    with pytest.raises(ValueError,match='CHECKPOINT_CHECKSUM'):checkpoint(file)


@pytest.mark.parametrize('defect',[None,'unsigned','discovery','parent','spent','thirteen'])
def test_r12_only_paused_parents_without_replenishing_caps(tmp_path,monkeypatch,defect):
    fixture=Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz'
    v=json.loads(gzip.decompress(fixture.read_bytes()))['manifest']
    v['continuation']={'format':'live11-continuation/v1','batch_id':v['batch_id'],
        'phases':{s:{'max_attempt':8} for s in v['scope_hashes']},
        'manual_hashes':v['post_stop']['manual_hashes'],'mappings1282':v['post_stop']['mappings1282']}
    proposed(v,tmp_path);scope=v['scope_hashes'][0]
    rule=v['continuation']['phases'][scope];rule.update(max_attempt=12,phase='REFRESH',r12_parent_id=119)
    if defect=='unsigned':v['continuation'].pop('autonomous')
    elif defect=='discovery':rule['phase']='DISCOVERY'
    elif defect=='parent':rule['r12_parent_id']=999
    elif defect=='thirteen':rule['max_attempt']=13
    monkeypatch.setattr(recovery,'approval',lambda:(v,'b'*64))
    attempt=12 if defect=='spent' else 11
    previous={'source_batch':{'scope_sha256':scope,'attempt':attempt}}
    proof={'kind':'continuation','approval_sha256':'b'*64,'parent_job_id':119}
    if defect:
        with pytest.raises(PermissionError):recovery.continuation_envelope(previous,attempt,attempt,proof)
    else:assert recovery.continuation_envelope(previous,11,11,proof)['attempt']==12



def test_saved_committed_matching_finishes_before_fresh_http(tmp_path,monkeypatch):
    from dataclasses import replace
    from test_source_runtime import setup
    from universal_supplier.control_plane.source_runtime import job_dir
    from universal_supplier.control_plane import source_batch
    run,job,backend,calls,url=setup('kami')
    snapshot=deepcopy(job.settings_snapshot)
    snapshot['source_input']['entries'].append({'url':url.rstrip('/')+'-offline-pending/', 'source_external_id':'site:offline-pending'})
    snapshot['crawl_settings']['max_products']=2
    job=replace(job,settings_snapshot=snapshot)
    persist=backend.persist
    def stop_after_commit(*args):
        result=persist(*args)
        (job_dir('kami',1)/'STOP_REQUESTED').write_text('OPERATOR_STOP')
        return result
    backend.persist=stop_after_commit
    first=run(job)
    assert first['status']=='partial' and first['sql_committed']==1 and not backend.matches
    before=list(calls);backend.persist=persist
    settings={**snapshot['crawl_settings'],'collection_mode':'RESUME','resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}
    snapshot={**snapshot,'crawl_settings':settings,'source_batch':{'id':'batch','phase':'REFRESH','deadline':'2030-01-01T00:00:00+00:00'}}
    resumed=replace(job,id=2,settings_snapshot=snapshot)
    monkeypatch.setattr(source_batch,'validate_snapshot',lambda *a:None)
    monkeypatch.setenv(recovery.ENV+'_FILE','offline')
    monkeypatch.setattr(recovery,'approval',lambda:({'batch_id':'batch','continuation':{'autonomous':{'offline':True}}},'b'*64))
    oldmatch=backend.matching
    def matched(ids):
        result=oldmatch(ids);job_dir('kami',2).mkdir(exist_ok=True)
        (job_dir('kami',2)/'STOP_REQUESTED').write_text('OPERATOR_STOP')
        return result
    backend.matching=matched
    result=run(resumed)
    cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert len(backend.matches)==1 and len(backend.writes)==1 and calls==before
    assert cp['terminal'][url]['receipt']['matching_receipt']['job_id']==2


def test_settings_restore_even_when_activation_evidence_write_fails(tmp_path,monkeypatch):
    from contextlib import nullcontext
    from scripts import live11_recovery_session as session
    from universal_supplier.control_plane.admin_store import PostgresAdminStore
    matrix=session.batch.matrix_rows()
    before=[[i+1,r['SOURCE'],False,{},{}] for i,r in enumerate(matrix)]
    state=deepcopy(before)
    class Cursor:
        rowcount=1
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def execute(self,sql,args=None):
            if sql.startswith('SELECT current_user'):self.rows=[('rc_runtime',False,False)]
            elif 'SELECT id,code,enabled' in sql:self.rows=deepcopy(state)
            elif sql.startswith('SELECT id FROM crawl_jobs'):self.rows=[]
            elif sql.startswith('UPDATE suppliers'):
                found=next(r for r in state if r[0]==args[3] and r[1]==args[4])
                found[2:]=[args[0],args[1].obj,args[2].obj]
            else:raise AssertionError(sql)
        def fetchone(self):return self.rows[0]
        def fetchall(self):return self.rows
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def transaction(self):return nullcontext()
        def cursor(self):return Cursor()
    queue=SimpleNamespace(config=None,_connect=Connection)
    monkeypatch.setattr(session.batch,'validate_config',lambda *a:None)
    monkeypatch.setattr(session.batch,'validate_connection',lambda *a:None)
    monkeypatch.setattr(PostgresAdminStore,'_audit',lambda *a:None)
    saved=[]
    def save(folder,name,value):
        if name=='SETTINGS_APPLIED.json':raise OSError('offline disk error')
        saved.append((name,value))
    monkeypatch.setattr(session,'save',save)
    with pytest.raises(OSError):
        with session.settings_session(queue,{'settings_before':before,'activation_matrix':matrix,'batch_id':'batch'},tmp_path):
            raise AssertionError('must not yield')
    assert state==before and saved[0][0]=='SETTINGS_RESTORED.json'


@pytest.mark.parametrize('defect',[None,'sql_receipt','matching','durability'])
def test_report_validates_receipts_and_fresh_matching_without_writes(tmp_path,monkeypatch,defect):
    from contextlib import nullcontext
    import psycopg
    from universal_supplier.control_plane import source_hourly,source_canary,source_runtime
    folder=tmp_path/'kami';folder.mkdir()
    state={'terminal':{'url':{'state':'COMMITTED','receipt':{'capture_fingerprint':'f'*64,'external_id':'site:1','matching_receipt':{'status':'succeeded'}}}},'frontier':[],'inflight':[]}
    state['checksum']=recovery.digest(state);(folder/'checkpoint.json').write_text(json.dumps(state))
    manifest={'batch_id':'b','settings_before':[],'parents':[{'snapshot':{'supplier_code':'kami','source_input':{'entries':[{'url':'url'}]}}}]}
    class Result:
        def __init__(self,rows):self.rows=rows
        def fetchone(self):return self.rows[0]
        def fetchall(self):return self.rows
    class Connection:
        def __enter__(self):return self
        def __exit__(self,*a):pass
        def transaction(self):return nullcontext()
        def cursor(self):return self
        def execute(self,sql,args=None):
            assert sql.lstrip().startswith(('SELECT','SET TRANSACTION')),sql
            if sql.lstrip().startswith('SELECT DISTINCT'):return Result([('kami','REFRESH',120,'partial',49,{'source_runtime':{'owner_job_id':43}},None,'43')])
            if "current_setting('fsync')" in sql:return Result([('off' if defect=='durability' else 'on','on','on')])
            if 'supplier_http_captures' in sql:return Result([] if defect=='sql_receipt' else [(42,'site:1')])
            if 'FROM product_matches' in sql:return Result([] if defect=='matching' else [(42,'NEW_CANDIDATE',False)])
            return Result([])
    monkeypatch.setattr(psycopg,'connect',lambda **kw:Connection())
    monkeypatch.setattr(source_canary,'validate_config',lambda *a:None)
    monkeypatch.setattr(source_canary,'validate_connection',lambda *a:None)
    monkeypatch.setattr(recovery,'continuation_invariants',lambda *a:None)
    monkeypatch.setattr(source_runtime,'job_dir',lambda *a:folder)
    if defect:
        with pytest.raises(ValueError,match='HOURLY_'):source_hourly.collect(SimpleNamespace(kwargs=lambda:{}),manifest)
    else:
        report=source_hourly.collect(SimpleNamespace(kwargs=lambda:{}),manifest)
        assert report['committed']==report['unique_products']==1
        assert report['sources'][0]['NEW']==1 and report['sources'][0]['matching_pending']==0
