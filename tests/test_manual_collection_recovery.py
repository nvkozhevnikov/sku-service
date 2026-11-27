"""The authenticated manual path uses the native queue, fetcher and receipts."""
from dataclasses import replace
from contextlib import nullcontext
from datetime import datetime, timezone
from http.client import IncompleteRead, RemoteDisconnected
import json
from pathlib import Path
import socket
import ssl
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import Request

import pytest

from offline_host_profiles import approval_for, approved_policy
from test_automation_admin import client, csrf
from test_host_fetch import Clock
from test_manual_collection import DB, KEY
from test_partner_pilot import html
from test_source_canary import CFG, build_preview
from test_source_jobs import deny
from test_source_runtime import Backend
from universal_supplier.control_plane.manual_collection import MANUAL_EXECUTION, ManualRequest, verify
from universal_supplier.control_plane.queue import CrawlJob, EnqueueResult
from universal_supplier.control_plane.security import AuthUser
from universal_supplier.control_plane.source_backend import SourceBackend
from universal_supplier.control_plane.source_resume import manual_redirect_receipt, manual_stop_receipt, manual_transport_receipt
from universal_supplier.control_plane.source_runtime import LEASED_HOST, engine_fingerprint, run_source
from universal_supplier.http_capture import HttpResponse, _BoundedRedirectHandler


@pytest.fixture(autouse=True)
def offline(monkeypatch,tmp_path):
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'evidence'))
    monkeypatch.setattr('psycopg.connect',deny)
    monkeypatch.setattr('requests.Session.send',deny)
    monkeypatch.setattr(socket,'getaddrinfo',deny)


def native_manual(*, fail_tail=False, end_window=False, block=False, robots_replies=None, robots_limit=2,
                  product_error=TimeoutError, second_error=None, all_cards_fail=False,
                  card_count=3, initial_network_failures=0, duration=100, block_card=None,
                  stop_during_cooldown=False, redirect_target=None, redirect_card=1,
                  first_card_failures=1, missing_cards=0, pause_card=None):
    code='partner_st'
    base='https://partner-st.ru'
    rate_values={'max_attempts':2,'max_concurrency':1,'jitter_max_seconds':0}
    if all_cards_fail:rate_values['consecutive_error_threshold']=2
    rate=approved_policy(code,base,rate_values)
    known=[{'url':base+f'/item-{i}/','source_external_id':str(i)} for i in range(1,card_count+1)]
    data={'format':'source-runtime-input/v1','supplier_code':code,'mode':'REFRESH','entries':known,
          'seeds':[],'rate_policy':rate.snapshot(),'engine_sha256':engine_fingerprint()}
    settings={'collection_mode':'REFRESH','persist_source_data':True,'max_products':card_count,
              'max_run_duration_seconds':duration,'host_policy_approval':approval_for(base)}
    job=CrawlJob(1,1,code,code,base+'/','manual','full','running',datetime.now(timezone.utc),
        {'source_input':data,'rate_settings':rate.snapshot(),'crawl_settings':settings,'manual_collection':{'signed':True}})
    if robots_replies is not None:
        supplier=(code,code,base+'/',True,{'host_policy_approval':approval_for(base),
                                          'max_run_duration_seconds':duration},rate.snapshot())
        rows=[(i,entry['source_external_id'],entry['url'],len(known)) for i,entry in enumerate(known,1)]
        snapshot=build_preview(job.supplier_id,supplier,rows,CFG,len(rows))['snapshot']
        snapshot['manual_collection']={'robots_attempts':robots_limit}
        job=replace(job,settings_snapshot=snapshot)
    clock=Clock();backend=Backend()
    backend.matching=lambda ids:{'status':'succeeded','matching_applied':False}
    backend.product_started_at=[]
    calls=[]
    persisted=backend.persist
    progress=backend.progress

    def persist(product,capture):
        written=persisted(product,capture)
        if end_window and len(backend.writes)==1:clock.sleep(100)
        return written

    backend.persist=persist
    def track(summary):
        progress(summary)
        if stop_during_cooldown and summary['phase']=='NETWORK_COOLDOWN':
            from universal_supplier.control_plane.source_runtime import job_dir
            (job_dir(code,job.id)/'STOP_REQUESTED').touch()
    backend.progress=track

    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url)
            self.before_request(url)
            if url.endswith('/robots.txt'):
                if robots_replies is not None:
                    calls.append('robots')
                    status=next(robots_replies)
                    if status is None:raise URLError(TimeoutError('service timeout'))
                    if status!=200:return HttpResponse(url,status,'text/plain',b'unavailable')
                return HttpResponse(url,200,'text/plain',b'User-agent: *\nAllow: /\n')
            number=int(url.removesuffix('/').rsplit('-',1)[-1])
            calls.append(number)
            backend.product_started_at.append(clock.monotonic())
            if number == pause_card:
                from universal_supplier.control_plane.source_runtime import job_dir
                (job_dir(code,job.id)/'STOP_REQUESTED').write_text('OPERATOR_STOP', encoding='utf-8')
                # Simulate a previously entered request whose next hop is stopped.
                self.before_request(url)
            if number==redirect_card and redirect_target:
                handler=_BoundedRedirectHandler(validator,self.before_request)
                handler.redirect_request(Request(url),None,302,'Found',{},redirect_target)
                pytest.fail('The new address must not be followed')
            if number<=missing_cards:
                return HttpResponse(url,404,'text/plain',b'missing')
            if (all_cards_fail or sum(type(item) is int for item in calls)<=initial_network_failures
                    or (number==1 and (calls.count(1)<=first_card_failures or fail_tail))):
                raise product_error('no response')
            if number==2 and second_error and calls.count(2)==1:
                raise second_error('connection reset')
            if (number==2 and block) or number==block_card:return HttpResponse(url,429,'text/plain',b'busy')
            return HttpResponse(url,200,'text/html; charset=utf-8',html(number).encode())

    def run(value):
        lease=LEASED_HOST.set(value.domain)
        auth=MANUAL_EXECUTION.set(value.id)
        try:return run_source(value,CFG if robots_replies is not None else None,
                              backend_factory=lambda cfg,source:backend,client_factory=Client,clock=clock)
        finally:
            MANUAL_EXECUTION.reset(auth)
            LEASED_HOST.reset(lease)
    return run,job,backend,calls


