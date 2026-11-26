"""In-process UI and policy regressions. No database or supplier transport."""
import json
import re
import pytest

from test_automation_admin import client
from test_automation_admin import csrf
from universal_supplier.control_plane.host_fetch import host_key, policy_digest
from universal_supplier.control_plane.rate_control import DomainRatePolicy
from universal_supplier.control_plane.supplier_automation import (
    digest, public_settings, rate_for, settings_for,
)


def confirmed_supplier():
    row = {'id': 7, 'code': 'uss_stanko', 'name': 'USS-Stanko',
           'base_url': 'https://uss-stanko.com/', 'enabled': False,
           'rate_settings': DomainRatePolicy(min_request_interval_seconds=0.2).snapshot(),
           'crawl_settings': {}}
    cfg = settings_for(row)
    rate = rate_for(cfg, row['rate_settings'])
    ref = f"ADMIN:{row['code']}:REV:{cfg['revision']}"
    row['crawl_settings']['host_policy_approval'] = {
        'host': host_key(row['base_url']), 'reference': ref,
        'approved_rate_sha256': digest(rate),
    }
    return row, rate, ref


def test_panel_confirmation_does_not_fabricate_executor_approval(monkeypatch):
    monkeypatch.delenv('STERBRUST_APPROVED_HOST_POLICIES', raising=False)
    row, _, _ = confirmed_supplier()
    view = public_settings(row)
    assert view['live_blocked_reason'] == 'HOST_PROFILE_DIGEST_NOT_APPROVED'
    assert view['host_confirmation_saved'] is True


def test_exact_executor_policy_still_requires_separate_execution_approval(monkeypatch):
    row, rate, ref = confirmed_supplier()
    token = policy_digest(row['code'], 'uss-stanko.com', DomainRatePolicy.from_mapping(rate), ref)
    monkeypatch.setenv('STERBRUST_APPROVED_HOST_POLICIES', json.dumps({'uss-stanko.com': {'uss_stanko': token}}))
    view = public_settings(row)
    assert view['live_blocked_reason'] == 'AUTOMATION_EXECUTION_APPROVAL_REQUIRED'
    row['rate_settings']['request_timeout_seconds'] += 1
    assert public_settings(row)['live_blocked_reason'] == 'HOST_PROFILE_DIGEST_NOT_APPROVED'
    assert public_settings(row)['host_confirmation_saved'] is False


def test_policy_error_has_readable_explanation_and_real_return_link(tmp_path, monkeypatch):
    c, _ = client(tmp_path, monkeypatch)
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED', 'YES')
    def blocked(*args):
        raise PermissionError('HOST_PROFILE_DIGEST_NOT_APPROVED')
    c.app.state.queue.preview_source_canary = blocked
    response = c.get('/suppliers/1/source-canary?max_products=1')
    assert response.status_code == 400
    title = re.search(r'<h2>(.*?)</h2>', response.text, re.S).group(1)
    assert 'HOST_PROFILE' not in title
    assert 'исполнител' in title.lower()
    assert 'href="/suppliers/1/parsing"' in response.text
    assert 'javascript:' not in response.text
    assert c.get('/suppliers/1/parsing').status_code == 200
    assert not c.app.state.queue.jobs


def test_disabled_supplier_cannot_offer_an_active_prepare_button(tmp_path, monkeypatch):
    c, store = client(tmp_path, monkeypatch)
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED', 'YES')
    store.data['suppliers'][0]['enabled'] = False
    page = c.get('/suppliers/1')
    assert page.status_code == 200
    assert 'Источник выключен' in page.text
    assert 'action="/suppliers/1/enabled"' in page.text
    form = re.search(r'<form[^>]+action="/suppliers/1/source-canary".*?</form>', page.text, re.S).group(0)
    assert re.search(r'<button\b[^>]*disabled', form)
    assert not c.app.state.queue.jobs


def test_settings_report_saved_confirmation_without_pretending_live_is_ready(tmp_path, monkeypatch):
    c, store = client(tmp_path, monkeypatch)
    row, _, _ = confirmed_supplier()
    store.data['suppliers'][0] = row
    monkeypatch.delenv('STERBRUST_APPROVED_HOST_POLICIES', raising=False)
    page = c.get('/suppliers/7/parsing?host_saved=1')
    assert page.status_code == 200
    assert 'Подтверждение сохранено' in page.text
    assert 'исполнител' in page.text
    assert '0.30000000000000004' not in page.text
    assert not c.app.state.queue.jobs


def test_quick_speed_save_preserves_other_flags_and_uses_revision(tmp_path, monkeypatch):
    c, store = client(tmp_path, monkeypatch)
    row = store.data['suppliers'][0]
    flags = ['schedule_enabled', 'refresh_enabled', 'discovery_enabled', 'export_updates', 'export_new_products']
    row['crawl_settings']['automation'] = {**settings_for(row), **dict.fromkeys(flags, True)}
    page = c.get('/suppliers/1')
    form = re.search(r'<form[^>]+action="/suppliers/1/parsing".*?</form>', page.text, re.S).group(0)
    values = dict(re.findall(r'name="([^"]+)"[^>]*value="([^"]*)"', form))
    values.update(delay_min_seconds='1', delay_max_seconds='2')
    result = c.post('/suppliers/1/parsing', data=values, follow_redirects=False)
    assert result.status_code == 303 and result.headers['location'] == '/suppliers/1?saved=1'
    assert all(settings_for(row)[key] for key in flags)
    assert settings_for(row)['delay_max_seconds'] == 2
    assert c.post('/suppliers/1/parsing', data=values).status_code == 400
    assert len(store.saved) == 1 and not c.app.state.queue.jobs


@pytest.mark.parametrize('case', ['csrf', 'origin', 'viewer', 'read_only'])
def test_inline_enable_keeps_existing_security_boundaries(tmp_path, monkeypatch, case):
    c, store = client(tmp_path, monkeypatch, 'VIEWER' if case == 'viewer' else 'ADMIN')
    changes = []
    store.set_supplier_enabled = lambda *args: changes.append(args)
    token = csrf(c.get('/suppliers/1'))
    if case == 'read_only':monkeypatch.setattr(type(store), 'read_only', True)
    response = c.post('/suppliers/1/enabled',
        data={'csrf_token': 'bad' if case == 'csrf' else token, 'enabled': 'true', 'return_to': 'source'},
        headers={'origin': 'https://attacker.invalid'} if case == 'origin' else {}, follow_redirects=False)
    assert response.status_code == 403 and not changes


def test_no_return_parameter_can_redirect_to_another_site(tmp_path, monkeypatch):
    c, store = client(tmp_path, monkeypatch)
    changes = []
    store.set_supplier_enabled = lambda *args: changes.append(args)
    response = c.post('/suppliers/1/enabled', data={'csrf_token': csrf(c.get('/suppliers/1')),
        'enabled': 'true', 'return_to': 'https://attacker.invalid'}, follow_redirects=False)
    assert response.status_code == 303 and response.headers['location'] == '/suppliers'
    assert changes == [(1, True)] and not c.app.state.queue.jobs
