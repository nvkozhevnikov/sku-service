"""Isolated live UI fixtures; never calls an external AI or VK service."""
import json
import sys
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import uvicorn
from content_studio.app import create_app
from content_studio.ai import AIService
from test_vk import FakeVk, MemoryVault, SECRET
from content_studio.vk_transport import VkError


class FakeModel:
    def request(self,config,token,system,user):
        try:data=json.loads(user)
        except ValueError:return '{"ok":true}'
        return self.answer(data)

    def request_stream(self,config,token,system,user,emit):
        data=json.loads(user)
        if not data['source_data'].startswith('QA:none'):
            emit({'type':'reasoning','text':'Учебный пример: проверяю модель и подписи характеристик. <script>опасный код не исполняется</script>\n'})
            time.sleep(.8)
            emit({'type':'reasoning','text':'Сохраняю ограничения исходника и формирую отдельный вариант для выбранного канала.\n'})
        if data['source_data'].startswith('QA:fail'):raise ValueError('Поток AI API оборвался до завершения. Автоповтора нет.')
        emit({'type':'status','text':'Модель передаёт текст материалов.'})
        return self.answer(data)

    @staticmethod
    def answer(data):
        drafts={}
        for channel in data.get('channels',[]):
            body='С установленной защитой ход каретки составляет 280 мм.' if channel=='vk' else '<p>Учебный материал.</p>'
            draft={'title':'Учебный материал BELMASH','body':body}
            if channel=='news':draft.update(body='<div class="sb-news-qa" style="width:100%;box-sizing:border-box;color:#343434"><p>Учебная новость о станке.</p><a href="/contacts/" style="background:#f2722c;color:#ffffff;padding:12px 19px">Связаться</a></div>',
                qa={'facts':'needs_review','editorial':'pass','html_visual':'pass'},issues=['Подтвердите сведения исходника.'])
            drafts[channel]=draft
        return json.dumps({'drafts':drafts})


app=create_app(sys.argv[1],'qa-browser-local')
app.state.vk.client,app.state.vk.vault=FakeVk(),MemoryVault()
app.state.vk.connect(SECRET)
app.state.vk.client.photo_error=VkError(27)
app.state.vk.probe_photos()
app.state.ai=AIService(app.state.store,FakeModel(),MemoryVault())
uvicorn.run(app,host='127.0.0.1',port=58207,access_log=False,log_level='warning')
