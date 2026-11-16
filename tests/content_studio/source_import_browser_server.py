"""Fixture source reader and AI/VK; no requests to external systems."""
import sys,time,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from content_studio.app import create_app
from content_studio.ai import AIService
from content_studio.source_reader import SourceReader
from test_vk import FakeVk,MemoryVault,SECRET
import uvicorn

class FakeModel:
    def request(self,config,token,system,user):
        data=json.loads(user)
        return json.dumps({'drafts':{c:{'title':'ТВ-7М','body':'Подтверждённые исходником характеристики ТВ-7М. Описание оборудования для мастерской.' if c=='vk' else '<p>Подтверждённые исходником характеристики ТВ-7М.</p>'} for c in data.get('channels',[])}})
    def request_stream(self,config,token,system,user,emit):
        emit({'type':'reasoning','text':'Учебный ответ. Проверяю характеристики.'})
        return self.request(config,token,system,user)

app=create_app(sys.argv[1],'qa-browser-local')
app.state.ai=AIService(app.state.store,FakeModel(),MemoryVault())
app.state.vk.client=FakeVk();app.state.vk.vault=MemoryVault();app.state.vk.connect(SECRET)
raw=(Path(__file__).parent/'fixtures/product-tv7m-dom.html').read_text(encoding='utf-8')
def source(url):
    if '/slow/' in url:time.sleep(1)
    if '/unavailable/' in url:raise ValueError('Страница временно недоступна. Вставьте текст вручную.')
    return url,raw
app.state.sources=SourceReader(fetch=source)
uvicorn.run(app,host='127.0.0.1',port=58207,access_log=False,log_level='warning')
