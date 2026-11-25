"""Prepared per-host bounded fetch component. No scheduler or source activation.

Reuses DomainRatePolicy/Limiter/resilience. The caller must supply the existing
host lease, URL allowlist, robots decision and HTTP client. SQL is never called.
"""
from concurrent.futures import ThreadPoolExecutor,wait,FIRST_COMPLETED
from dataclasses import dataclass, replace
from datetime import datetime,timezone
from http.client import RemoteDisconnected
from urllib.parse import urlsplit
from contextvars import ContextVar
import hashlib,json,math,os,threading
from .rate_control import (DomainRatePolicy,DomainRateLimiter,RequestMetrics,
    CircuitBreaker,CircuitOpen,SystemClock,RequestAborted,request_with_resilience,retry_after_seconds)
from universal_supplier.http_capture import has_blocking_interstitial

# Request-local approval from an authenticated, audited manual launch. Never
# mutate the serving process's environment or leak trust to another request.
MANUAL_POLICIES = ContextVar('manual_collection_policies', default=None)


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
        approved_digests=approved_digests if approved_digests is not None else (MANUAL_POLICIES.get() if MANUAL_POLICIES.get() is not None else json.loads(os.environ.get('STERBRUST_APPROVED_HOST_POLICIES','{}')))
        approved=approved_digests.get(host)
        if isinstance(approved,dict):approved=approved.get(source)
        if approved!=policy_digest(source,host,rate,approval['reference']):raise PermissionError('HOST_PROFILE_DIGEST_NOT_APPROVED')
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
    scope_refusal:dict|None=None
    dispatch_refusal:dict|None=None
    timeout_refusal:dict|None=None
    disconnect_refusal:bool=False
    error_diagnostics:dict|None=None

class FrozenScopeRefusal(RequestAborted):
    """Signed-policy refusal; the rejected target is NEVER dispatched."""
    def __init__(self,target):
        super().__init__('CANARY_URL_OUTSIDE_FROZEN_BATCH')
        self.details={'reason':'CANARY_URL_OUTSIDE_FROZEN_BATCH',
            'blocked_target_sha256':hashlib.sha256(target.encode()).hexdigest()}

class RepeatedDispatchRefusal(RequestAborted):
    """The attempted repeat is denied; no response or successful GET is implied."""
    def __init__(self,target,count,attempts):
        super().__init__('CANARY_PRODUCT_REQUEST_LIMIT')
        self.details={'reason':'CANARY_PRODUCT_REQUEST_LIMIT','cause':'URL_ATTEMPTS_EXHAUSTED',
            'blocked_target_sha256':hashlib.sha256(target.encode()).hexdigest(),
            'dispatch_count':count,'max_attempts':attempts,'target_in_frozen_scope':True}

class ReadTimeoutRefusal(RequestAborted):
    """Unknown transport result; signed isolation prevents an automatic retry."""
    def __init__(self,url):
        super().__init__('READ_TIMEOUT_UNKNOWN')
        self.details={'reason':'READ_TIMEOUT_UNKNOWN','exception':'TimeoutError',
            'response_available':False,'transport_entered':True,
            'blocked_target_sha256':hashlib.sha256(url.encode()).hexdigest()}


class RemoteDisconnectRefusal(RequestAborted):
    """A manual public GET returned no status or response body."""

