"""Prepared pool tests use in-memory responses only, never real HTTP/SQL."""
from dataclasses import replace
import json,threading
import pytest
from universal_supplier.control_plane.host_fetch import compile_policy,policy_digest,BoundedHostFetcher
from universal_supplier.control_plane.rate_control import DomainRatePolicy
from universal_supplier.http_capture import HttpResponse

class Clock:
    def __init__(self):self.value=0.;self.lock=threading.RLock()
    def monotonic(self):
        with self.lock:return self.value
    def sleep(self,seconds):
        with self.lock:self.value+=seconds


def fetcher(statuses=(200,),*,rate=None,robots=lambda u:True,stop=None,approval=None):
    clock=Clock();calls=[];codes=iter(statuses)
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            validator(url);self.before_request(url);calls.append((url,clock.monotonic(),timeout_seconds))
            return HttpResponse(url,next(codes), 'text/html',b'<h1>machine</h1>',headers={'Retry-After':'12'})
    rate=rate or compile_policy('vekprom','https://vekprom.ru/',{'jitter_max_seconds':0})
    f=BoundedHostFetcher('vekprom.ru',rate,Client,validator=lambda u:None,robots_allowed=robots,robots_delay_seconds=0,lease_acquired=True,clock=clock,stop_requested=stop,source='vekprom',approval=approval)
    return f,calls


def test_conservative_default_and_unauthorized_speed_fail_closed():
    r=compile_policy('vekprom','https://vekprom.ru/');assert r.max_concurrency==1 and r.min_request_interval_seconds==20
    with pytest.raises(PermissionError):compile_policy('vekprom','https://vekprom.ru/',{'min_request_interval_seconds':1})

@pytest.mark.parametrize('field,value',[('min_request_interval_seconds',float('nan')),('min_request_interval_seconds',0),('max_concurrency',1.5),('max_attempts',True),('respect_retry_after',False)])
def test_invalid_policy_is_rejected(field,value):
    with pytest.raises((ValueError,PermissionError)):compile_policy('vekprom','https://vekprom.ru/',{field:value})


def test_exact_host_policy_digest_and_rate_values_must_match(monkeypatch):
    raw={'max_concurrency':2,'min_request_interval_seconds':1.,'max_attempts':1,'request_timeout_seconds':20.,'max_run_duration_seconds':300.,'jitter_max_seconds':0}
    r=DomainRatePolicy.from_mapping(raw);approval={'host':'vekprom.ru','reference':'OFFLINE_TEST_ONLY'}
    token=policy_digest('vekprom','vekprom.ru',r,approval['reference']);monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES',json.dumps({'vekprom.ru':token}))
    out=compile_policy('vekprom','https://vekprom.ru/',raw,approval=approval);assert out==r
    with pytest.raises(PermissionError):compile_policy('vekprom','https://vekprom.ru/',{**raw,'max_concurrency':3},approval=approval)
    f,calls=fetcher((200,200,200),rate=r,approval=approval)
    outcomes=list(f.fetch(['https://vekprom.ru/'+str(i) for i in range(3)],limit=3,seconds=300))
    assert len(outcomes)==3 and all(x.response.status_code==200 for x in outcomes)
    starts=sorted(x[1] for x in calls);assert all(b-a>=1 for a,b in zip(starts,starts[1:]))

@pytest.mark.parametrize('status',[403,429])
def test_blocked_host_stops_without_retry_or_next_url(status):
    r=compile_policy('vekprom','https://vekprom.ru/',{'max_attempts':3,'jitter_max_seconds':0})
    f,calls=fetcher((status,),rate=r)
    rows=list(f.fetch(['https://vekprom.ru/1','https://vekprom.ru/2'],limit=2,seconds=300))
    assert len(calls)==1 and rows[0].response.status_code==status and rows[0].attempts==1
    assert f.stop_reason=='SOURCE_BLOCKED_HTTP_'+str(status) and f.metrics.retry_count==0
    assert rows[0].retry_after_seconds==12


def test_5xx_retry_is_bounded_by_existing_resilience_policy():
    r=compile_policy('vekprom','https://vekprom.ru/',{'max_attempts':2,'jitter_max_seconds':0})
    f,calls=fetcher((500,200),rate=r);rows=list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=300))
    assert len(calls)==2 and rows[0].attempts==2 and f.metrics.retry_count==1
    assert calls[1][1]-calls[0][1]>=20


def test_robots_and_url_limits_fail_before_client():
    f,calls=fetcher()
    with pytest.raises(ValueError,match='HARD_URL_BUDGET'):list(f.fetch(['https://vekprom.ru/1','https://vekprom.ru/2'],limit=1,seconds=300))
    assert not calls
    f,calls=fetcher(robots=lambda u:False)
    with pytest.raises(PermissionError,match='ROBOTS'):list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=300))
    assert not calls


def test_stop_before_start_does_not_send():
    f,calls=fetcher(stop=lambda:True);assert list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=300))==[] and not calls


def test_receipt_failure_stops_before_next_http():
    f,calls=fetcher((200,200))
    def broken(outcome):raise OSError('archive unavailable')
    f.receipt_sink=broken
    outcomes=list(f.fetch(['https://vekprom.ru/1','https://vekprom.ru/2'],limit=2,seconds=300))
    assert len(calls)==1 and len(outcomes)==1
    assert f.stop_reason=='CAPTURE_ARCHIVE_FAILED' and 'archive unavailable' in outcomes[0].error
    assert outcomes[0].response.status_code==200 and outcomes[0].observed_at is not None


def test_cross_host_redirect_is_rejected_before_target_request():
    f,calls=fetcher()
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            self.before_request(url)
            validator('https://foreign.example/private')
            raise AssertionError('must not send')
    f.client_factory=Client
    rows=list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=300))
    assert len(rows)==1 and 'CROSS_HOST' in rows[0].error


def test_additional_redirect_cannot_exceed_unique_url_budget():
    f,calls=fetcher()
    class Client:
        def get_validated(self,url,*,validator,timeout_seconds):
            self.before_request(url);self.before_request('https://vekprom.ru/redirect')
            raise AssertionError('must not send redirect')
    f.client_factory=Client
    rows=list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=300))
    assert f.stop_reason=='HARD_HTTP_URL_BUDGET' and len(f._target_urls)==1
    assert 'HARD_HTTP_URL_BUDGET' in rows[0].error


@pytest.mark.parametrize('seconds',[True,float('nan'),float('inf'),0,-1,3601,'20'])
def test_invalid_time_budget_fails_before_http(seconds):
    f,calls=fetcher()
    with pytest.raises(ValueError,match='TIME_BUDGET'):list(f.fetch(['https://vekprom.ru/1'],limit=1,seconds=seconds))
    assert not calls


def test_lease_and_robots_delay_are_mandatory():
    r=compile_policy('vekprom','https://vekprom.ru/')
    kw=dict(validator=lambda u:None,robots_allowed=lambda u:True,source='vekprom')
    with pytest.raises(PermissionError,match='LEASE'):BoundedHostFetcher('vekprom.ru',r,object,**kw)
    with pytest.raises(PermissionError,match='DELAY'):BoundedHostFetcher('vekprom.ru',r,object,lease_acquired=True,**kw)
    with pytest.raises(PermissionError,match='SLOWER'):BoundedHostFetcher('vekprom.ru',r,object,lease_acquired=True,robots_delay_seconds=30,**kw)
