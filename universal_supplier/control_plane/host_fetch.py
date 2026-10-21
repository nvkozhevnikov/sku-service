"""Prepared per-host bounded fetch component. No scheduler or source activation.

Reuses DomainRatePolicy/Limiter/resilience. The caller must supply the existing
host lease, URL allowlist, robots decision and HTTP client. SQL is never called.
"""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import dataclass
from datetime import datetime,timezone
from urllib.parse import urlsplit
import hashlib,json,math,os,threading
from .rate_control import (DomainRatePolicy,DomainRateLimiter,RequestMetrics,
    CircuitBreaker,SystemClock,RequestAborted,request_with_resilience,retry_after_seconds)
from universal_supplier.http_capture import has_blocking_interstitial


def host_key(url):return (urlsplit(url).hostname or '').lower().removeprefix('www.')

def policy_digest(source,host,rate,approval):
    return hashlib.sha256(json.dumps({'source':source,'host':host,'rate':rate.snapshot(),'approval_reference':approval},sort_keys=True,separators=(',',':')).encode()).hexdigest()

def compile_policy(source,url,settings=None,*,approval=None,approved_digests=None):
    from .rate_control import PARTNER_ST_DEFAULT_RATE_POLICY,OPTIMUM_DEFAULT_RATE_POLICY
    native={'partner_st':PARTNER_ST_DEFAULT_RATE_POLICY,'optimum':OPTIMUM_DEFAULT_RATE_POLICY}.get(source)
    required={'max_concurrency','min_request_interval_seconds','request_timeout_seconds','jitter_min_seconds','jitter_max_seconds','max_attempts','max_run_duration_seconds'}
    if native is None and not required.issubset(settings or {}):raise PermissionError('EXPLICIT_HOST_PROFILE_REQUIRED')
    defaults=native.snapshot() if native else {}
    values={**defaults,**(settings or {})}
    rate=DomainRatePolicy.from_mapping(values)
    for name,value in rate.snapshot().items():
        if name=='respect_retry_after':continue
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):raise ValueError('INVALID_HOST_RATE_NUMBER')
    if rate.min_request_interval_seconds<=0 or rate.max_concurrency>8 or rate.max_attempts>3:raise ValueError('HOST_RATE_OUTSIDE_BOUNDED_PROFILE')
    if rate.respect_retry_after is not True:raise ValueError('RETRY_AFTER_PROTECTION_REQUIRED')
    for name in ('max_concurrency','max_attempts','consecutive_error_threshold'):
        if type(getattr(rate,name)) is not int:raise ValueError('HOST_RATE_INTEGER_REQUIRED')
    host=host_key(url)
    if not host:raise ValueError('HOST_REQUIRED')
    requires_approval=(rate.max_concurrency>native.max_concurrency or rate.min_request_interval_seconds<native.min_request_interval_seconds) if native else True
    if requires_approval:
        approval=approval or {}
        if approval.get('host')!=host or not approval.get('reference'):raise PermissionError('HOST_PROFILE_NOT_APPROVED')
        approved_digests=approved_digests if approved_digests is not None else json.loads(os.environ.get('STERBRUST_APPROVED_HOST_POLICIES','{}'))
        if approved_digests.get(host)!=policy_digest(source,host,rate,approval['reference']):raise PermissionError('HOST_PROFILE_DIGEST_NOT_APPROVED')
    return rate

@dataclass(frozen=True)
class FetchOutcome:
    url:str
    response:object=None
    observed_at:object=None
    attempts:int=0
    seconds:float=0.0
    error:str|None=None
    retry_after_seconds:float|None=None
    transport_started:bool|None=None