class BoundedHostFetcher:
    def __init__(self,host,rate,client_factory,*,validator,robots_allowed,
                 lease_acquired=False,clock=None,stop_requested=None,robots_delay_seconds=None,source=None,approval=None,receipt_sink=None,defer_first_start=False,isolate_timeouts=False,prevent_unknown_retry=False,robots_url=None,error_sink=None,manual_product_attempts=False,manual_robots_attempts=None):
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
        from . import automation_execution
        if automation_execution.ACTIVE is not None:
            self.limiter=automation_execution.ACTIVE.host_limiter(self.host,rate,self.clock)
        if defer_first_start:
            # A newly acquired session lease cannot know the previous job's
            # last start. Use the SAME gate with a conservative initial delay.
            with self.limiter._lock:
                self.limiter._next_start=max(self.limiter._next_start,
                    self.clock.monotonic()+self.limiter.policy.min_request_interval_seconds)
        self.breaker=CircuitBreaker(rate,clock=self.clock,metrics=self.metrics);self.stop_reason=None
        self._lock=threading.Lock();self.outcomes=[];self.receipt_sink=receipt_sink
        self.stop_sink=None
        self.request_start_guard=None
        self.isolate_timeouts=isolate_timeouts
        self.prevent_unknown_retry=prevent_unknown_retry
        self.manual_product_attempts=manual_product_attempts
        self.manual_robots_attempts=manual_robots_attempts
        self.source=source;self.robots_url=robots_url;self.error_sink=error_sink
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
    def _fetch(self,url,deadline,request_scope='product'):
        started=self.clock.monotonic();client=None;response=None;attempts=0;observed=None;transport_started=False;diagnostics=None
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
                        if not isinstance(error,RepeatedDispatchRefusal):self.stop(str(error))
                        raise
                if callback:callback(target)
            # The existing public client calls this for each redirect too.
            client.before_request=before_request
            def send():
                nonlocal transport_started,diagnostics
                initial[0]=True
                # Once the transport is entered, absence of a response is
                # uncertain. A limiter deadline BEFORE send proves no GET.
                transport_started=True
                request_started=self.clock.monotonic()
                try:
                    response=client.get_validated(url,validator=self._validate,timeout_seconds=min(self.rate.request_timeout_seconds,max(.001,deadline-self.clock.monotonic())))
                except Exception as error:
                    from .transport_diagnostics import exception_evidence,local_transport_error
                    if not (self.manual_robots_attempts is not None and request_scope=='robots'
                            and isinstance(error,RequestAborted) and diagnostics is not None):
                        diagnostics={**exception_evidence(error),'source':self.source,'host':self.host,
                            'request_scope':request_scope,'response_available':False,'wire_outcome':'UNKNOWN'}
                    if self.prevent_unknown_retry and not isinstance(error,RequestAborted):
                        # In ordinary approved automation an entered transport
                        # without a response is UNKNOWN, never a retry candidate.
                        with self._lock:
                            self.metrics.record_network_error(self.clock.monotonic()-request_started,type(error).__name__)
                            self.breaker.failure(type(error).__name__)
                        from . import automation_execution
                        if request_scope=='robots' and url==self.robots_url and local_transport_error(error):
                            diagnostics['stop_scope']='supplier'
                            if automation_execution.ACTIVE is not None:
                                automation_execution.ACTIVE.pause_source(self.source,'SOURCE_ROBOTS_UNAVAILABLE',diagnostics)
                            self.stop('SOURCE_ROBOTS_UNAVAILABLE')
                            raise RequestAborted('SOURCE_ROBOTS_UNAVAILABLE') from error
                        diagnostics['stop_scope']='system'
                        if automation_execution.ACTIVE is not None:
                            automation_execution.ACTIVE.fail('AUTOMATION_UNKNOWN_HTTP_OUTCOME')
                        self.stop('AUTOMATION_UNKNOWN_HTTP_OUTCOME')
                        raise RequestAborted('AUTOMATION_UNKNOWN_HTTP_OUTCOME') from error
                    manual_request=self.manual_product_attempts and request_scope!='robots'
                    defer_disconnect=manual_request and type(error) is RemoteDisconnected
                    defer_timeout=isinstance(error,TimeoutError) and (self.isolate_timeouts or manual_request)
                    if not (defer_disconnect or defer_timeout):raise
                    # Keep the real network failure and native breaker. The typed
                    # abort only prevents retrying an UNKNOWN transport result.
                    with self._lock:
                        error_type=type(error).__name__
                        self.metrics.record_network_error(self.clock.monotonic()-request_started,error_type)
                        self.breaker.failure(error_type)
                        tripped=self.metrics.circuit_breaker_trips>0
                    if tripped:self.stop('SOURCE_NETWORK_CIRCUIT_OPEN')
                    if defer_disconnect:raise RemoteDisconnectRefusal('REMOTE_DISCONNECTED_UNKNOWN') from error
                    raise ReadTimeoutRefusal(url)
                self._validate(response.final_url)
                for target in response.redirects:self._validate(target)
                if response.status_code in (403,429):self.stop('SOURCE_BLOCKED_HTTP_'+str(response.status_code))
                elif response.status_code==503 and (retry_after_seconds(response.headers or {}) or 0)>0:
                    # Respect a temporary host refusal even on the final
                    # attempt. A later job needs a fresh operator boundary.
                    stop_statuses.add(503);self.stop('SOURCE_RETRY_AFTER_HTTP_503')
                elif has_blocking_interstitial(response.body):self.stop('SOURCE_BLOCKED_INTERSTITIAL')
                return response
            policy=self.rate
            if request_scope=='robots' and self.manual_robots_attempts is not None:
                policy=replace(self.rate,max_attempts=self.manual_robots_attempts)
            elif request_scope!='robots' and self.manual_product_attempts:
                policy=replace(self.rate,max_attempts=1)
            response,attempts=request_with_resilience(send,policy,self.limiter,clock=self.clock,metrics=self.metrics,breaker=self.breaker,deadline=deadline,stop_statuses=stop_statuses,abort_requested=self.aborted)
            if self.prevent_unknown_retry and self.metrics.circuit_breaker_trips:
                self.stop('SOURCE_NETWORK_CIRCUIT_OPEN')
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
            from .transport_diagnostics import safe_message,exception_evidence
            if diagnostics is None:
                diagnostics={**exception_evidence(e),'source':self.source,'host':self.host,
                    'request_scope':request_scope,'response_available':response is not None,
                    'wire_outcome':'UNKNOWN' if transport_started and response is None else 'NOT_DISPATCHED' if not transport_started else 'RESPONSE_AVAILABLE'}
            if self.prevent_unknown_retry and isinstance(e,CircuitOpen):self.stop('SOURCE_NETWORK_CIRCUIT_OPEN')
            outcome=FetchOutcome(url,response,observed,attempts,self.clock.monotonic()-started,error=f'{type(e).__name__}:{safe_message(e)}',transport_started=transport_started,
                scope_refusal=e.details if isinstance(e,FrozenScopeRefusal) else None,
                dispatch_refusal=e.details if isinstance(e,RepeatedDispatchRefusal) else None,
                timeout_refusal=e.details if isinstance(e,ReadTimeoutRefusal) else None,
                disconnect_refusal=isinstance(e,RemoteDisconnectRefusal),error_diagnostics=diagnostics)
            if diagnostics is not None and self.error_sink:
                try:self.error_sink(outcome)
                except Exception:
                    self.stop('TRANSPORT_EVIDENCE_FAILED')
                    from . import automation_execution
                    if automation_execution.ACTIVE is not None:automation_execution.ACTIVE.fail('AUTOMATION_TRANSPORT_EVIDENCE_FAILED')
                    raise
            return outcome
        finally:
            if client and getattr(client,'session',None):client.session.close()
    def fetch(self,urls,*,limit,seconds,request_scope='product'):
        urls=list(urls)
        if request_scope not in ('product','robots','discovery'):raise ValueError('REQUEST_SCOPE_REQUIRED')
        if request_scope=='robots' and (not self.robots_url or urls!=[self.robots_url]):raise ValueError('ROBOTS_SCOPE_MISMATCH')
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
                            url=urls[cursor];cursor+=1;pending[pool.submit(self._fetch,url,deadline,request_scope)]=url
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
