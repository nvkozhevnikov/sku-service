from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier, Lock
from time import perf_counter, sleep

import pytest

from universal_supplier.control_plane.rate_control import (
    DomainRateLimiter, DomainRatePolicy, RequestAborted,
)
from universal_supplier.http import HttpTransport
from universal_supplier.crawl import run_partner_st_live
from universal_supplier.models import FetchRecord
from universal_supplier.partner_pilot import read_stop_reason, rate_profile
from test_partner_pilot import batch, Transport
from test_stage6c1_partner import FakeClock, FakeSession


def test_profile_preserves_minimum_and_allows_positive_jitter():
    policy = DomainRatePolicy(**rate_profile('compare_2'))
    assert (policy.max_concurrency, policy.min_request_interval_seconds) == (2, 1)
    assert (policy.jitter_min_seconds, policy.jitter_max_seconds) == (0, .1)
    clock = FakeClock()
    jitters = iter([0, .03, .1, 0])
    limiter = DomainRateLimiter(policy, clock=clock, random_uniform=lambda *_: next(jitters))
    starts = []
    for _ in range(4):
        limiter.wait()
        starts.append(clock.monotonic())
    gaps = [b-a for a,b in zip(starts, starts[1:])]
    assert gaps == pytest.approx([1, 1.03, 1.1])
    assert min(gaps) >= 1


def test_two_workers_share_native_gate_without_exact_pause_requirement():
    class Clock:
        monotonic = staticmethod(perf_counter)
        sleep = staticmethod(sleep)
    limiter = DomainRateLimiter(DomainRatePolicy(**rate_profile('compare_2')),
                                clock=Clock(), random_uniform=lambda *_: .02)
    starts = []
    lock = Lock()
    def admit(_):
        limiter.wait()
        with lock:
            starts.append(perf_counter())
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(admit, range(3)))
    assert all(b-a >= 1 for a,b in zip(sorted(starts), sorted(starts)[1:]))


@pytest.mark.parametrize('when', ['before_gate', 'after_gate', 'inside_send'])
def test_local_abort_is_not_wire_error_retry_or_breaker_failure(when):
    clock = FakeClock()
    def abort():
        return when == 'before_gate' or (when == 'after_gate' and clock.value >= 1)
    session = FakeSession([RequestAborted('OPERATOR_STOP')])
    transport = HttpTransport(session=session, clock=clock, abort_requested=abort,
                              domain_policy=DomainRatePolicy(min_request_interval_seconds=1,
                                                             jitter_max_seconds=0, max_attempts=3))
    if when == 'after_gate':
        transport.limiter.wait()
    with pytest.raises(RequestAborted):
        transport.get_validated('https://partner-st.ru/item-1/', lambda u:u)
    assert len(session.calls) == (1 if when == 'inside_send' else 0)
    assert transport.metrics.requests_total == transport.metrics.network_errors == 0
    assert transport.metrics.requests_aborted == 1
    assert transport.metrics.retry_count == transport.metrics.circuit_breaker_trips == 0


def test_stop_file_preserves_reason_and_empty_file_still_stops(tmp_path):
    path = tmp_path/'stop'
    assert read_stop_reason(path) is None
    path.write_text('  OPERATOR_STOP  ', encoding='utf-8')
    assert read_stop_reason(path) == 'OPERATOR_STOP'
    path.write_text('', encoding='utf-8')
    assert read_stop_reason(path) == 'STOP_REQUESTED'


@pytest.mark.parametrize('reason', [True, 'OPERATOR_STOP', 'PILOT_UNSAFE_TEST'])
def test_stop_is_not_deadline_and_preserves_first_reason(tmp_path, reason):
    values = iter([reason, 'LATER_REASON'])
    result = run_partner_st_live(baseline=1222, pilot=batch(2), evidence_root=tmp_path,
        transport_factory=Transport, rate_policy=DomainRatePolicy(max_concurrency=2),
        stop_requested=lambda:next(values, 'LATER_REASON'))
    expected = 'STOP_REQUESTED' if reason is True else reason
    assert result.termination_reason == expected
    assert result.discovery_metadata['stop_requested'] is True
    assert result.discovery_metadata['max_run_duration_exceeded'] is False
    assert not result.cards and not result.captures and not result.rows
    assert result.errors[-1].message.startswith(expected)
    assert 'deadline' not in result.errors[-1].message
    import json
    assert json.loads((tmp_path/'FIRST_STOP.json').read_text())['reason'] == expected


def test_two_workers_first_site_stop_survives_waiter_abort(tmp_path):
    both_started = Barrier(2)
    class SiteStop(Transport):
        def __init__(self, **kw):
            super().__init__(**kw)
            self.abort = kw['abort_requested']
        def get_validated(self, url, validator):
            if url.endswith('robots.txt'):
                return super().get_validated(url, validator)
            both_started.wait(timeout=2)
            if 'item-1/' in url:
                return FetchRecord(url, url, 403, (), 1, 0, 'Forbidden')
            end = perf_counter()+2
            while not self.abort() and perf_counter()<end:
                sleep(.001)
            assert self.abort()
            raise RequestAborted('HOST_STOPPED')
    result = run_partner_st_live(baseline=1222, pilot=batch(2), evidence_root=tmp_path,
        transport_factory=SiteStop, rate_policy=DomainRatePolicy(max_concurrency=2))
    assert result.termination_reason == 'PILOT_SITE_BLOCKED'
    import json
    assert json.loads((tmp_path/'FIRST_STOP.json').read_text())['reason'] == 'PILOT_SITE_BLOCKED'
    assert not result.discovery_metadata['max_run_duration_exceeded']
    assert not result.cards


@pytest.mark.parametrize('reason', ['STOP_REQUESTED', 'OPERATOR_STOP', 'MAX_RUN_DURATION_EXCEEDED'])
def test_any_stopped_pilot_refuses_ingest_and_matching(tmp_path, monkeypatch, reason):
    from unittest.mock import MagicMock
    from universal_supplier.control_plane import adapter_registry as ar
    from universal_supplier.control_plane.queue import CrawlJob
    from test_partner_pilot import run, CFG
    result, _ = run(tmp_path, 1)
    result = replace(result, termination_reason=reason)
    repo = MagicMock()
    monkeypatch.setattr(ar, 'PostgresRepository', lambda _:repo)
    monkeypatch.setattr(ar, '_run_with_progress', lambda *a, **kw:result)
    matching = MagicMock()
    monkeypatch.setattr(ar, 'run_post_ingest', matching)
    for k,v in {'STERBRUST_PARTNER_PILOT':'YES','STERBRUST_ALLOW_SUPPLIER_HTTP':'YES',
                'PARTNER_PILOT_EVIDENCE_ROOT':str(tmp_path/'evidence'),
                'PARTNER_PILOT_STOP_FILE':str(tmp_path/'stop')}.items():
        monkeypatch.setenv(k,v)
    job = CrawlJob(1,1,'partner_st','partner_st','https://partner-st.ru/','manual','full',
                   'running',None,{'partner_pilot':batch(1),'rate_settings':rate_profile('conservative')})
    with pytest.raises(RuntimeError, match=reason):
        ar.run_partner_st_job(job, CFG)
    repo.ingest.assert_not_called()
    repo.record_partner_pilot_captures.assert_not_called()
    matching.assert_not_called()
