import http.client
import json
import ssl
from unittest.mock import patch

import pytest

from content_studio.ai import AIClient
from content_studio.ai_errors import AIAPIError, provider_error
from test_distribution_ai import KEY, SOURCE, ai_setup


def config(**extra):
    return {'base_url': 'https://api.z.ai/api/coding/paas/v4', 'model': 'glm-5.3',
            'api_type': 'openai', 'token_parameter': 'max_tokens', 'token_limit': 4096,
            'json_mode': True, **extra}


@pytest.mark.parametrize('effort,sent', [('auto', 'low'), ('low', 'low'), ('high', 'high'), ('max', 'max'), ('provider', None)])
def test_glm_effort_and_timeout_apply_without_disabling_reasoning(effort, sent):
    response = {'choices': [{'finish_reason': 'stop', 'message': {'content': '{"ok":true}'}}]}
    class Response:
        status = 200
        def read(self, _): return json.dumps(response).encode()
    with patch('content_studio.ai.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
            patch('content_studio.ai.http.client.HTTPSConnection') as connection:
        connection.return_value.getresponse.return_value = Response()
        assert AIClient().request(config(reasoning_effort=effort, timeout_seconds=420), KEY, 'JSON', 'user') == '{"ok":true}'
        assert connection.call_args.kwargs['timeout'] == 420
        payload = json.loads(connection.return_value.request.call_args.kwargs['body'])
        assert payload.get('reasoning_effort') == sent
        assert payload.get('thinking') == ({'type': 'enabled'} if sent else None)


def test_auto_does_not_send_glm_fields_to_unrelated_provider():
    with patch('content_studio.ai.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
            patch('content_studio.ai.http.client.HTTPSConnection') as connection:
        connection.return_value.getresponse.return_value.read.return_value = b'{"choices":[{"finish_reason":"stop","message":{"content":"ok"}}]}'
        connection.return_value.getresponse.return_value.status = 200
        AIClient().request(config(base_url='https://other.example.com', model='other'), KEY, 'JSON', 'user')
        payload = json.loads(connection.return_value.request.call_args.kwargs['body'])
        assert 'thinking' not in payload and 'reasoning_effort' not in payload
        assert connection.call_args.kwargs['timeout'] == 300


@pytest.mark.parametrize('exception,reason', [(TimeoutError(KEY), 'timeout'),
    (ConnectionResetError(KEY), 'connection'), (ssl.SSLError(KEY), 'tls'),
    (http.client.RemoteDisconnected(KEY), 'connection'), (http.client.IncompleteRead(b'private', 100), 'connection')],
    ids=['timeout', 'reset', 'tls', 'disconnect', 'incomplete'])
@pytest.mark.parametrize('stage', ['headers', 'body'])
def test_transport_errors_are_specific_private_and_never_retried(exception, reason, stage):
    with patch('content_studio.ai.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
            patch('content_studio.ai.http.client.HTTPSConnection') as connection:
        conn = connection.return_value
        if stage == 'headers': conn.getresponse.side_effect = exception
        else: conn.getresponse.return_value.read.side_effect = exception
        with pytest.raises(AIAPIError) as caught:
            AIClient().request(config(), KEY, 'JSON', 'user')
        assert caught.value.reason == reason and caught.value.status is None
        assert KEY not in str(caught.value) and 'private' not in str(caught.value)
        assert 'HTTP None' not in str(caught.value) and not caught.value.wait
        conn.request.assert_called_once()
        conn.close.assert_called_once()


@pytest.mark.parametrize('timeout,effort', [(True, 'auto'), (29, 'auto'), (601, 'auto'), (60, 'invalid'), (60, None)])
def test_invalid_wait_settings_do_not_replace_configuration(tmp_path, timeout, effort):
    _, _, client, headers, cfg = ai_setup(tmp_path)
    result = client.post('/api/ai/settings', json={**cfg, 'timeout_seconds': timeout, 'reasoning_effort': effort}, headers=headers)
    assert result.status_code == 400 and client.get('/api/ai/settings').json() == cfg


def test_valid_wait_settings_preserve_key_and_routes_and_atomicity(tmp_path):
    app, fake, client, headers, cfg = ai_setup(tmp_path)
    saved = client.post('/api/ai/settings', json={**cfg, 'timeout_seconds': 420, 'reasoning_effort': 'low'}, headers=headers).json()
    assert saved['configured'] and saved['timeout_seconds'] == 420 and KEY not in json.dumps(saved)
    routes = client.get('/api/ai/gateway').json()['tasks']
    assert all(task['route']['timeout_seconds'] == 420 for task in routes)
    def fail(*_):
        from content_studio.ai_errors import transport_error
        raise transport_error('timeout', 420)
    fake.request = fail
    before = app.state.store.list()
    result = client.post('/api/compose', json={'source': SOURCE, 'engine': 'ai', 'channels': ['vk', 'news', 'article'],
        'ai_version': saved['version'], 'ai_acknowledged': True}, headers=headers)
    assert result.status_code == 400 and result.json()['ai_error']['reason'] == 'timeout'
    assert '420' in result.json()['error'] and app.state.store.list() == before


def test_coding_balance_message_does_not_claim_standard_api_is_configured():
    error = provider_error('https://api.z.ai/api/coding/paas/v4', 429, b'{"error":{"code":"1113"}}')
    assert error.reason == 'balance' and 'Coding Plan' in str(error)
    assert 'Standard API Key' not in str(error)
