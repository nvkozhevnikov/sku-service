import json

from fastapi.testclient import TestClient

from content_studio.ai import AIService
from content_studio.app import create_app
from test_vk import MemoryVault


class EditorialModel:
    def __init__(self):
        self.calls = []
        self.fail_model = ''

    def request(self, config, token, instructions, user):
        payload = json.loads(user)
        self.calls.append((config['base_url'], config['model'], token, instructions, payload))
        if config['model'] == self.fail_model:
            return '{"drafts":{}}'
        return json.dumps({'drafts': {channel: {'body': 'Учебный текст.'} for channel in payload['channels']}})


def setup_gateway(tmp_path):
    app = create_app(tmp_path, 'qa')
    model = EditorialModel()
    app.state.ai = AIService(app.state.store, model, MemoryVault())
    client = TestClient(app)
    client.headers['X-CSRF-Token'] = client.post('/api/login', json={'code': 'qa'}).json()['csrf']
    return app, client, model


def save_profile(client, version, name, url, key, **overrides):
    result = client.post('/api/ai/gateway/profile', json={
        'version': version, 'name': name, 'base_url': url, 'model': name, 'key': key, **overrides,
    })
    assert result.status_code == 200, result.text
    return result.json()


def test_per_task_models_and_keys_follow_confirmed_routes(tmp_path):
    app, client, model = setup_gateway(tmp_path)
    first = save_profile(client, '0:0', 'glm-example', 'https://glm.example.com/v4', 'first-local-fake-key')
    assert first['default'] == first['saved_id']
    second = save_profile(client, first['version'], 'article-example', 'https://article.example.com/v1', 'second-local-fake-key')
    route = client.post('/api/ai/gateway/task', json={
        'version': second['version'], 'id': 'article', 'profile': second['saved_id'],
        'model': 'long-example', 'token_limit': 6400, 'instructions': 'Добавь раздел характеристик.',
    }).json()
    source = 'В мастерской представили токарный станок. Потребляемая мощность 600 Вт.'
    options = {'source': source, 'channels': ['vk', 'news', 'article'], 'engine': 'ai', 'ai_acknowledged': True}
    stale = client.post('/api/compose', json={**options, 'ai_gateway_version': second['version']})
    assert stale.status_code == 400 and not model.calls
    response = client.post('/api/compose', json={**options, 'ai_gateway_version': route['version']})
    assert response.status_code == 200, response.text
    assert [call[:3] for call in model.calls] == [
        ('https://glm.example.com/v4', 'glm-example', 'first-local-fake-key'),
        ('https://article.example.com/v1', 'long-example', 'second-local-fake-key'),
    ]
    assert model.calls[0][4]['channels'] == ['vk', 'news']
    assert model.calls[1][4]['channels'] == ['article']
    assert 'Добавь раздел характеристик.' in model.calls[1][3]
    assert response.json()['composition']['ai_routes']['article']['token_limit'] == 6400
    assert 'key_ref' not in client.get('/api/ai/gateway').text
    assert all(draft['state'] == 'DRAFT' for draft in response.json()['project']['drafts'].values())


def test_offline_check_never_reads_credentials_or_calls_model(tmp_path, monkeypatch):
    app, client, model = setup_gateway(tmp_path)
    config = save_profile(client, '0:0', 'glm-example', 'https://glm.example.com/v4', '')
    assert not config['profiles'][0]['configured']

    def fail_if_read(*_args):
        raise AssertionError('Offline check must not read a key')

    monkeypatch.setattr(app.state.ai.vault, 'get', fail_if_read)
    result = client.post('/api/ai/gateway/offline-check', json={'id': 'article'})
    assert result.status_code == 200, result.text
    assert result.json()['mode'] == 'offline' and not result.json()['route']['ready']
    assert '<h2>' in result.json()['draft']['body']
    assert not model.calls and not app.state.store.list()


def test_missing_later_key_and_failed_later_response_preserve_atomicity(tmp_path):
    app, client, model = setup_gateway(tmp_path)
    first = save_profile(client, '0:0', 'vk-example', 'https://vk.example.com/v1', 'first-local-fake-key')
    second = save_profile(client, first['version'], 'news-example', 'https://news.example.com/v1', '')
    route = client.post('/api/ai/gateway/task', json={
        'version': second['version'], 'id': 'news', 'profile': second['saved_id'],
    }).json()
    options = {'source': 'Представили новый станок. Мощность 600 Вт.', 'channels': ['vk', 'news'],
               'engine': 'ai', 'ai_acknowledged': True, 'ai_gateway_version': route['version']}
    assert client.post('/api/compose', json=options).status_code == 400
    assert not model.calls
    ready = save_profile(client, route['version'], 'news-example', 'https://news.example.com/v1', 'second-local-fake-key', id=second['saved_id'])
    model.fail_model = 'news-example'
    result = client.post('/api/compose', json={**options, 'ai_gateway_version': ready['version']})
    assert result.status_code == 400 and 'Новость для сайта' in result.json()['error']
    assert len(model.calls) == 2 and not app.state.store.list()


def test_profile_edits_keep_legacy_and_enforce_version_and_key_scope(tmp_path):
    _, client, model = setup_gateway(tmp_path)
    legacy = client.post('/api/ai/settings', json={'base_url': 'https://legacy.example.com/v1', 'model': 'legacy-example', 'key': 'legacy-local-fake-key'}).json()
    first = save_profile(client, '0:1', 'new-example', 'https://new.example.com/v1', 'new-local-fake-key')
    assert first['default'] == 'legacy' and client.get('/api/ai/settings').json() == legacy
    stale = client.post('/api/ai/gateway/default', json={'version': '0:1', 'id': first['saved_id']})
    assert stale.status_code == 409
    changed = client.post('/api/ai/gateway/profile', json={
        'version': first['version'], 'id': first['saved_id'], 'name': 'new-example',
        'base_url': 'https://different.example.com/v1', 'model': 'new-example', 'key': '',
    })
    assert changed.status_code == 400 and not model.calls
