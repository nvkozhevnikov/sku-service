import asyncio
import io
import json
import threading
import time
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from content_studio.ai import AIClient
from content_studio.ai_errors import AIAPIError
from content_studio.ai_stream import read_chat_stream, ReasoningText, StreamCancelled, MAX_REASONING
from content_studio.compose_stream import stream_response
from content_studio.content import sanitize, safe_news_link, render
from content_studio.exporting import make_export
from test_distribution_ai import ai_setup, KEY, SOURCE


def frame(delta=None, finish=None):
    return {'choices':[{'index':0,'delta':delta or {},'finish_reason':finish}]}


def wire(events, done=True):
    text=''.join('data: '+json.dumps(e,ensure_ascii=False)+'\r\n\r\n' for e in events)
    if done:text+='data: [DONE]\r\n\r\n'
    return io.BytesIO(text.encode())


@pytest.mark.parametrize('cut', [1,5,16,25,40])
def test_stream_redacts_secret_across_events_and_preserves_unicode(cut):
    events=[]
    source=wire([frame({'role':'assistant'}),frame({'reasoning_content':'Проверяю факты. '+KEY[:cut]}),
                 frame({'reasoning_content':KEY[cut:]+' Сравниваю модели.'}),
                 frame({'content':'{"drafts":'}),frame({'content':'{}}'}),frame(finish='stop'),{'choices':[],'usage':{}}])
    text=read_chat_stream(source,'https://api.z.ai/api/coding/paas/v4',KEY,events.append)
    thoughts=''.join(e.get('text','') for e in events if e['type']=='reasoning')
    assert text=='{"drafts":{}}' and 'Проверяю факты' in thoughts and 'Сравниваю модели' in thoughts
    assert KEY not in thoughts and '[ключ скрыт]' in thoughts


def test_reasoning_limit_does_not_truncate_final_answer():
    events=[]
    text=read_chat_stream(wire([frame({'reasoning_content':'я'*20000}),frame({'reasoning_content':'я'*20000}),frame({'reasoning_content':'б'*20000}),frame({'reasoning_content':'б'*20000}),
        frame({'content':'ok'}),frame(finish='stop')]),'https://api.z.ai',KEY,events.append)
    assert text=='ok' and sum(len(x['text']) for x in events if x['type']=='reasoning')==MAX_REASONING
    assert sum(x['type']=='reasoning_limit' for x in events)==1


@pytest.mark.parametrize('events,done', [([frame({'content':'partial'})],False),
    ([frame({'content':'partial'}),frame(finish='length')],True),
    ([frame(finish='stop')],True),([frame({'content':'ok'}),frame(finish='stop')],False)],
    ids=['early-eof','token-limit','empty','missing-done'])
def test_incomplete_stream_is_never_returned_as_answer(events,done):
    with pytest.raises(ValueError):read_chat_stream(wire(events,done),'https://api.z.ai',KEY,lambda _:None)


def test_stream_error_does_not_echo_provider_body():
    with pytest.raises(AIAPIError) as exc:
        read_chat_stream(wire([{'error':{'code':'1113','message':KEY}}]),'https://api.z.ai/api/paas/v4',KEY,lambda _:None)
    assert exc.value.code=='1113' and KEY not in str(exc.value)


def test_stream_payload_transport_and_json_fallback_use_one_request():
    cfg={'base_url':'https://api.z.ai/api/coding/paas/v4','api_type':'openai','model':'glm-5.3',
         'token_limit':4096,'token_parameter':'max_tokens','json_mode':True}
    class Response(io.BytesIO):
        status=200
        def getheader(self,_):return 'text/event-stream'
    response=Response(wire([frame({'reasoning_content':'Ход проверки.'}),frame({'content':'ok'}),frame(finish='stop')]).getvalue())
    with patch('content_studio.ai.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('content_studio.ai.http.client.HTTPSConnection') as connection:
        connection.return_value.getresponse.return_value=response
        events=[]
        assert AIClient().request_stream(cfg,KEY,'JSON','source',events.append)=='ok'
        assert json.loads(connection.return_value.request.call_args.kwargs['body'])['stream'] is True
        connection.return_value.request.assert_called_once();connection.return_value.close.assert_called_once()
    class JSONResponse:
        status=200
        def getheader(self,_):return 'application/json'
        def read(self,_):return json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'ok','reasoning_content':KEY+' private reasoning'}}]}).encode()
    with patch('content_studio.ai.socket.getaddrinfo',return_value=[(2,1,6,'',('8.8.8.8',443))]),patch('content_studio.ai.http.client.HTTPSConnection') as connection:
        connection.return_value.getresponse.return_value=JSONResponse();events=[]
        assert AIClient().request_stream(cfg,KEY,'JSON','source',events.append)=='ok'
        assert KEY not in json.dumps(events);connection.return_value.request.assert_called_once()


