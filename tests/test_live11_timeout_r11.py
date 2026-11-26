"""Signed timeout isolation: native SQL/checkpoint paths, offline clients only."""
from copy import deepcopy
from pathlib import Path
import gzip,hashlib,json
import pytest
from test_live11_scope_policy import policy_run
from test_live11_r10_dispatch import extension
from test_source_runtime import isolation
from universal_supplier.control_plane import source_recovery as recovery


def authorization(value,tmp_path):
    extension(value,tmp_path)
    auth={'action':'READ_TIMEOUT_QUARANTINE_AND_R11','batch_id':value['batch_id'],
        'previous_approval_sha256':'d998d79bd79746f5cec343b695e6c45a5875cbd52a0fb8efbdf335d7c912274f',
        'original_started_at':value['started_at'],'original_deadline':value['deadline'],
        'execution_until':'2026-10-27T11:27:36+00:00','extra_refresh_parents':list(range(110,119)),
        'refresh_max_attempt':11,'discovery_max_attempt':4,'historical_timeout_ids':[8429,8430,8431],
        'consecutive_error_threshold':3,'cooldown_seconds':60,
        'matrix_sha256':value['matrix_sha256'],'scope_hashes':value['scope_hashes'],'input_hashes':value['input_hashes']}
    path=tmp_path/'timeout-auth.json';path.write_text(json.dumps(auth),encoding='utf-8')
    value['continuation']['timeout_authorization']={'path':str(path),'sha256':recovery.file_sha(path)}
    return auth,path


@pytest.mark.parametrize('failure',[None,'source','capture','observation','history'])
def test_timeout_unknown_proof_and_real_runtime_no_retry(tmp_path,monkeypatch,failure):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch,timeout=True,failure=failure)
    authorization(value,tmp_path)
    result=run();cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert calls.count(bad['url'])==1 and not targets
    row=cp['terminal'][bad['url']]
    assert row['history'][0]['state']=='UNCERTAIN'
    if failure:
        assert result['status']=='partial' and row['state']=='UNCERTAIN' and not backend.writes
    else:
        assert result['status']=='succeeded' and result['sql_committed']==1,result
        assert row['state']=='QUARANTINED' and row['receipt']['isolation']['http_response_available'] is False
        assert 'original_capture_metadata' not in row['receipt']
        assert len(backend.writes)==1 and backend.writes[0].requested_url==good
        item=recovery.approved_scope_item(row['receipt'],bad)
        assert item['refusal']['exception']=='TimeoutError'


@pytest.mark.parametrize('signed',[False,True])
def test_three_real_network_failures_keep_breaker_and_single_unknown_never_retries(signed):
    from test_host_fetch import fetcher
    from offline_host_profiles import approved_policy
    rate=approved_policy('vekprom','https://vekprom.ru/',{'max_concurrency':1,'max_attempts':3,'jitter_max_seconds':0,'consecutive_error_threshold':3,'cooldown_seconds':60})
    f,_=fetcher(rate=rate);f.isolate_timeouts=signed;calls=[]
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append(url)
            raise TimeoutError('The read operation timed out')
    f.client_factory=Client
    rows=list(f.fetch(['https://vekprom.ru/'+str(i) for i in range(4)],limit=4,seconds=300))
    if signed:
        assert f.metrics.network_errors==3 and f.metrics.circuit_breaker_trips==1
        assert f.metrics.circuit_breaker_cooldown_seconds==60
        assert len(calls)==3 and len(set(calls))==3 and f.metrics.retry_count==0
        assert f.stop_reason=='SOURCE_NETWORK_CIRCUIT_OPEN'
        assert sum(bool(r.timeout_refusal) for r in rows)==3
    else:
        assert not any(r.timeout_refusal for r in rows)
        assert calls.count(calls[0])==3  # Original unsigned policy remains unchanged.


@pytest.mark.parametrize('field,new',[('extra_refresh_parents',[111]),('refresh_max_attempt',12),
    ('discovery_max_attempt',5),('execution_until','2026-10-28T11:27:36+00:00'),
    ('consecutive_error_threshold',4),('cooldown_seconds',0),('historical_timeout_ids',[8429]),
    ('previous_approval_sha256','bad'),('matrix_sha256','bad')])
def test_timeout_permission_cannot_expand_scope_or_limits(tmp_path,field,new):
    value={'batch_id':'3a69ca0d-b9d0-41f9-8f2a-49c4b43bc57d','continuation':{}}
    auth,path=authorization(value,tmp_path);auth[field]=new
    path.write_text(json.dumps(auth),encoding='utf-8')
    value['continuation']['timeout_authorization']['sha256']=recovery.file_sha(path)
    with pytest.raises(PermissionError):recovery.timeout_authorization(value)