@pytest.mark.parametrize('fail_tail',[False,True])
@pytest.mark.parametrize('product_error',[TimeoutError,RemoteDisconnected,ConnectionResetError,ConnectionAbortedError,
    pytest.param(lambda message:IncompleteRead(message.encode(),10),id='incomplete-response'),
    pytest.param(lambda message:URLError(TimeoutError(10060,message)),id='wrapped-connect-timeout'),
    pytest.param(lambda message:URLError(ConnectionResetError(10054,message)),id='wrapped-connection-reset')])
def test_manual_transport_failure_moves_to_tail_once(fail_tail,product_error):
    run,job,backend,calls=native_manual(fail_tail=fail_tail,product_error=product_error)
    result=run(job)
    assert calls==[1,2,3,1]
    assert result['sql_committed']==(2 if fail_tail else 3)
    assert result['deferred']==int(fail_tail)
    assert result['status']==('partial' if fail_tail else 'succeeded')
    assert result['remaining']==0 and len(backend.writes)==result['sql_committed']
    assert any(summary.get('sql_committed')==1 and summary.get('deferred')==1 for summary in backend.summaries)


@pytest.mark.parametrize('product_error',[lambda message:URLError(TimeoutError(10060,message)),ConnectionResetError])
def test_consecutive_transport_failures_stop_with_resumable_queue(product_error):
    run,job,backend,calls=native_manual(all_cards_fail=True,product_error=product_error,duration=600)
    result=run(job)
    assert calls==[1,2,3,1,2] and not backend.writes
    assert result['termination_reason']=='SOURCE_NETWORK_CIRCUIT_OPEN'
    assert result['network_pauses']==3
    assert result['deferred']==3 and result['remaining']==1 and result['resume_available']
    starts=backend.product_started_at
    assert [b-a for a,b in zip(starts[1:],starts[2:])]==pytest.approx([60,120,240])


@pytest.mark.parametrize(('block_card','stop_during_cooldown','duration','expected_reason','expected_calls'),[
    (None,False,600,None,[1,2,3,4,5,6,1,2,3,4,5]),
    (6,False,600,'SOURCE_BLOCKED_HTTP_429',[1,2,3,4,5,6]),
    (None,True,600,'OPERATOR_STOP',[1,2,3,4,5]),
    (None,False,45,'SOURCE_NETWORK_RETRY_OUTSIDE_WINDOW',[1,2,3,4,5]),
])
def test_night_collection_waits_after_five_failed_cards(block_card,stop_during_cooldown,duration,
                                                       expected_reason,expected_calls):
    run,job,backend,calls=native_manual(card_count=6,initial_network_failures=5,
        duration=duration,robots_replies=iter((200,)),block_card=block_card,
        stop_during_cooldown=stop_during_cooldown)
    result=run(job)
    assert calls==['robots',*expected_calls]
    assert result['termination_reason']==expected_reason
    if expected_reason is None:
        assert result['status']=='succeeded' and result['sql_committed']==len(backend.writes)==6
        assert result['canary']['robots_requests']==1 and result['network_pauses']==1
    else:
        assert not backend.writes
    assert any(summary.get('phase')=='NETWORK_COOLDOWN' for summary in backend.summaries)==(duration==600)
    if len(expected_calls)>5:
        assert backend.product_started_at[5]-backend.product_started_at[4]>=60


def test_timeout_then_reset_does_not_stop_remaining_cards():
    run,job,backend,calls=native_manual(second_error=ConnectionResetError)
    result=run(job)
    assert calls==[1,2,3,1,2]
    assert result['status']=='succeeded' and result['deferred']==0
    assert result['sql_committed']==len(backend.writes)==3


