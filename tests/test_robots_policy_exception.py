"""Offline proof of the signed robots-policy exception path; no HTTP/SQL."""
import hashlib
from datetime import datetime, timezone
from types import SimpleNamespace
import pytest

from offline_host_profiles import approved_policy, approval_for
from test_source_jobs import saved_case
from test_host_fetch import Clock
from universal_supplier.control_plane import automation_execution
from universal_supplier.control_plane.source_runtime import (
    LEASED_HOST, ROBOTS_EXCEPTION_KIND, run_source, engine_fingerprint, signed_robots_exception)
from universal_supplier.control_plane.source_discovery import HOSTS
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.http_capture import HttpResponse
from test_source_runtime import Backend, isolation  # noqa: F401  (autouse offline env)

ROBOTS = b'User-agent: *\nCrawl-delay: 20\nAllow: /\n'
ROBOTS_SHA = hashlib.sha256(ROBOTS).hexdigest()
CODE = 'intervesp'


def exception_block(**changes):
    value = {'format': ROBOTS_EXCEPTION_KIND, 'source': CODE, 'host': HOSTS[CODE],
             'robots_sha256': ROBOTS_SHA, 'declared_minimum_seconds': 20.0,
             'applied_min_request_interval_seconds': 5.0,
             'requires_operator_confirmation': True}
    value.update(changes)
    return value


def active_session(exception=None, sources=(CODE,)):
    from universal_supplier.control_plane.rate_control import DomainRateLimiter
    suppliers = {code: {'host': HOSTS[code], 'min_request_interval_seconds': 5.0,
                        'robots_policy_exception': exception if code == CODE else None} for code in sources}
    return SimpleNamespace(sha='approved-sha', value={'suppliers': suppliers}, check=lambda: None,
                           reason=lambda: None, fail=lambda reason: None,
                           pause_source=lambda code, reason, diagnostics=None: None,
                           host_limiter=lambda host, rate, clock: DomainRateLimiter(rate, clock=clock))


def setup(robots=ROBOTS, rate_changes=None):
    body, url, charset, _ = saved_case(CODE)
    clock = Clock(); calls = []; backend = Backend()
    from universal_supplier.ltt_uss_capture import sanitise_for_charset
    clean = sanitise_for_charset(body, charset)
    from universal_supplier.control_plane.source_jobs import parse_existing
    product = parse_existing(CODE, clean.decode(), url)
    rate = approved_policy(CODE, 'https://' + HOSTS[CODE],
                           {'jitter_max_seconds': 0, 'min_request_interval_seconds': 5.0,
                            'max_attempts': 1, **(rate_changes or {})})
    value = {'format': 'source-runtime-input/v1', 'supplier_code': CODE, 'mode': 'REFRESH',
             'entries': [{'url': url, 'source_external_id': product.site_internal_id}],
             'seeds': [], 'rate_policy': rate.snapshot(), 'engine_sha256': engine_fingerprint()}
    job = CrawlJob(1, 1, CODE, CODE, 'https://' + HOSTS[CODE] + '/', 'manual', 'full', 'running',
                   datetime.now(timezone.utc),
                   {'source_input': value, 'rate_settings': rate.snapshot(),
                    'crawl_settings': {'collection_mode': 'REFRESH', 'persist_source_data': True,
                                       'max_products': 5, 'max_discovery_pages': 5,
                                       'host_policy_approval': approval_for('https://' + HOSTS[CODE])}})

    class Client:
        def get_validated(self, target, *, validator, timeout_seconds):
            validator(target); self.before_request(target)
            calls.append((target, clock.monotonic(), timeout_seconds))
            if target.endswith('/robots.txt'):
                return HttpResponse(target, 200, 'text/plain', robots)
            return HttpResponse(target, 200, 'text/html; charset=' + charset, body)

    def run(j=job):
        token = LEASED_HOST.set(j.domain)
        try:
            return run_source(j, None, client_factory=Client,
                              backend_factory=lambda cfg, source: backend, clock=clock)
        finally:
            LEASED_HOST.reset(token)
    return run, job, backend, calls, url


