"""Only 58207 with isolated data. DNS and HTTPS transport are replaced for AI QA."""
import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from content_studio.ai import AIClient, AIService
from content_studio.app import create_app
from test_vk import FakeVk, MemoryVault, SECRET


class FakeConnection:
    calls = 0
    def __init__(self, *_args, **_kwargs):
        self.status = 200
        self.code = ''
    def request(self, _method, _path, *, body, headers):
        type(self).calls += 1
        payload = json.loads(body)
        try:
            user = json.loads(payload['messages'][-1]['content'])
            source = user.get('source_data', '')
            if source.startswith('QA:balance'):
                self.status, self.code = 429, '1113'
            elif source.startswith('QA:rate_limit'):
                self.status, self.code = 429, '1302'
            self.text = json.dumps({'drafts': {c: {'title': 'Учебный материал',
                'body': 'Синтетический текст.' if c == 'vk' else '<p>Синтетический текст.</p>'}
                for c in user.get('channels', [])}})
        except (ValueError, KeyError):
            self.text = '{"ok":true}'
    def getresponse(self): return self
    def getheader(self, _name): return '60' if self.code == '1302' else None
    def read(self, _limit):
        if self.code:
            return json.dumps({'error': {'code': self.code, 'message': 'PRIVATE_RESPONSE_MUST_STAY_HIDDEN'}}).encode()
        return json.dumps({'choices': [{'finish_reason': 'stop', 'message': {'content': self.text}}]}).encode()
    def close(self): pass


class BrowserAI(AIClient):
    def request(self, *args):
        with patch('content_studio.ai.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('8.8.8.8', 443))]), \
                patch('content_studio.ai.http.client.HTTPSConnection', FakeConnection):
            return super().request(*args)


app = create_app(sys.argv[1], 'qa-browser-local')
app.state.vk.client, app.state.vk.vault = FakeVk(), MemoryVault()
app.state.vk.connect(SECRET)
app.state.ai = AIService(app.state.store, BrowserAI(), MemoryVault())


@app.get('/api/qa/transport-count')
def transport_count():
    return {'calls': FakeConnection.calls}


uvicorn.run(app, host='127.0.0.1', port=58207, access_log=False, log_level='warning')