def test_manual_redirect_to_unlisted_product_skips_only_old_card():
    run,job,backend,calls=native_manual(robots_replies=iter((200,)),
                                       redirect_target='https://partner-st.ru/item-new/')
    result=run(job)
    assert calls==['robots',1,2,3]
    assert len(result['canary']['product_requests'])==3
    assert result['status']=='partial' and result['termination_reason'] is None
    assert result['sql_committed']==len(backend.writes)==2 and result['remaining']==0
    assert result['deferred']==0 and result['network_pauses']==0
    receipt,=result['quarantined']
    assert receipt['isolation']['category']=='manual_out_of_scope_redirect'
    evidence=Path(result['checkpoint']).parent/'transport-errors'/receipt['redirect_evidence']['file']
    assert manual_redirect_receipt(receipt['origin'],receipt['url'],job.id,evidence)==receipt
    entry=job.settings_snapshot['source_input']['entries'][0]
    class Cursor:
        capture_exists=False
        def execute(self,query,args):
            assert 'supplier_http_captures' in query and 'entity_change_history' in query
            assert args==(receipt['url'],job.id,job.supplier_code,entry['source_product_id'],entry['source_external_id'])
        def fetchone(self):
            return (job.settings_snapshot['source_input'],self.capture_exists,False,False)
    cursor=Cursor()
    sql_backend=SourceBackend.__new__(SourceBackend)
    sql_backend.code=job.supplier_code
    sql_backend.repo=SimpleNamespace(connection=SimpleNamespace(cursor=lambda:nullcontext(cursor)))
    assert sql_backend.probe(entry,receipt)=='NOT_FOUND'
    cursor.capture_exists=True
    assert sql_backend.probe(entry,receipt)=='UNKNOWN'
    evidence.write_bytes(b'{}')
    with pytest.raises(ValueError,match='MANUAL_REDIRECT_PROOF_REQUIRED'):
        manual_redirect_receipt(receipt['origin'],receipt['url'],job.id,evidence)


def test_consecutive_removed_cards_do_not_stop_manual_collection():
    run,job,backend,calls=native_manual(robots_replies=iter((200,)),card_count=6,missing_cards=5)
    result=run(job)
    assert calls==['robots',1,2,3,4,5,6]
    assert result['termination_reason'] is None and result['remaining']==0
    assert result['status']=='partial' and result['sql_committed']==len(backend.writes)==1


def test_manual_redirect_does_not_refetch_a_saved_address_inside_batch():
    run,job,backend,calls=native_manual(robots_replies=iter((200,)),redirect_card=2,
        redirect_target='https://partner-st.ru/item-1/',first_card_failures=0)
    result=run(job)
    assert calls==['robots',1,2,3] and result['termination_reason'] is None
    assert result['sql_committed']==len(backend.writes)==2
    assert result['quarantined'][0]['isolation']['category']=='manual_out_of_scope_redirect'