def stream_fixture(tmp_path, fail=False):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    calls=[]
    def stream(config,token,system,user,emit):
        calls.append(json.loads(user)['channels'])
        emit({'type':'reasoning','text':'UNSAVED_TRACE <script>not html</script>'})
        if fail:raise ValueError('Поток AI API оборвался до завершения.')
        return json.dumps({'drafts':{c:{'title':'QA','body':'готово' if c=='vk' else '<p>готово</p>'} for c in calls[-1]}})
    fake.request_stream=stream
    options={'source':SOURCE,'channels':['vk','news','article'],'engine':'ai','ai_version':cfg['version'],'ai_acknowledged':True}
    return app,client,headers,options,calls


def decoded(response):return [json.loads(line) for line in response.content.splitlines()]


def test_stream_api_atomic_commit_and_no_trace_in_project_or_export(tmp_path):
    app,client,headers,options,calls=stream_fixture(tmp_path)
    events=decoded(client.post('/api/compose/stream',json=options,headers=headers))
    result=next(x['result'] for x in events if x['type']=='result')
    assert len(calls)==1 and any(x['type']=='reasoning' for x in events)
    p=app.state.store.get(result['project']['id'])
    assert 'UNSAVED_TRACE' not in json.dumps(p) and all(d['state']=='DRAFT' for d in p['drafts'].values())
    assert 'UNSAVED_TRACE' not in json.dumps(app.state.store.composition(p['id']))
    with zipfile.ZipFile(io.BytesIO(make_export(app.state.store,p,'backup'))) as z:
        assert all(b'UNSAVED_TRACE' not in z.read(name) for name in z.namelist())


def test_stream_failure_and_disabled_trace_and_missing_consent(tmp_path):
    app,client,headers,options,calls=stream_fixture(tmp_path,fail=True)
    events=decoded(client.post('/api/compose/stream',json=options,headers=headers))
    assert any(x['type']=='error' for x in events) and not any(x['type']=='result' for x in events) and not app.state.store.list()
    events=decoded(client.post('/api/compose/stream',json={**options,'show_reasoning':False},headers=headers))
    assert not any(x['type']=='reasoning' for x in events)
    count=len(calls)
    events=decoded(client.post('/api/compose/stream',json={**options,'ai_acknowledged':False},headers=headers))
    assert len(calls)==count and any(x['type']=='error' for x in events)
    assert client.post('/api/compose/stream',json=options).status_code==403
    other=TestClient(app)
    assert other.post('/api/compose/stream',json=options).status_code==401


def test_disconnected_stream_cancels_before_commit():
    committed=[];finished=threading.Event()
    def job(sink):
        try:
            sink({'type':'reasoning','text':'early'})
            time.sleep(.15)
            return sink.commit(lambda:committed.append(True))
        finally:finished.set()
    async def run():
        response=stream_response(job)
        iterator=response.body_iterator
        assert b'start' in await anext(iterator)
        assert b'early' in await anext(iterator)
        await iterator.aclose()
        await asyncio.to_thread(finished.wait,2)
    asyncio.run(run())
    assert finished.is_set() and not committed


