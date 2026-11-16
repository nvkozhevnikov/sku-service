"""Isolated browser QA: all AI/VK clients are fake; no external requests."""
from pathlib import Path
import sys
import json
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from content_studio.app import create_app
from content_studio.ai import AIService
from content_studio.vk_transport import VkError
from test_vk import FakeVk, MemoryVault, SECRET
import uvicorn


class BrowserAI:
    def request(self, cfg, token, system, user):
        try:
            channels = json.loads(user)['channels']
        except (ValueError, KeyError):
            return '{"ok":true}'
        return json.dumps({'drafts': {c: {'title': 'BELMASH B3499 · QA AI', 'body': 'Потребляемая мощность:600Вт. С установленной защитой ход каретки составляет280мм.' if c == 'vk' else '<p>Потребляемая мощность:600Вт.</p>'} for c in channels}})


data = sys.argv[1] if len(sys.argv) > 1 else 'test-results/tags-ai-browser-data'
app = create_app(data, 'qa-browser-local')
app.state.vk.client, app.state.vk.vault = FakeVk(), MemoryVault()
app.state.vk.connect(SECRET)
app.state.vk.client.photo_error = VkError(27)
app.state.vk.probe_photos()
app.state.ai = AIService(app.state.store, BrowserAI(), MemoryVault())
uvicorn.run(app, host='127.0.0.1', port=58207, access_log=False, log_level='warning')
