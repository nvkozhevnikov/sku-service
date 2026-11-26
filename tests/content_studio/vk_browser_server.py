"""QA-only localhost process. All VK calls are replaced, no real credentials."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from content_studio.app import create_app
from content_studio.vk_transport import VkError
from test_vk import FakeVk,MemoryVault,SECRET
import uvicorn

app=create_app('test-results/vk-browser-data','qa-browser-local')
app.state.vk.client=FakeVk();app.state.vk.vault=MemoryVault()
app.state.vk.connect(SECRET)
app.state.vk.client.photo_error=VkError(27)
app.state.vk.probe_photos()
uvicorn.run(app,host='127.0.0.1',port=58207,access_log=False,log_level='warning')