def test_rules_crud_scope_version_and_trash(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    state=client.get('/api/ai/gateway').json()
    def act(action,**data):
        nonlocal state
        result=client.post('/api/ai/gateway/rules-'+action,json={'version':state['version'],'channel':'news',**data},headers=headers)
        assert result.status_code==200,result.text
        state=result.json();return state
    original_vk=next(x['route']['instructions'] for x in state['tasks'] if x['id']=='vk')
    act('import');rules=state['editorial_rules']['news'];assert len(rules)==12
    assert all('sterbrust.ru' not in r['text'] for r in rules)
    assert next(x['route']['instructions'] for x in state['tasks'] if x['id']=='vk')==original_vk
    assert next(x['route']['rules_count'] for x in state['tasks'] if x['id']=='article')==0
    old=state['version'];act('save',title='Своё правило',text='Пиши ясно.',enabled=True)
    rid=state['editorial_rules']['news'][-1]['id'];act('move',id=rid,direction=-1)
    act('delete',id=rid);assert state['editorial_rules_trash']['news'][-1]['id']==rid
    act('restore',id=rid);act('delete',id=rid)
    bad=client.post('/api/ai/gateway/rules-purge',json={'version':state['version'],'channel':'news','id':rid},headers=headers)
    assert bad.status_code==400
    act('purge',id=rid,acknowledged=True)
    stale=client.post('/api/ai/gateway/rules-import',json={'version':old,'channel':'news'},headers=headers)
    assert stale.status_code==409 and not fake.calls
    act('import',channel='vk');assert len(state['editorial_rules']['vk'])==8


@pytest.mark.parametrize('link', ['//evil.example/path','https://sterbrust.ru.evil.example/catalog/a/','/catalog/../bitrix/admin/',
    '/catalog/%2e%2e/bitrix/','javascript:alert(1)','https://sterbrust.ru/catalog/a/?delete=1'])
def test_news_links_fail_closed(link):assert not safe_news_link(link)


def test_news_inline_styles_links_and_export_are_preserved_only_for_news(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    body='<div class="sb-news-qa" style="width:100%;box-sizing:border-box;color:#343434"><p>Новость.</p><a href="/contacts/" style="background:#f2722c;color:#ffffff;padding:12px 19px">Связаться</a></div>'
    safe,_=sanitize(body,profile='news');default,_=sanitize(body)
    assert '#f2722c' in safe and 'href="/contacts/"' in safe and 'sb-news-qa' in safe
    assert '#f2722c' not in default and 'href="/contacts/"' not in default
    assert 'https://sterbrust.com/contacts/' in render(safe,{},'preview',profile='news')
    p=app.state.store.create('news');p['drafts']['news']['body']=safe
    p=app.state.store.save(p['id'],p,p['revision'])
    with zipfile.ZipFile(io.BytesIO(make_export(app.state.store,p,'transfer'))) as z:
        assert any(b'#f2722c' in z.read(n) and b'/contacts/' in z.read(n) for n in z.namelist() if n.endswith('.html'))


def test_channel_rules_are_applied_only_to_corresponding_model_request(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    def reply(config,token,system,user):
        fake.calls.append((config,token,system,user))
        return json.dumps({'drafts':{c:{'title':'QA','body':'Готово' if c=='vk' else '<p>Готово</p>'}
            for c in json.loads(user)['channels']}})
    fake.request=reply
    state=client.get('/api/ai/gateway').json()
    for channel,marker in [('news','NEWS_ONLY_MARK'),('vk','VK_ONLY_MARK')]:
        r=client.post('/api/ai/gateway/rules-save',json={'version':state['version'],'channel':channel,
            'title':marker,'text':marker,'enabled':True},headers=headers)
        assert r.status_code==200;state=r.json()
    r=client.post('/api/compose',json={'source':SOURCE,'channels':['vk','news','article'],'engine':'ai',
        'ai_gateway_version':state['version'],'ai_acknowledged':True},headers=headers)
    assert r.status_code==200,r.text
    assert len(fake.calls)==3
    for call in fake.calls:
        data=json.loads(call[3]);system=call[2]
        assert ('NEWS_ONLY_MARK' in system)==(data['channels']==['news'])
        assert ('VK_ONLY_MARK' in system)==(data['channels']==['vk'])
    assert r.json()['composition']['news_review']['facts']=='needs_review'
    assert len(r.json()['composition']['editorial_rules']['news'])==1


@pytest.mark.parametrize('body', ['<h1>title</h1>', '<script>alert(1)</script><p>test</p>',
    '<a href="//evil.example/a">bad</a>','<a href="/catalog/not-in-source/">invented</a>',
    '<div style="width:calc(100% + 20px)">wide</div>','<div class="article-toc">toc</div>'])
def test_news_model_output_rejects_forbidden_structures(body):
    from content_studio.ai import AIService
    with pytest.raises(ValueError):AIService.decode_drafts(json.dumps({'drafts':{'news':{'body':body}}}),['news'])