class BoundedHostFetcher:
    def __init__(self,host,rate,client_factory,*,validator,robots_allowed,
                 lease_acquired=False,clock=None,stop_requested=None,robots_delay_seconds=None,source=None,approval=None,receipt_sink=None,defer_first_start=False):
        if not source:raise ValueError('SOURCE_REQUIRED')
        rate=compile_policy(source,'https://'+host,rate.snapshot(),approval=approval)
        if not lease_acquired:raise PermissionError('HOST_LEASE_REQUIRED')
        if not callable(robots_allowed):raise PermissionError('ROBOTS_DECISION_REQUIRED')
        if isinstance(robots_delay_seconds,bool) or not isinstance(robots_delay_seconds,(int,float)) or not math.isfinite(robots_delay_seconds) or robots_delay_seconds<0:raise PermissionError('ROBOTS_DELAY_DECISION_REQUIRED')
        if robots_delay_seconds>rate.min_request_interval_seconds:raise PermissionError('ROBOTS_REQUIRES_SLOWER_PROFILE')
        self._target_urls=set();self._url_budget=0
        self.host=host_key('https://'+host);self.rate=rate;self.client_factory=client_factory
        self.validator=validator;self.robots_allowed=robots_allowed;self.clock=clock or SystemClock()
        self.stop_requested=stop_requested or (lambda:False);self.stopped=threading.Event()
        self.metrics=RequestMetrics();self.limiter=DomainRateLimiter(rate,clock=self.clock)
        if defer_first_start:
            # A newly acquired session lease cannot know the previous job's
            # last start. Use the SAME gate with a conservative initial delay.
            self.limiter._next_start=self.clock.monotonic()+rate.min_request_interval_seconds
        self.breaker=CircuitBreaker(rate,clock=self.clock,metrics=self.metrics);self.stop_reason=None
        self._lock=threading.Lock();self.outcomes=[];self.receipt_sink=receipt_sink
        self.stop_sink=None
        self.request_start_guard=None
    def stop(self,reason='OPERATOR_STOP'):
        with self._lock:
            first=self.stop_reason is None
            if first:self.stop_reason=reason
            self.stopped.set()
        if first and self.stop_sink:self.stop_sink(reason)
    def aborted(self):
        reason=self.stop_requested()
        if reason:self.stop(reason if isinstance(reason,str) else 'OPERATOR_STOP')
        return self.stopped.is_set()
    def _validate(self,url):
        self.validator(url)
        if host_key(url)!=self.host:raise ValueError('CROSS_HOST_REDIRECT_FORBIDDEN')
        if not self.robots_allowed(url):raise PermissionError('ROBOTS_DISALLOW')
    def _fetch(self,url,deadline):
        started=self.clock.monotonic();client=None;response=None;attempts=0;observed=None;transport_started=False
        stop_statuses={403,429}
        try:
            self._validate(url);client=self.client_factory();callback=getattr(client,'before_request',None)
            initial=[True]
            def before_request(target):
                self._validate(target)
                with self._lock:
                    exceeded=target not in self._target_urls and len(self._target_urls)>=self._url_budget
                    if not exceeded:self._target_urls.add(target)
                if exceeded:
                    self.stop('HARD_HTTP_URL_BUDGET');raise ValueError('HARD_HTTP_URL_BUDGET')
                if self.aborted():raise RequestAborted('HOST_STOPPED')
                if initial[0]:initial[0]=False
                else:self.limiter.wait(self.metrics,deadline=deadline)
                if self.aborted():raise RequestAborted('HOST_STOPPED')
                if self.request_start_guard:
                    try:self.request_start_guard(target)
                    except RequestAborted as error:
                        self.stop(str(error));raise
                if callback:callback(target)
            # The existing public client calls this for each redirect too.
            client.before_request=before_request
            def send():
                nonlocal transport_started
                initial[0]=True
                # Once the transport is entered, absence of a response is
                # uncertain. A limiter deadline BEFORE send proves no GET.
                transport_started=True
                response=client.get_validated(url,validator=self._validate,timeout_seconds=min(self.rate.request_timeout_seconds,max(.001,deadline-self.clock.monotonic())))
                self._validate(response.final_url)
                for target in response.redirects:self._validate(target)
                if response.status_code in (403,429):self.stop('SOURCE_BLOCKED_HTTP_'+str(response.status_code))
                elif response.status_code==503 and (retry_after_seconds(response.headers or {}) or 0)>0:
                    # Respect a temporary host refusal even on the final
                    # attempt. A later job needs a fresh operator boundary.
                    stop_statuses.add(503);self.stop('SOURCE_RETRY_AFTER_HTTP_503')
                elif has_blocking_interstitial(response.body):self.stop('SOURCE_BLOCKED_INTERSTITIAL')
                return response
            response,attempts=request_with_resilience(send,self.rate,self.limiter,clock=self.clock,metrics=self.metrics,breaker=self.breaker,deadline=deadline,stop_statuses=stop_statuses,abort_requested=self.aborted)
            observed=datetime.now(timezone.utc)
            outcome=FetchOutcome(url,response,observed,attempts,self.clock.monotonic()-started,
                self.stop_reason if response.status_code in stop_statuses or has_blocking_interstitial(response.body) else None,
                retry_after_seconds(response.headers) if getattr(response,'headers',None) else None,transport_started)
            if self.receipt_sink:
                try:self.receipt_sink(outcome)
                except Exception:
                    self.stop('CAPTURE_ARCHIVE_FAILED');raise
            return outcome
        except Exception as e:
            return FetchOutcome(url,response,observed,attempts,self.clock.monotonic()-started,error=f'{type(e).__name__}:{e}',transport_started=transport_started)
        finally:
            if client and getattr(client,'session',None):client.session.close()
    def fetch(self,urls,*,limit,seconds):
        urls=list(urls)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=200004 or len(urls)>limit:raise ValueError('HARD_URL_BUDGET')
        if len(urls)!=len(set(urls)):raise ValueError('DUPLICATE_URL')
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not math.isfinite(seconds) or not 0<seconds<=3600:raise ValueError('TIME_BUDGET')
        for url in urls:self._validate(url)
        self._url_budget=limit
        deadline=self.clock.monotonic()+min(seconds,self.rate.max_run_duration_seconds)
        pending={};cursor=0
        try:
            with ThreadPoolExecutor(max_workers=self.rate.max_concurrency) as pool:
                try:
                    while pending or cursor<len(urls):
                        while cursor<len(urls) and len(pending)<self.rate.max_concurrency and not self.aborted() and self.clock.monotonic()<deadline:
                            url=urls[cursor];cursor+=1;pending[pool.submit(self._fetch,url,deadline)]=url
                        if not pending:break
                        done,_=wait(pending,return_when=FIRST_COMPLETED)
                        for future in done:
                            pending.pop(future);outcome=future.result();self.outcomes.append(outcome);yield outcome
                        if self.clock.monotonic()>=deadline:self.stop('MAX_RUN_DURATION_EXCEEDED')
                    for url in urls[cursor:]:
                        yield FetchOutcome(url,error='NOT_DISPATCHED',transport_started=False)
                except BaseException:
                    self.stop('CONSUMER_STOP');raise
        finally:
            # A collector reuses this SAME host gate across robots, discovery
            # and product batches. Closing one batch must not stop its job.
            if pending:self.stop('CONSUMER_STOP')
