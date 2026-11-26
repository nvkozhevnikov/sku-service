import json
from email.utils import formatdate
from unittest.mock import patch

import pytest

from content_studio.ai import AIClient
from content_studio.ai_errors import AIAPIError, AICooldown, provider_error, retry_seconds
from test_distribution_ai import KEY, SOURCE, ai_setup


@pytest.mark.parametrize('code,reason', [
    ('1113', 'balance'), ('1211', 'model'), ('1302', 'rate_limit'), ('1305', 'overloaded'),
    ('1308', 'quota'), ('1309', 'plan_expired'), ('1310', 'quota'), ('1311', 'model_access'),
    ('1313', 'account_restricted'), ('1314', 'plan_expired'), ('1315', 'key_type'),
    ('1316', 'quota_balance'), ('1317', 'quota_balance'),
    ('1318', 'spend_limit'), ('1319', 'spend_limit'), ('1320', 'spend_limit'), ('1321', 'spend_limit'),
])
def test_zai_business_error_is_specific_without_provider_text(code, reason):
    raw = json.dumps({'error': {'code': code, 'message': KEY + ' <script>untrusted</script>'}}).encode()
    error = provider_error('https://api.z.ai/api/paas/v4', 429, raw)
    assert error.reason == reason and error.code == code
    assert KEY not in str(error) and 'script' not in str(error)
    assert KEY not in json.dumps(error.public())
    assert bool(error.wait) == (code in ('1302', '1305'))
    assert 'HTTP 429' in str(error) and code in str(error)


@pytest.mark.parametrize('raw', [b'<html>private proxy error</html>', b'[]', b'null',
    b'{"error": "malformed"}', b'{"error":{"code":"<script>"}}', b'\xff', b'{' * 3000,
    b'x' * (2 * 1024 * 1024 + 1)], ids=['html', 'array', 'null', 'shape', 'unsafe-code', 'encoding', 'deep', 'oversize'])
def test_unknown_error_never_claims_balance_or_echoes_untrusted_text(raw):
    error = provider_error('https://api.z.ai/api/paas/v4', 429, raw)
    assert error.reason == 'rate_or_quota' and not error.code
    assert 'точная причина не указана' in str(error)
    assert 'private proxy' not in str(error) and '<script>' not in str(error)


def test_zai_codes_not_applied_to_other_hosts_and_task_metadata_survives():
    raw = b'{"error":{"code":"1113"}}'
    error = provider_error('https://other.example.com', 429, raw).for_tasks('Пост для VK')
    assert error.reason == 'rate_or_quota' and not error.code
    assert str(error).startswith('Пост для VK') and error.public()['http_status'] == 429


def test_retry_header_is_bounded_and_date_is_relative():
    assert retry_seconds('99999999') == 86400
    assert retry_seconds('-1') == 0 and retry_seconds(KEY) == 0
    with patch('content_studio.ai_errors.time.time', return_value=1000):
        assert retry_seconds(formatdate(1090, usegmt=True)) == 90
        assert retry_seconds(formatdate(900, usegmt=True)) == 0
    error = provider_error('https://api.z.ai/api/paas/v4', 429, b'{"error":{"code":1302}}', '75')
    assert error.wait == 75


def test_cooldown_blocks_network_until_expiry_and_never_auto_retries():
    config = {'base_url': 'https://api.z.ai/api/paas/v4', 'model': 'glm-5.3', 'api_type': 'openai',
              'token_limit': 4096, 'token_parameter': 'max_tokens', 'json_mode': True}
    class Response:
        status = 429
        def getheader(self, _): return '60'
        def read(self, _): return json.dumps({'error': {'code': '1302', 'message': KEY}}).encode()
    client = AIClient()
    clock = [100]
    with patch('content_studio.ai_errors.time.monotonic', side_effect=lambda: clock[0]), \
            patch('content_studio.ai.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]) as dns, \
            patch('content_studio.ai.http.client.HTTPSConnection') as connect:
        connect.return_value.getresponse.return_value = Response()
        with pytest.raises(AIAPIError) as first:
            client.request(config, KEY, 'system', 'user')
        assert first.value.wait == 60
        clock[0] = 110
        with pytest.raises(AIAPIError) as second:
            client.request(config, KEY, 'system', 'user')
        assert second.value.wait == 50
        assert connect.call_count == dns.call_count == connect.return_value.request.call_count == 1
        clock[0] = 161
        with pytest.raises(AIAPIError):
            client.request(config, KEY, 'system', 'user')
        assert connect.return_value.request.call_count == 2
        assert KEY not in str(first.value) and KEY not in str(second.value)
        assert connect.return_value.close.call_count == 2


def test_cooldown_is_bounded_and_separate_for_different_credentials():
    guard = AICooldown()
    error = provider_error('https://api.z.ai/api/paas/v4', 429, b'{}')
    for n in range(105):
        guard.remember(guard.identity('https://api.z.ai/api/paas/v4', str(n)), error)
    assert len(guard.entries) == 100
    guard.check(guard.identity('https://api.z.ai/api/paas/v4', 'new-key'))
    with pytest.raises(AIAPIError):
        guard.check(guard.identity('https://api.z.ai/api/paas/v4', '104'))


def test_structured_error_preserves_existing_project_and_does_not_create_new(tmp_path):
    app, fake, client, headers, cfg = ai_setup(tmp_path)
    original = app.state.store.create('Сохранённый материал')
    def fail(*_args):
        raise provider_error('https://api.z.ai/api/paas/v4', 429, b'{"error":{"code":"1113"}}')
    fake.request = fail
    options = {'source': SOURCE, 'channels': ['vk'], 'engine': 'ai',
               'ai_version': cfg['version'], 'ai_acknowledged': True}
    before = app.state.store.list()
    response = client.post('/api/compose', json=options, headers=headers)
    assert response.status_code == 400 and response.json()['ai_error']['reason'] == 'balance'
    assert 'Пост для VK' in response.json()['error'] and KEY not in response.text
    response = client.post(f'/api/projects/{original["id"]}/compose',
        json={**options, 'expected_revision': original['revision']}, headers=headers)
    assert response.status_code == 400 and response.json()['ai_error']['provider_code'] == '1113'
    assert app.state.store.get(original['id']) == original and app.state.store.list() == before


def test_connection_check_uses_same_safe_error_format(tmp_path):
    _, fake, client, headers, cfg = ai_setup(tmp_path)
    def fail(*_args):
        raise provider_error('https://api.z.ai/api/paas/v4', 429, b'{"error":{"code":"1311"}}')
    fake.request = fail
    result = client.post('/api/ai/test', json={'version': cfg['version'], 'acknowledged': True}, headers=headers)
    assert result.status_code == 400 and result.json()['ai_error']['reason'] == 'model_access'
    assert KEY not in result.text
