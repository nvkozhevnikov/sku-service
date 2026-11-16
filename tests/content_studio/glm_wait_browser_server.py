"""Isolated UI fixtures. No real AI/VK network or keys."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import uvicorn
from content_studio.app import create_app
from content_studio.ai import AIService
from content_studio.ai_errors import transport_error
from test_vk import FakeVk, MemoryVault, SECRET


class FakeAI:
    def request(self, config, _token, _system, user):
        try: data = json.loads(user)
        except ValueError: return '{"ok":true}'
        if data.get('source_data', '').startswith('QA:timeout'):
            time.sleep(2)
            raise transport_error('timeout', config.get('timeout_seconds', 300))
        return json.dumps({'drafts': {c: {'title': 'Учебный материал',
            'body': 'С установленной защитой ход каретки составляет 280 мм.' if c == 'vk' else '<p>Синтетический текст.</p>'}
            for c in data.get('channels', [])}})


app = create_app(sys.argv[1], 'qa-browser-local')
app.state.vk.client, app.state.vk.vault = FakeVk(), MemoryVault()
app.state.vk.connect(SECRET)
app.state.ai = AIService(app.state.store, FakeAI(), MemoryVault())
uvicorn.run(app, host='127.0.0.1', port=58207, access_log=False, log_level='warning')