def test_signed_exception_allows_five_second_pacing(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE', active_session(exception_block()))
    run, job, backend, calls, url = setup()
    result = run()
    assert result['status'] == 'succeeded', result
    assert result['termination_reason'] is None and result['sql_committed'] == 1
    assert result['robots_policy_exception_applied'] == {
        'format': ROBOTS_EXCEPTION_KIND, 'source': CODE, 'robots_sha256': ROBOTS_SHA,
        'declared_minimum_seconds': 20.0, 'applied_min_request_interval_seconds': 5.0}
    gaps = [b[1] - a[1] for a, b in zip(calls, calls[1:])]
    assert all(4.999 <= gap for gap in gaps), gaps


def test_without_session_exception_still_stops(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE', None)
    run, job, backend, calls, url = setup()
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason']
    assert len(calls) == 1 and not backend.writes
    assert result['robots_policy_exception_applied'] is None


def test_session_without_exception_for_source_still_stops(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE', active_session(exception=None))
    run, job, backend, calls, url = setup()
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and not backend.writes


def test_exception_for_other_source_does_not_apply(monkeypatch):
    other = active_session(exception_block(source='kami'))
    monkeypatch.setattr(automation_execution, 'ACTIVE', other)
    run, job, backend, calls, url = setup()
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and not backend.writes


def test_changed_robots_bytes_stop(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE', active_session(exception_block()))
    run, job, backend, calls, url = setup(robots=b'User-agent: *\nCrawl-delay: 20\nDisallow: /private\nAllow: /\n')
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and not backend.writes


def test_declared_minimum_mismatch_stops(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE',
                        active_session(exception_block(declared_minimum_seconds=10.0)))
    run, job, backend, calls, url = setup()
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and not backend.writes


def test_faster_than_signed_exception_stops(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE', active_session(exception_block()))
    run, job, backend, calls, url = setup(rate_changes={'min_request_interval_seconds': 3.0})
    result = run()
    assert 'ROBOTS_REQUIRES_SLOWER' in result['termination_reason'] and not backend.writes


def test_unsigned_exception_block_is_ignored(monkeypatch):
    monkeypatch.setattr(automation_execution, 'ACTIVE',
                        active_session(exception_block(requires_operator_confirmation=False)))
    assert signed_robots_exception(CODE, ROBOTS_SHA, 20.0) is None


def test_load_validates_exception_contract(tmp_path, monkeypatch):
    from test_automation_execution import grant, write, mocked_identity
    def grant_with(exception, interval=5.0):
        value = grant()
        rule = value['suppliers'][CODE]
        rule['min_request_interval_seconds'] = interval
        rule['host'] = HOSTS[CODE]
        if exception is not None:
            rule['robots_policy_exception'] = exception
        return value
    def load(value):
        path, sha = write(tmp_path, value)
        return automation_execution.load(path, sha)
    # 1. A coherent signed exception is accepted by the grant loader.
    mocked_identity(monkeypatch)
    loaded = load(grant_with(exception_block()))
    assert loaded['suppliers'][CODE]['robots_policy_exception']['format'] == ROBOTS_EXCEPTION_KIND
    # 2. Every contract break fails closed at approval load time.
    for changes, reason in (
        (dict(source='kami'), 'AUTOMATION_ROBOTS_EXCEPTION_CONTRACT_INVALID'),
        (dict(host='other.ru'), 'AUTOMATION_ROBOTS_EXCEPTION_CONTRACT_INVALID'),
        (dict(requires_operator_confirmation=False), 'AUTOMATION_ROBOTS_EXCEPTION_CONTRACT_INVALID'),
        (dict(robots_sha256='zz'), 'AUTOMATION_ROBOTS_EXCEPTION_ROBOTS_SHA_INVALID'),
        (dict(declared_minimum_seconds=0), 'AUTOMATION_ROBOTS_EXCEPTION_DECLARED_INVALID'),
        (dict(applied_min_request_interval_seconds=25.0), 'AUTOMATION_ROBOTS_EXCEPTION_APPLIED_INVALID'),
    ):
        with pytest.raises(PermissionError, match=reason):
            load(grant_with(exception_block(**changes)))
    # 3. The approval's own interval must equal the signed applied interval.
    with pytest.raises(PermissionError, match='AUTOMATION_ROBOTS_EXCEPTION_RATE_MISMATCH'):
        load(grant_with(exception_block(), interval=1.0))