def test_manual_pause_mid_request_continues_other_cards_after_proof(tmp_path):
    run, job, backend, calls = native_manual(robots_replies=iter((200, 200)), first_card_failures=0, pause_card=2)
    first = run(job)
    assert calls == ['robots', 1, 2]
    assert first['termination_reason'] == 'OPERATOR_STOP'
    assert first['sql_committed'] == first['remaining'] == 1
    receipt, = first['quarantined']
    assert receipt['isolation']['category'] == 'manual_stop_interrupted'
    checkpoint = json.loads(Path(first['checkpoint']).read_bytes())
    snapshot = job.settings_snapshot
    resumed = replace(job, id=2, settings_snapshot={
        **snapshot, 'manual_resume': {'previous_engine':snapshot['source_input']['engine_sha256'],
                                      'checkpoint_sha256':checkpoint['checksum']},
        'crawl_settings':{**snapshot['crawl_settings'], 'collection_mode':'RESUME',
                          'resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    result = run(resumed)
    assert calls == ['robots', 1, 2, 'robots', 3]
    assert result['sql_committed'] == 2 and result['remaining'] == 0
    assert result['quarantined'] == [receipt]
    proof = Path(first['checkpoint']).parent/'transport-errors'/receipt['stop_evidence']['file']
    first_stop = proof.parent.parent/'FIRST_STOP.json'
    first_stop.write_text('{"reason":"HOST_LEASE_LOST","job_id":1}', encoding='utf-8')
    with pytest.raises(ValueError, match='MANUAL_STOP_PROOF_REQUIRED'):
        manual_stop_receipt(receipt['origin'], receipt['url'], 1, proof)


@pytest.mark.parametrize('try_committed',[False,True])
def test_network_only_retry_preserves_saved_cards_and_can_recover(try_committed):
    run,job,backend,calls=native_manual(robots_replies=iter((200,200)),first_card_failures=2)
    first=run(job)
    assert first['remaining']==0 and first['deferred']==1 and first['sql_committed']==2
    snapshot=job.settings_snapshot
    checkpoint=json.loads(Path(first['checkpoint']).read_bytes())
    retry=[entry['url'] for entry in snapshot['source_input']['entries'][:2 if try_committed else 1]]
    resumed=replace(job,id=2,settings_snapshot={
        **snapshot,'manual_resume':{'previous_engine':snapshot['source_input']['engine_sha256'],
                                     'checkpoint_sha256':checkpoint['checksum'],'retry_network_urls':retry},
        'crawl_settings':{**snapshot['crawl_settings'],'collection_mode':'RESUME',
                          'resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    result=run(resumed)
    if try_committed:
        assert calls==['robots',1,2,3,1] and result['sql_committed']==2
        assert result['termination_reason']=='MANUAL_NETWORK_RETRY_SCOPE_CHANGED'
    else:
        assert calls==['robots',1,2,3,1,'robots',1]
        assert result['status']=='succeeded' and result['sql_committed']==len(backend.writes)==3
        assert result['sql_committed_this_job']==1 and result['network_retry_count']==1
        assert result['canary']['product_requests']==[retry[0]]


@pytest.mark.parametrize('target', ['https://example.net/item-new/', 'https://partner-st.ru/item-new/?token=test'])
def test_manual_unapproved_redirect_keeps_hard_stop(target):
    run,job,backend,calls=native_manual(robots_replies=iter((200,)),redirect_target=target)
    result=run(job)
    assert calls==['robots',1] and not backend.writes
    assert result['termination_reason']=='MANUAL_REDIRECT_TARGET_NOT_ALLOWED'
    assert not result['quarantined'] and not result['resume_available']


def test_legacy_scope_archive_requires_the_original_last_dispatched_address(tmp_path):
    from universal_supplier.control_plane.source_runtime import job_dir
    source='kami';job_id=17;url='https://www.stanki.ru/catalog/test-1/'
    origin={'source':source,'job_id':job_id,'manifest_sha256':'a'*64}
    reason='CANARY_URL_OUTSIDE_FROZEN_BATCH'
    folder=job_dir(source,job_id)
    evidence=folder/'transport-errors'/('b'*64+'.json')
    evidence.parent.mkdir(parents=True)
    evidence.write_text(json.dumps({'url':url,'job_id':job_id,'owner_job_id':job_id,
        'input_sha256':origin['manifest_sha256'],'transport_started':True,'http_status':None,
        'diagnostics':{'source':source,'request_scope':'product','response_available':False,'wire_outcome':'UNKNOWN',
                       'original_exception':{'type':'ValueError','module':'builtins','message':reason}}}))
    (folder/'FIRST_STOP.json').write_text(json.dumps({'job_id':job_id,'reason':reason}))
    result={'supplier_code':source,'owner_job_id':job_id,'input_sha256':origin['manifest_sha256'],
            'termination_reason':reason,'resume_uncertain':[url],'canary':{'product_requests':[url]}}
    (folder/'result.json').write_text(json.dumps(result))
    assert manual_redirect_receipt(origin,url,job_id,evidence,legacy=True)['state']=='QUARANTINED'
    result['canary']['product_requests'].append('https://www.stanki.ru/catalog/unrelated/')
    (folder/'result.json').write_text(json.dumps(result))
    with pytest.raises(ValueError,match='MANUAL_REDIRECT_PROOF_REQUIRED'):
        manual_redirect_receipt(origin,url,job_id,evidence,legacy=True)


def test_old_transport_receipt_must_match_terminal_error():
    run,job,_,_=native_manual(fail_tail=True,product_error=ConnectionResetError)
    result=run(job)
    receipt=result['quarantined'][0]
    evidence=Path(result['checkpoint']).parent/'transport-errors'/receipt['timeout_evidence']['file']
    assert manual_transport_receipt(receipt['origin'],receipt['url'],job.id,evidence,
                                    expected_error='ConnectionResetError:no response')==receipt
    with pytest.raises(ValueError,match='DEFERRED_TRANSPORT_PROOF_REQUIRED'):
        manual_transport_receipt(receipt['origin'],receipt['url'],job.id,evidence,
                                 expected_error='TimeoutError:no response')


@pytest.mark.parametrize('wrong_evidence',[None,'certificate','scope','http','wrong_job'])
def test_kami_tls_eof_requires_an_exact_no_response_archive_and_sql_probe(wrong_evidence):
    from universal_supplier.control_plane.source_resume import classify, retryable_network_urls
    from universal_supplier.control_plane.source_runtime import job_dir
    from universal_supplier.control_plane.transport_diagnostics import exception_evidence, safe_message

    source,owner,job_id='kami',17,19
    url='https://www.stanki.ru/catalog/test-1/'
    origin={'source':source,'job_id':owner,'manifest_sha256':'a'*64}
    error=ssl.SSLEOFError(8,'connection closed while reading')
    error.reason='UNEXPECTED_EOF_WHILE_READING'
    if wrong_evidence=='certificate':
        error=ssl.SSLCertVerificationError(1,'certificate verify failed')
    outer=URLError(error)
    evidence=job_dir(source,job_id)/'transport-errors'/('b'*64+'.json')
    evidence.parent.mkdir(parents=True)
    record={'url':url,'job_id':18 if wrong_evidence=='wrong_job' else job_id,'owner_job_id':owner,
            'input_sha256':origin['manifest_sha256'],'transport_started':True,
            'http_status':200 if wrong_evidence=='http' else None,
            'diagnostics':{**exception_evidence(outer),'source':source,
            'request_scope':'robots' if wrong_evidence=='scope' else 'product',
            'response_available':False,'wire_outcome':'UNKNOWN'}}
    evidence.write_text(json.dumps(record),encoding='utf-8')
    if wrong_evidence:
        with pytest.raises(ValueError):
            manual_transport_receipt(origin,url,job_id,evidence,expected_error='URLError:'+safe_message(outer))
        return
    receipt=manual_transport_receipt(origin,url,job_id,evidence,expected_error='URLError:'+safe_message(outer))
    assert receipt['isolation']['category']=='manual_tls_eof'
    def classified(probe):
        return classify([{'url':url}],[receipt],lambda *args:probe,source=source,parent_job_id=owner,
                        manifest_sha256=origin['manifest_sha256'])[0]['state']
    assert classified('NOT_FOUND')=='QUARANTINED'
    assert classified('COMMITTED')==classified('UNKNOWN')=='UNCERTAIN'
    assert not retryable_network_urls({'terminal':{url:{'state':'QUARANTINED','receipt':receipt}}})


def test_kami_fetcher_classifies_tls_eof_without_retry():
    from universal_supplier.control_plane.host_fetch import BoundedHostFetcher
    source='kami';base='https://www.stanki.ru'
    rate=approved_policy(source,base,{'max_attempts':3,'max_concurrency':1})
    calls=[]
    class Client:
        def get_validated(self,url,**kwargs):
            self.before_request(url)
            calls.append(url)
            error=ssl.SSLEOFError(8,'connection closed while reading')
            error.reason='UNEXPECTED_EOF_WHILE_READING'
            raise URLError(error)
    runner=BoundedHostFetcher('www.stanki.ru',rate,Client,validator=lambda _:None,robots_allowed=lambda _:True,
                            robots_delay_seconds=0,lease_acquired=True,clock=Clock(),source=source,
                            approval=approval_for(base),manual_product_attempts=True)
    url=base+'/catalog/test-1/'
    outcome,=runner.fetch([url],limit=1,seconds=60)
    assert calls==[url] and outcome.disconnect_refusal and outcome.response is None
    assert outcome.error=='RemoteDisconnectRefusal:TLS_EOF_UNKNOWN'
    assert runner.metrics.network_errors==1 and runner.metrics.retry_count==0 and not runner.stop_reason


def test_other_urlerror_keeps_unknown_result_blocked():
    run,job,backend,calls=native_manual(product_error=URLError)
    result=run(job)
    assert calls==[1] and not backend.writes
    assert result['termination_reason']=='SOURCE_CARD_HTTP_FAILED'
    assert result['deferred']==0 and not result['resume_available']


@pytest.mark.parametrize('code',('ltt_msk','ltt_sib'))
def test_ltt_unicode_wire_form_stays_inside_approved_manual_batch(code):
    from test_source_jobs import saved_case
    from universal_supplier.control_plane.source_jobs import parse_existing
    from universal_supplier.http_capture import public_transport_url

    body,url,charset,_=saved_case(code)
    url=url.rstrip('/')+'-проверка/'
    wire=public_transport_url(url)
    assert wire != url
    base='https://'+urlsplit(url).hostname
    rate=approved_policy(code,base,{'max_concurrency':1})
    ext=parse_existing(code,body.decode(charset),url).site_internal_id
    supplier=(code,code,base+'/',True,{'host_policy_approval':approval_for(base)},rate.snapshot())
    snapshot=build_preview(1,supplier,[(1,ext,url,1)],CFG,1)['snapshot']
    snapshot['manual_collection']={'robots_attempts':1}
    job=CrawlJob(1,1,code,code,base+'/','manual','full','running',datetime.now(timezone.utc),snapshot)
    calls=[];backend=Backend()
    class Client:
        def get_validated(self,target,*,validator,timeout_seconds):
            validator(target)
            validator(public_transport_url(target))
            self.before_request(target)
            calls.append(target)
            if target.endswith('/robots.txt'):
                return HttpResponse(target,200,'text/plain',b'User-agent: *\nAllow: /\n')
            return HttpResponse(wire,200,'text/html; charset='+charset,body)
    lease=LEASED_HOST.set(job.domain);manual=MANUAL_EXECUTION.set(job.id)
    try:
        result=run_source(job,CFG,backend_factory=lambda *args:backend,client_factory=Client,clock=Clock())
    finally:
        MANUAL_EXECUTION.reset(manual);LEASED_HOST.reset(lease)
    assert calls==[base+'/robots.txt',url]
    assert result['sql_committed']==1 and result['canary']['product_requests']==[url]


def test_optimum_public_license_replays_only_verified_saved_capture():
    import hashlib
    from universal_supplier.commercial_persistence import OPTIMUM_PUBLIC_LICENSE_URL, _safe_url
    from universal_supplier.control_plane.manual_saved_recovery import optimum_archive_receipt
    from universal_supplier.control_plane.source_runtime import job_dir

    url=OPTIMUM_PUBLIC_LICENSE_URL
    assert _safe_url(url)==url
    for unsafe in (url+'?token=example',url+'#token',url.replace('optimum.su','example.invalid'),
                   url.replace('https://','https://anyone@')):
        with pytest.raises(ValueError):
            _safe_url(unsafe)
    origin={'source':'optimum','job_id':17,'manifest_sha256':'a'*64}
    payload=b'<h1>Public licence</h1>'
    evidence_sha=hashlib.sha256(payload).hexdigest()
    folder=job_dir('optimum',17)
    body=folder/'captures'/(evidence_sha+'.html')
    record_path=folder/'responses'/('b'*64+'.json')
    body.parent.mkdir(parents=True)
    record_path.parent.mkdir()
    body.write_bytes(payload)
    record_path.write_text(json.dumps(dict(origin_job_id=17,input_sha256=origin['manifest_sha256'],
        url=url,final_url=url,http_status=200,error=None,redirects=[],response_sha256=evidence_sha,
        evidence_sha256=evidence_sha,file=str(body),content_type='text/html',
        observed_at='2026-11-26T00:00:00+00:00')),encoding='utf-8')
    receipt=optimum_archive_receipt(dict(origin=origin,url=url),record_path)
    assert receipt['state']=='HTTP_CAPTURED_NOT_COMMITTED'
    assert receipt['original_capture_metadata']['observed_at']=='2026-11-26T00:00:00+00:00'
    body.write_bytes(b'tampered')
    with pytest.raises(ValueError,match='MANUAL_SAVED_CAPTURE_NOT_PROVEN'):
        optimum_archive_receipt(dict(origin=origin,url=url),record_path)


def test_bounded_manual_disconnect_goes_to_tail_before_retry():
    run,job,backend,calls=native_manual(fail_tail=True,product_error=RemoteDisconnected,robots_replies=iter((200,)))
    result=run(job)
    assert calls==['robots',1,2,3,1]
    assert result['status']=='partial' and result['sql_committed']==2
    assert result['remaining']==0 and result['deferred']==1
    assert len(result['canary']['product_requests'])==4
    details=result['quarantined'][0]['isolation']
    assert details['category']=='manual_remote_disconnect'


def test_manual_resume_skips_commit_and_retries_tail_in_saved_order():
    run,job,backend,calls=native_manual(end_window=True)
    first=run(job)
    assert calls==[1,2] and first['sql_committed']==1 and first['resume_available']
    assert first['termination_reason']=='MAX_RUN_DURATION_EXCEEDED'
    snapshot=job.settings_snapshot
    checkpoint=json.loads(Path(first['checkpoint']).read_bytes())
    resumed=replace(job,id=2,settings_snapshot={
        **snapshot,'manual_resume':{'previous_engine':snapshot['source_input']['engine_sha256'],
                                     'checkpoint_sha256':checkpoint['checksum']},
        'crawl_settings':{**snapshot['crawl_settings'],'collection_mode':'RESUME',
                          'resume_owner_job_id':1,'resume_input_sha256':first['input_sha256']}})
    second=run(resumed)
    assert calls==[1,2,3,1]
    assert second['sql_committed']==3 and second['sql_committed_this_job']==2
    assert len(backend.writes)==3 and second['deferred']==0


def test_manual_rate_limit_still_stops_with_checkpoint():
    run,job,backend,calls=native_manual(block=True)
    result=run(job)
    assert calls==[1,2] and result['termination_reason']=='SOURCE_BLOCKED_HTTP_429'
    assert result['sql_committed']==0 and result['remaining']>=1


def test_manual_robots_timeout_retries_before_any_cards_with_frozen_budget():
    run,job,backend,calls=native_manual(robots_replies=iter((None,200)))
    result=run(job)
    assert calls==['robots','robots',1,2,3,1]
    assert result['status']=='succeeded' and result['sql_committed']==3
    assert result['canary']['robots_requests']==2
    phases=[summary['phase'] for summary in backend.summaries if 'phase' in summary]
    assert phases[:3]==['PREPARING','ROBOTS','PRODUCTS']


@pytest.mark.parametrize(('responses','limit','attempts','reason'),[
    ((None,None),2,2,'SOURCE_ROBOTS_UNAVAILABLE'),
    ((None,),1,1,'SOURCE_ROBOTS_UNAVAILABLE'),
    ((403,),2,1,'SOURCE_BLOCKED_HTTP_403'),
    ((429,),2,1,'SOURCE_BLOCKED_HTTP_429'),
])
def test_manual_robots_failures_stop_before_products(responses,limit,attempts,reason):
    run,job,backend,calls=native_manual(robots_replies=iter(responses),robots_limit=limit)
    result=run(job)
    assert calls==['robots']*attempts
    assert result['canary']['robots_requests']==attempts
    assert result['status']=='partial' and result['termination_reason']==reason
    assert result['sql_committed']==0 and result['remaining']==3 and not backend.writes
    if responses[0] is None:
        error=result['transport_errors'][0]['original_exception']
        assert error['type']=='URLError' and error['reason']['type']=='TimeoutError'


def test_night_budget_is_bound_to_one_job_and_non_admin_cannot_extend(monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    db=DB('kami');queue=db.queue()
    request=ManualRequest(AuthUser(1,'qa','QA','ADMIN'),KEY,'3','5',0,duration_hours=15)
    queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,manual_request=request)
    snap=db.snapshots[0]
    assert snap['crawl_settings']['max_run_duration_seconds']==15*3600
    assert snap['source_input']['canary']['max_run_duration_seconds']==15*3600
    assert verify(snap,KEY)['matching_apply'] is False
    assert not any('UPDATE suppliers SET rate_settings' in sql for sql,_ in db.sql)
    operator=ManualRequest(AuthUser(2,'qa','QA','OPERATOR'),KEY,'3','5',0,duration_hours=15)
    with pytest.raises(PermissionError):
        queue.enqueue(1,trigger_type='manual',crawl_type='full',source_mode='REFRESH',source_limit=5,manual_request=operator)


def test_continue_request_launches_only_new_child(tmp_path,monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch);calls=[];launches=[]
    def enqueue(*args,**options):
        calls.append(options)
        return EnqueueResult(45,'queued')
    c.app.state.queue.enqueue=enqueue
    monkeypatch.setattr('universal_supplier.control_plane.manual_collection.launch',lambda jid,**k:launches.append(jid))
    token=csrf(c.get('/suppliers/1'))
    result=c.post('/suppliers/1/manual-start',data={'csrf_token':token,'collection_mode':'RESUME',
        'resume_job_id':'44'},follow_redirects=False)
    assert result.status_code==303 and calls[0]['resume_job_id']==44
    assert calls[0]['source_limit'] is None and launches==[45]
    assert 'job_id=45' in result.headers['location']


def test_job_progress_polls_and_api_omits_private_metadata(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    monkeypatch.setattr(type(store),'job_progress',lambda self,jid:{'job_status':'running','run_id':100,
        'total':10,'succeeded':2,'deferred':1,'private':'do not render'},raising=False)
    page=c.get('/runs?job_id=44&enqueue_status=queued')
    assert 'Задание №44' in page.text and '/api/jobs/44/progress' in page.text
    assert 'collection-progress.js' in page.text
    response=c.get('/api/jobs/44/progress')
    assert response.json()=={'job_status':'running','run_id':100,'total':10,'succeeded':2,'deferred':1}
    assert response.headers['cache-control']=='no-store'


def test_runs_shows_selected_job_and_other_active_run_together(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch)
    monkeypatch.setattr(store,'runs',lambda: [
        {'id':220,'entry_type':'job','supplier_name':'Векпром','status':'running'},
        {'id':110,'entry_type':'run','supplier_name':'КАМИ','status':'running',
         'metadata':{'source_runtime':{'remaining':4816},'progress':{'total':5264,'succeeded':448}}},
    ])
    page = c.get('/runs?job_id=220')
    assert page.status_code==200
    assert page.text.count('data-collection-progress="/api/jobs/220/progress"') == 1
    job_row = page.text.split('id="job-220"',1)[1].split('</tr>',1)[0]
    assert 'data-collection-progress="/api/jobs/220/progress"' in job_row and 'data-run-link' in job_row
    assert 'data-collection-progress="/api/runs/110/progress"' in page.text


def test_partial_collection_separates_network_and_matching_for_operator(tmp_path,monkeypatch):
    c,store=client(tmp_path,monkeypatch,role='OPERATOR')
    receipts=[
        {'url':'https://example.test/timeout?token=private-sample',
         'isolation':{'category':'manual_read_timeout','reason':'READ_TIMEOUT_UNKNOWN'}},
        {'url':'https://example.test/conflict',
         'isolation':{'category':'matching','reason':'SOURCE_UPDATE_SEMANTIC_CONFLICT:EXISTING_LINK_CONFLICT'}},
        {'url':'javascript:alert(1)','isolation':{'category':'http','reason':'HTTP_410'}},
    ]
    progress={'total':9,'succeeded':6,'deferred':1,'durable_progress':True}
    details={'id':105,'supplier_name':'КАМИ','status':'partial','supplier_id':5812,'manual_job_id':214,
             'metadata':{'progress':progress,'source_runtime':{'remaining':0,'quarantined':receipts}},
             'errors':[]}
    monkeypatch.setattr(store,'run',lambda run_id: details)
    monkeypatch.setattr(type(store),'job_progress',lambda self,jid:{**progress,'job_status':'partial',
        'run_id':105,'matching_conflicts':1,'other_card_issues':1,'quarantined':receipts},raising=False)
    page=c.get('/runs/105')
    assert page.status_code==200 and 'Очередь обработана' in page.text
    assert 'Отложено из-за сети: 1' in page.text and 'Конфликты сопоставления: 1' in page.text
    assert 'Прочие проблемы с карточками: 1' in page.text
    assert 'Прежняя связь сохранена' in page.text
    assert 'Карточка недоступна по сохранённому адресу' in page.text
    assert 'EXISTING_LINK_CONFLICT' not in page.text and 'Продолжить сбор' not in page.text
    assert 'href="https://example.test/timeout"' in page.text
    assert 'private-sample' not in page.text and 'javascript:alert' not in page.text
    response=c.get('/api/jobs/214/progress')
    assert response.status_code==200
    payload=response.json()
    assert payload['matching_conflicts']==1 and payload['other_card_issues']==1
    assert 'quarantined' not in payload
    assert c.post('/api/jobs/214/progress').status_code==403


def test_drained_manual_collection_offers_a_separate_retry_action(tmp_path,monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch)
    details={'id':105,'supplier_name':'КАМИ','status':'partial','supplier_id':5812,'manual_job_id':214,
             'metadata':{'source_runtime':{'remaining':0,'deferred':2}},'errors':[]}
    monkeypatch.setattr(store,'run',lambda run_id:details)
    checks=[]
    def check(sid,jid,approval):
        checks.append((sid,jid,approval.retry_network))
    monkeypatch.setattr(c.app.state.queue,'manual_resume_status',check,raising=False)
    page=c.get('/runs/105')
    assert 'Повторить сетевые ошибки' in page.text and 'name="retry_network" value="yes"' in page.text
    assert 'Продолжить сбор' not in page.text
    assert checks==[(5812,214,True)]
    requests=[]
    def enqueue(*args,**kwargs):
        requests.append(kwargs)
        return EnqueueResult(215,'queued')
    c.app.state.queue.enqueue=enqueue
    monkeypatch.setattr('universal_supplier.control_plane.manual_collection.launch',lambda *args,**kwargs:None)
    posted=c.post('/suppliers/5812/manual-start',data={'csrf_token':csrf(page),'collection_mode':'RESUME',
        'resume_job_id':'214','retry_network':'yes'},follow_redirects=False)
    assert posted.status_code==303 and 'job_id=215' in posted.headers['location']
    assert len(requests)==1 and requests[0]['manual_request'].retry_network
    assert requests[0]['source_mode']=='RESUME' and requests[0]['resume_job_id']==214
    details['metadata']['source_runtime']['remaining']=3
    page=c.get('/runs/105')
    assert 'Продолжить сбор' in page.text and 'Повторить сетевые ошибки' not in page.text
    assert checks[-1]==(5812,214,False)
    details['metadata']['source_runtime']['resume_uncertain']=True
    assert 'Продолжить сбор' in c.get('/runs/105').text
    def refuse(*args):
        raise ValueError('UNCERTAIN_OR_FAILED_RESUME')
    monkeypatch.setattr(c.app.state.queue,'manual_resume_status',refuse)
    page=c.get('/runs/105')
    assert 'Продолжить сбор' not in page.text and 'Нужна сверка' in page.text
    details['latest_supplier_job_id']=217
    page=c.get('/runs/105')
    assert 'Продолжить сбор' not in page.text and '/runs?job_id=217' in page.text


def test_waiting_manual_job_recovery_dispatches_existing_job_only(tmp_path,monkeypatch):
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch)
    rows=store.suppliers
    monkeypatch.setattr(store,'suppliers',lambda:[{**row,'manual_job_queued':True,
        'job_status':'queued','job_id':176,'manual_job_actor_id':1} for row in rows()])
    queue=c.app.state.queue
    requests=[];launched=[]
    def recover(supplier,job,actor,key,*,cancel):
        requests.append((supplier,job,actor.id,cancel))
    monkeypatch.setattr(queue,'recover_queued_manual_job',recover,raising=False)
    monkeypatch.setattr('universal_supplier.control_plane.manual_collection.launch',
                        lambda jid,**kwargs:launched.append(jid))
    page=c.get('/suppliers/1')
    assert 'Запустить ожидающее задание' in page.text and 'Начать парсинг' not in page.text
    path='/suppliers/1/recover-queued-job'
    data={'job_id':'176','action':'launch'}
    assert c.post(path,data=data,follow_redirects=False).status_code==403
    data['csrf_token']=csrf(page)
    response=c.post(path,data=data,follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/runs?job_id=176'
    assert requests==[(1,176,1,False)] and launched==[176] and not queue.jobs
    data['action']='cancel'
    assert c.post(path,data=data,follow_redirects=False).status_code==303
    assert requests[-1]==(1,176,1,True) and launched==[176]


def test_expired_job_action_is_scoped_and_requires_csrf(tmp_path,monkeypatch):
    from universal_supplier.control_plane.queue import InMemoryJobQueue
    monkeypatch.setenv('STERBRUST_MANUAL_COLLECTION_ENABLED','YES')
    c,store=client(tmp_path,monkeypatch)
    supplier_rows=store.suppliers
    monkeypatch.setattr(store,'suppliers',lambda:[{**row,'manual_job_expired':True,
        'has_running_job':True,'job_id':176} for row in supplier_rows()])
    requests=[]
    monkeypatch.setattr(InMemoryJobQueue,'close_expired_manual_job',
        lambda self,supplier,job,actor_id:requests.append((supplier,job,actor_id)),raising=False)
    headers={'origin':'https://testserver'}
    endpoint='/suppliers/1/close-expired-job'
    assert c.post(endpoint,headers=headers,data={'job_id':'176'},follow_redirects=False).status_code==403
    page=c.get('/suppliers/1')
    assert 'Нет связи с исполнителем' in page.text and endpoint in page.text
    assert 'Начать парсинг' not in page.text and 'Сбор выполняется' not in page.text
    token=csrf(page)
    response=c.post(endpoint,headers=headers,data={'job_id':'176','csrf_token':token},follow_redirects=False)
    assert response.status_code==303 and response.headers['location']=='/suppliers/1'
    assert requests==[(1,176,1)]


@pytest.mark.parametrize(('expired','lock_acquired','run_owner','closes'),[
    (True,True,'176',True), (False,True,'176',False),
    (True,False,'176',False), (True,True,'177',False),
])
def test_close_expired_job_requires_expiry_released_lock_and_same_owner(expired,lock_acquired,run_owner,closes):
    statements=[]

    class Connection:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def transaction(self):return nullcontext()
        def cursor(self):return self
        def execute(self,sql,args=None):
            self.sql=sql
            statements.append((sql,args))
        def fetchone(self):
            if 'pg_control_system' in self.sql:
                from universal_supplier.control_plane.source_canary import TARGET
                return (TARGET['dbname'],TARGET['system_identifier'],'17.11',TARGET['pgdata'],TARGET['host'],TARGET['port'])
            if 'FOR UPDATE OF j' in self.sql:
                return (67,None,None,'beka-mak.su','https://beka-mak.su/') if expired else None
            if 'pg_try_advisory_xact_lock' in self.sql:return (lock_acquired,)
            if 'FROM crawl_runs' in self.sql:return ('running',run_owner)
            raise AssertionError(self.sql)

    from universal_supplier.control_plane.queue import PostgresJobQueue
    queue=PostgresJobQueue(CFG)
    queue._connect=Connection
    if closes:
        queue.close_expired_manual_job(11,176,actor_id=1)
        assert [args[-1] for sql,args in statements if sql.startswith('UPDATE crawl_')]==[67,176]
        assert sum(sql.startswith('INSERT INTO audit_events') for sql,_ in statements)==1
    else:
        with pytest.raises((ValueError,PermissionError)):
            queue.close_expired_manual_job(11,176,actor_id=1)
        assert not any(sql.startswith(('UPDATE ', 'INSERT ')) for sql,_ in statements)
    assert any('j.id=%s AND j.supplier_id=%s' in sql and args==(176,11) for sql,args in statements)