@pytest.mark.parametrize('defect',[None,'unsigned','discovery','parent','spent','twelve'])
def test_r11_single_bounded_attempt(tmp_path,monkeypatch,defect):
    fixture=Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz'
    value=json.loads(gzip.decompress(fixture.read_bytes()))['manifest']
    value['continuation']={'format':'live11-continuation/v1','batch_id':value['batch_id'],
        'phases':{s:{'max_attempt':8} for s in value['scope_hashes']},
        'manual_hashes':value['post_stop']['manual_hashes'],'mappings1282':value['post_stop']['mappings1282']}
    authorization(value,tmp_path);scope=value['scope_hashes'][0]
    rule=value['continuation']['phases'][scope];rule.update(max_attempt=11,phase='REFRESH',r11_parent_id=110)
    if defect=='unsigned':value['continuation'].pop('timeout_authorization')
    elif defect=='discovery':rule['phase']='DISCOVERY'
    elif defect=='parent':rule['r11_parent_id']=999
    elif defect=='twelve':rule['max_attempt']=12
    monkeypatch.setattr(recovery,'approval',lambda:(value,'b'*64))
    attempt=11 if defect=='spent' else 10
    snapshot={'source_batch':{'scope_sha256':scope,'attempt':attempt}}
    proof={'kind':'continuation','approval_sha256':'b'*64,'parent_job_id':110}
    if defect:
        with pytest.raises(PermissionError):recovery.continuation_envelope(snapshot,attempt,attempt,proof)
    else:assert recovery.continuation_envelope(snapshot,10,10,proof)['attempt']==11


@pytest.mark.parametrize('index',[0,1,2])
@pytest.mark.parametrize('defect',[None,'response','source','authorization','reason'])
def test_real_three_saved_timeout_rows_require_exact_proof(tmp_path,index,defect):
    fixture=json.loads(gzip.decompress((Path(__file__).parent/'fixtures/live11_recovery/kami_three_timeouts.json.gz').read_bytes()))
    item=deepcopy(fixture['items'][index]);context=item['timeout_authorization']
    authorization(context,tmp_path)
    ref=context['continuation']['timeout_authorization']
    item['decision'].update(authorization_path=ref['path'],authorization_sha256=ref['sha256'])
    if defect=='response':item['refusal']['response_available']=True
    elif defect=='source':item['decision']['source']='other'
    elif defect=='authorization':context['continuation'].pop('timeout_authorization')
    elif defect=='reason':item['decision']['reason']='TimeoutError:other'
    if defect:
        with pytest.raises((PermissionError,KeyError)):recovery.scope_quarantine_receipt(item)
    else:
        result=recovery.scope_quarantine_receipt(item)
        assert result['state']=='QUARANTINED' and result['isolation']['http_response_available'] is False
        assert item['failed_row']['state']=='UNCERTAIN'
        assert 'original_capture_metadata' not in item['failed_row']['receipt']


def test_unsigned_runtime_timeout_remains_stop(tmp_path,monkeypatch):
    run,job,backend,calls,targets,bad,good,value,_=policy_run(tmp_path,monkeypatch,timeout=True)
    result=run();cp=json.loads(Path(result['checkpoint']).read_text(encoding='utf-8'))
    assert result['status']=='partial' and cp['terminal'][bad['url']]['state']=='UNCERTAIN'
    assert not backend.writes and not backend.matches


@pytest.mark.parametrize('count',[0,1,2,4])
def test_r11_preparation_requires_all_three_decisions_before_connection(tmp_path,count):
    fixture=Path(__file__).parent/'fixtures/live11_recovery/post_stop_41_50.json.gz'
    value=json.loads(gzip.decompress(fixture.read_bytes()))['manifest']
    value['continuation']={'quarantine':[],'mappings1282':value['post_stop']['mappings1282']}
    _,auth=authorization(value,tmp_path)
    previous=tmp_path/'previous.json';previous.write_text(json.dumps(value),encoding='utf-8')
    class Queue:
        def _connect(self):raise AssertionError('Must reject before SQL')
    decisions=[{'url':str(i),'job_id':111} for i in range(count)]
    with pytest.raises(PermissionError):
        recovery.prepare_continuation(Queue(),previous,recovery.file_sha(previous),[],decisions,timeout_auth=auth)
