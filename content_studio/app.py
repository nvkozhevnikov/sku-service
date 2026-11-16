from __future__ import annotations

import asyncio
import base64
import hmac
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from .content import CHANNELS, image_bytes, render, sanitize
from .ai_errors import AIAPIError
from .exporting import import_project, make_export
from .storage import Conflict, Store

STATIC=Path(__file__).parent/'static'
MAX_BODY=30*1024*1024


def create_app(directory, login_code=None, *, store=None, ai_vault=None, vk_vault=None, shared=None, base_path=''):
    app=FastAPI(docs_url=None,redoc_url=None,openapi_url=None)
    app.state.store=store if store is not None else Store(directory)
    app.state.shared=shared
    app.state.base_path=base_path
    app.state.vk_vault=vk_vault
    from .ai import AIService
    app.state.ai=AIService(app.state.store,vault=ai_vault)
    app.state.login_code=login_code or secrets.token_urlsafe(16)
    app.state.sessions={}
    app.state.preview_documents={}
    from .source_reader import SourceReader
    app.state.sources=SourceReader()
    failures=[]
    app.mount('/static',StaticFiles(directory=STATIC),name='static')

    @app.middleware('http')
    async def boundary(request,call_next):
        path=request.url.path
        if base_path and path.startswith(base_path):path=path[len(base_path):] or '/'
        host=request.url.hostname
        if shared is None and host not in ('127.0.0.1','localhost','testserver'):
            return JSONResponse({'error':'Недоверенное имя узла.'},403)
        if request.headers.get('sec-fetch-site')=='cross-site':
            return JSONResponse({'error':'Запрос с другого сайта запрещён.'},403)
        origin=request.headers.get('origin')
        if origin and origin!=f'{request.url.scheme}://{request.url.netloc}':
            return JSONResponse({'error':'Недоверенный источник запроса.'},403)
        # Bounded request read, including chunked bodies. Starlette replays the cached body downstream.
        if request.method in ('POST','PUT','PATCH'):
            length=request.headers.get('content-length','')
            try:
                if length and int(length)>MAX_BODY:return JSONResponse({'error':'Запрос больше 30 MiB.'},413)
            except ValueError:return JSONResponse({'error':'Некорректный размер запроса.'},400)
            body=bytearray()
            async for chunk in request.stream():
                body.extend(chunk)
                if len(body)>MAX_BODY:return JSONResponse({'error':'Запрос больше 30 MiB.'},413)
            request._body=bytes(body)
        if shared is None and path.startswith('/api/') and path!='/api/login':
            session=app.state.sessions.get(request.cookies.get('studio_session',''))
            if not session or session['expires']<time.monotonic():return JSONResponse({'error':'Введите код входа из окна запуска.'},401)
            if request.method not in ('GET','HEAD') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),session['csrf']):
                return JSONResponse({'error':'Сессия устарела. Войдите снова.'},403)
        actor_token=None
        if shared is not None:
            from .postgres_store import CURRENT_ACTOR
            from fastapi import HTTPException
            try:
                actor=shared.authorize(request,path)
                request.state.studio_actor=actor
                if request.method in ('POST','PUT','PATCH'):
                    try:value=await request.json()
                    except (ValueError,UnicodeDecodeError):value={}
                    if isinstance(value,dict):await shared.body_guard(request,path,value,actor)
                preview_id=path.rsplit('/',1)[-1] if path.startswith('/api/previews/') else None
                if preview_id:
                    item=app.state.preview_documents.get(preview_id)
                    if item and not app.state.store.access(item['project_id'],actor):raise HTTPException(404,'Предпросмотр недоступен.')
                actor_token=CURRENT_ACTOR.set(actor)
            except HTTPException as exc:return JSONResponse({'error':exc.detail},exc.status_code)
        try:
            response=await call_next(request)
        except Exception:
            # Never stringify arbitrary exceptions/requests into UI or access logs.
            return JSONResponse({'error':'Локальная операция не выполнена. Предыдущая сохранённая версия доступна.'},500)
        finally:
            if actor_token is not None:CURRENT_ACTOR.reset(actor_token)
        if shared is not None and request.method not in ('GET','HEAD','OPTIONS') and response.status_code<400:
            await asyncio.to_thread(app.state.store.audit,actor,request.method+' '+path)
        if 'content-security-policy' not in response.headers:
            response.headers['Content-Security-Policy']="default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; frame-src 'self' about:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
        response.headers.update({'X-Content-Type-Options':'nosniff','Referrer-Policy':'no-referrer','Cache-Control':'no-store'})
        return response

    for typ,status in ((ValueError,400),(KeyError,404),(Conflict,409)):
        async def error(request,exc,_status=status):
            return JSONResponse({'error':str(exc).strip("'"),'conflict':_status==409},_status)
        app.add_exception_handler(typ,error)

    @app.exception_handler(AIAPIError)
    async def ai_api_error(request, exc):
        return JSONResponse({'error': str(exc), 'ai_error': exc.public()}, 400)

    async def body(request):
        try:v=await request.json()
        except (ValueError,UnicodeDecodeError):raise ValueError('Ожидается JSON.') from None
        if not isinstance(v,dict):raise ValueError('Ожидается JSON-объект.')
        return v

    @app.get('/')
    def home():
        if not base_path:return FileResponse(STATIC/'index.html',media_type='text/html')
        source=(STATIC/'index.html').read_text(encoding='utf-8')
        source=source.replace('href="/static/','href="'+base_path+'/static/').replace('src="/static/','src="'+base_path+'/static/').replace('href="/api/','href="'+base_path+'/api/')
        source=source.replace('<head>','<head><meta name="studio-base" content="'+base_path+'">')
        source=source.replace('<body>','<body><nav class="integrated-nav"><a href="/">← Unified RC</a><a href="/applications">Приложения</a><strong>Контент-студия</strong></nav>')
        return HTMLResponse(source)

    @app.post('/api/login')
    async def login(request:Request):
        v=await body(request);current=time.monotonic()
        failures[:]=[x for x in failures if x>current-60]
        if len(failures)>=8:return JSONResponse({'error':'Слишком много попыток. Подождите минуту.'},429)
        if not hmac.compare_digest(str(v.get('code','')),app.state.login_code):
            failures.append(current);return JSONResponse({'error':'Неверный код входа.'},401)
        sid=secrets.token_urlsafe(32);csrf=secrets.token_urlsafe(32)
        app.state.sessions[sid]={'csrf':csrf,'expires':current+12*3600}
        result=JSONResponse({'csrf':csrf});result.set_cookie('studio_session',sid,httponly=True,samesite='strict',max_age=43200)
        return result

    @app.get('/api/session')
    def session(request:Request):
        if shared is not None:
            user,csrf=shared.session(request)
            from universal_supplier.control_plane.access_policy import PERMISSIONS
            return {'csrf':csrf,'mode':'UNIFIED_RC','role':user.role,'display_name':user.display_name,
                    'permissions':[p for p in PERMISSIONS if shared.allowed(user,p)],'external_calls':shared.external_calls}
        return {'csrf':app.state.sessions[request.cookies['studio_session']]['csrf'],'mode':'LOCAL_ONLY'}

    @app.get('/api/projects')
    def projects(q:str=''):return app.state.store.list(q)

    @app.get('/api/compose/templates')
    def composition_templates():
        from .composition import PRESETS,VERSION
        return {'version':VERSION,'engine':'local-source-template','templates':{k:{'label':v['label']} for k,v in PRESETS.items()}}

    def generate_values(v):
        from .composition import generate
        generated=generate(v.get('source',''),v.get('title',''),v.get('url',''),v.get('channels'),v.get('preset','news'),v.get('reviewed') is True,v.get('publication_url',''),v.get('hashtags'),v.get('news_mode','rewrite'),v.get('publication_link_mode')!='manual' and ('publication_url' not in v or bool(v.get('publication_url'))))
        provenance=app.state.sources.provenance(v)
        if provenance:generated['metadata']['imported_source']=provenance
        return generated

    @app.post('/api/sources/read')
    async def read_source(request:Request):
        v=await body(request)
        return await asyncio.to_thread(app.state.sources.read,v.get('url'))

    @app.post('/api/sources/validate')
    async def validate_source_url(request:Request):
        from .source_reader import source_url
        return {'url':source_url((await body(request)).get('url'))}

    @app.post('/api/sources/from-browser')
    async def source_from_browser(request:Request):
        return await asyncio.to_thread(app.state.sources.from_browser,await body(request))

    @app.get('/api/sources/browser-module-info')
    def browser_module_info():
        return {'folder':str((STATIC.parent/'browser_import').resolve()),'permissions':'Страницы Sterbrust и локальная Studio. Для нового адреса панели требуется адресная настройка модуля. Без cookie, паролей и публикации.'}

    @app.get('/api/sources/browser-module')
    def browser_module_zip():
        import io,zipfile
        buffer=io.BytesIO();folder=STATIC.parent/'browser_import'
        with zipfile.ZipFile(buffer,'w',zipfile.ZIP_DEFLATED) as archive:
            for name in ('manifest.json','bridge.js','worker.js','README.md'):archive.writestr(name,(folder/name).read_bytes())
        return Response(buffer.getvalue(),media_type='application/zip',headers={'Content-Disposition':'attachment; filename="sterbrust-browser-import.zip"'})

    async def compose_values(v):
        v=await asyncio.to_thread(app.state.sources.resolve,v)
        generated=await asyncio.to_thread(generate_values,v)
        engine=v.get('engine','local')
        if engine=='ai':return await asyncio.to_thread(app.state.ai.generate,v,generated)
        if engine!='local':raise ValueError('Неизвестный способ подготовки.')
        return generated

    def persist_composition(generated, previous=None, expected_revision=None):
        if previous is None:
            initial={'title':generated['title'],'source':generated['source'],'facts':generated['facts']}
            initial['drafts']={c:generated['drafts'].get(c,{'title':'','body':'','announcement':'','seo_title':'','seo_description':'','state':'DRAFT','media':[]}) for c in CHANNELS}
            p=app.state.store.create(generated['title'],initial,generated['metadata'])
        else:
            p=previous
            for c,d in generated['drafts'].items():d['media']=p['drafts'][c]['media'];p['drafts'][c]=d
            p['source']=generated['source'];p['facts']=generated['facts'];p['title']=generated['title']
            p=app.state.store.save(p['id'],p,expected_revision,generated['metadata'])
        return {'project':p,'composition':generated['metadata']}

    async def compose_stream(request, pid=None):
        from .compose_stream import stream_response
        from .composition import generate
        v=await body(request)
        if v.get('engine')!='ai':raise ValueError('Потоковый режим предназначен для подготовки через ИИ.')
        if type(v.get('show_reasoning',True)) is not bool:raise ValueError('Проверьте выбор показа рассуждений.')
        previous=app.state.store.get(pid) if pid else None
        if previous is not None and previous['revision']!=v.get('expected_revision'):
            raise Conflict('Материал изменился. Обновите редакцию перед подготовкой.')
        v=await asyncio.to_thread(app.state.sources.resolve,v)

        def job(sink):
            sink.check()
            generated=generate_values(v)
            generated=app.state.ai.generate(v,generated,on_event=sink)
            return sink.commit(lambda:persist_composition(generated,previous,v.get('expected_revision')))
        return stream_response(job,v.get('show_reasoning',True))

    @app.post('/api/compose/stream')
    async def new_composition_stream(request:Request):return await compose_stream(request)

    @app.post('/api/projects/{pid}/compose/stream')
    async def existing_composition_stream(pid:str,request:Request):return await compose_stream(request,pid)

    @app.get('/api/ai/settings')
    def ai_status():return app.state.ai.status()

    @app.post('/api/ai/settings')
    async def ai_save(request:Request):return await asyncio.to_thread(app.state.ai.save,await body(request))

    @app.post('/api/ai/test')
    async def ai_test(request:Request):
        v=await body(request)
        return await asyncio.to_thread(app.state.ai.test,v.get('version'),v.get('acknowledged'))

    @app.get('/api/ai/gateway')
    def ai_gateway():
        return app.state.ai.gateway.status()

    @app.post('/api/ai/gateway/{action}')
    async def ai_gateway_action(action: str, request: Request):
        gateway = app.state.ai.gateway
        if action.startswith('rules-'):
            return await asyncio.to_thread(gateway.rules.mutate, action[6:], await body(request))
        operations = {
            'profile': gateway.save_profile,
            'default': gateway.save_default,
            'task': gateway.save_task,
            'test-profile': gateway.test_profile,
            'offline-check': app.state.ai.offline_check,
        }
        if action not in operations:
            raise KeyError('Действие ИИ не найдено.')
        return await asyncio.to_thread(operations[action], await body(request))

    @app.post('/api/distribution/validate-links')
    async def validate_distribution_links(request:Request):
        from .distribution import site_link
        from .content import public_url
        v=await body(request)
        source=v.get('url','')
        if not isinstance(source,str) or len(source)>3000 or (source and not public_url(source)):
            return JSONResponse({'error':'Ссылка на источник: укажите публичный HTTPS-адрес без параметров доступа или оставьте поле пустым.', 'field':'compose-url'},400)
        try:
            publication=site_link(v.get('publication_url',''))
        except ValueError as error:
            return JSONResponse({'error':str(error),'field':'compose-publication-url'},400)
        return {'publication_url':publication}

    @app.post('/api/distribution/detect')
    async def detect_distribution(request:Request):
        from .distribution import detect_tags
        v=await body(request)
        return await asyncio.to_thread(detect_tags,v.get('source',''),v.get('title',''))

    @app.post('/api/compose')
    async def compose(request:Request):
        generated=await compose_values(await body(request))
        return persist_composition(generated)

    @app.post('/api/projects/{pid}/compose')
    async def recompose(pid:str,request:Request):
        v=await body(request);p=app.state.store.get(pid)
        if p['revision']!=v.get('expected_revision'):raise Conflict('Материал изменился. Обновите редакцию перед подготовкой.')
        generated=await compose_values(v)
        return persist_composition(generated,p,v.get('expected_revision'))

    @app.get('/api/projects/{pid}/composition')
    def composition_metadata(pid:str):return app.state.store.composition(pid)

    @app.post('/api/projects')
    async def create(request:Request):return app.state.store.create(str((await body(request)).get('title','Новый материал')))

    @app.get('/api/projects/{pid}')
    def get(pid:str):return app.state.store.get(pid)

    @app.post('/api/projects/{pid}/duplicate')
    async def duplicate(pid:str):
        def copy():
            source=app.state.store.get(pid)
            result=import_project(app.state.store,make_export(app.state.store,source,'backup'))
            result['title']=('Копия — '+source['title'])[:250]
            return app.state.store.save(result['id'],result,result['revision'])
        return await asyncio.to_thread(copy)

    @app.put('/api/projects/{pid}')
    async def save(pid:str,request:Request):
        v=await body(request)
        if type(v.get('expected_revision')) is not int:raise ValueError('Нужна версия для сохранения.')
        return app.state.store.save(pid,v.get('project'),v['expected_revision'])

    @app.post('/api/projects/{pid}/approve/{channel}')
    async def approve(pid:str,channel:str,request:Request):
        v=await body(request)
        return app.state.store.approve(pid,channel,v.get('expected_revision'))

    @app.get('/api/projects/{pid}/history')
    def history(pid:str):return app.state.store.history(pid)

    @app.post('/api/projects/{pid}/restore/{revision}')
    async def restore(pid:str,revision:int,request:Request):
        v=await body(request);old=app.state.store.revision(pid,revision);current=app.state.store.get(pid)
        # Retain all media to preserve every historical reference.
        old['media']=current['media'];old['accept_cleaning']=True
        return app.state.store.save(pid,old,v.get('expected_revision'))

    @app.post('/api/sanitize')
    async def clean(request:Request):
        v=await body(request)
        safe,removed=sanitize(str(v.get('html','')),profile='news' if v.get('channel')=='news' else 'default')
        return {'html':safe,'changed':removed}

    @app.post('/api/projects/{pid}/media')
    async def upload(pid:str,request:Request):
        v=await body(request)
        try:raw=base64.b64decode(v.get('data',''),validate=True)
        except (ValueError,TypeError):raise ValueError('Некорректный файл.') from None
        jpeg,meta=await asyncio.to_thread(image_bytes,raw)
        return app.state.store.media_add(pid,raw,jpeg,{**meta,'name':str(v.get('name','Фото'))[:250]},v.get('expected_revision'))

    @app.get('/api/projects/{pid}/media/{aid}')
    def media(pid:str,aid:str):
        p=app.state.store.get(pid)
        if aid not in {a['id'] for a in p['media']}:raise KeyError('Фото не найдено.')
        return FileResponse(app.state.store.root/'media'/f'{aid}.jpg',media_type='image/jpeg')

    def build_preview(pid,channel,v,session):
        if channel not in CHANNELS:raise ValueError('Неизвестный канал.')
        p=app.state.store.get(pid);assets=app.state.store.assets(p)
        # Preview accepts unsaved text but only references committed project assets.
        raw=str(v.get('html',''));rendered=render(raw,assets,'preview',profile=channel) if channel!='vk' else ''
        if channel=='vk':
            from html import escape
            rendered='<div class="vk-preview-copy" style="white-space:pre-wrap">'+escape(raw)+'</div>'
            for aid in v.get('media',p['drafts']['vk']['media']):
                if aid not in assets:raise ValueError('Фото отсутствует в проекте.')
                rendered+=render('<img data-asset-id="'+aid+'" alt="">',assets,'preview')
        if channel!='vk':
            order=v.get('media',p['drafts'][channel]['media'])
            if not isinstance(order,list) or len(order)>30 or not all(isinstance(a,str) and a in assets for a in order) or len(set(order))!=len(order):
                raise ValueError('Некорректный выбор фото для предпросмотра.')
            from html import escape
            for aid in order:
                if f'data-asset-id="{aid}"' not in raw:
                    rendered+=render(f'<img data-asset-id="{aid}" alt="{escape(assets[aid].get("alt",""),quote=True)}">',assets,'preview',profile=channel)
        # A separate sandboxed document has its own restrictive CSP. srcdoc inherits
        # the parent policy, which prevents its inline styles from rendering correctly.
        nonce=secrets.token_urlsafe(20)
        import hashlib
        from html import escape
        from lxml import html as html_parser
        attributes=set()
        if rendered:
            tree=html_parser.fragment_fromstring(rendered,create_parent='div')
            for el in tree.iter():
                if el.get('style'):attributes.add("'sha256-"+base64.b64encode(hashlib.sha256(el.get('style').encode()).digest()).decode()+"'")
        policy="default-src 'none'; script-src 'none'; img-src data:; style-src 'nonce-"+nonce+"'; style-src-attr 'unsafe-hashes' "+' '.join(sorted(attributes))+"; object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'self'; sandbox"
        css=(STATIC/'preview.css').read_text(encoding='utf-8')
        title=str(v.get('title',p['drafts'][channel]['title'] or p['title']))[:10000]
        document='<!doctype html><html lang="ru"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style nonce="'+nonce+'">'+css+'</style><body><h1>'+escape(title)+'</h1>'+rendered+'</body></html>'
        if len(document.encode())>12*1024*1024:raise ValueError('Предпросмотр больше 12 MiB. Уменьшите число фото в версии.')
        current=time.monotonic()
        bucket=app.state.preview_documents
        for key in list(bucket):
            if bucket[key]['expires']<current:bucket.pop(key)
        while len(bucket)>=6:bucket.pop(next(iter(bucket)))
        ident=secrets.token_hex(16);bucket[ident]={'session':session,'html':document,'policy':policy,'expires':current+180}
        bucket[ident]['project_id']=pid
        return {'html':rendered,'preview_url':base_path+'/api/previews/'+ident}

    @app.post('/api/projects/{pid}/preview/{channel}')
    async def preview(pid:str,channel:str,request:Request):
        return build_preview(pid,channel,await body(request),request.cookies.get('universal_supplier_session' if shared else 'studio_session',''))

    @app.get('/api/projects/{pid}/preview-window/{channel}')
    def preview_window(pid:str,channel:str,request:Request):
        if channel not in CHANNELS:raise ValueError('Неизвестный канал.')
        p=app.state.store.get(pid);d=p['drafts'][channel]
        result=build_preview(pid,channel,{'html':d['body'],'media':d['media'],'title':d['title'] or p['title']},request.cookies.get('universal_supplier_session' if shared else 'studio_session',''))
        item=app.state.preview_documents[result['preview_url'].rsplit('/',1)[1]]
        return HTMLResponse(item['html'],headers={'Content-Security-Policy':item['policy'],'Cache-Control':'no-store'})

    @app.get('/api/previews/{ident}')
    def preview_document(ident:str,request:Request):
        session=request.cookies.get('universal_supplier_session' if shared else 'studio_session','');item=app.state.preview_documents.get(ident)
        if not item or item['session']!=session or item['expires']<time.monotonic():raise KeyError('Предпросмотр устарел. Измените текст или переключите версию для обновления.')
        return HTMLResponse(item['html'],headers={'Content-Security-Policy':item['policy']})

    @app.get('/api/projects/{pid}/export')
    async def export(pid:str,kind:str='transfer'):
        p=app.state.store.get(pid)
        data=await asyncio.to_thread(make_export,app.state.store,p,kind)
        return Response(data,media_type='application/zip',headers={'Content-Disposition':f'attachment; filename="sterbrust-{pid[:8]}-r{p["revision"]}-{kind}.zip"'})

    @app.post('/api/import')
    async def restore_import(request:Request):
        v=await body(request)
        try:raw=base64.b64decode(v.get('data',''),validate=True)
        except (ValueError,TypeError):raise ValueError('Некорректный архив.') from None
        try:return await asyncio.to_thread(import_project,app.state.store,raw)
        except (zipfile.BadZipFile,KeyError,TypeError,json.JSONDecodeError):raise ValueError('Повреждённый пакет проекта.') from None

    @app.post('/api/projects/{pid}/suggest/{channel}')
    async def suggest(pid:str,channel:str,request:Request):
        if channel not in CHANNELS:raise ValueError('Неизвестный канал.')
        p=app.state.store.get(pid)
        facts=[f['text'] for f in p['facts'] if f['state']=='CONFIRMED' and f['text'].strip()]
        if not facts:raise ValueError('Добавьте подтверждённые факты. Локальный шаблон использует только их.')
        from html import escape
        result='\n\n'.join(facts) if channel=='vk' else ''.join('<p>'+escape(x)+'</p>' for x in facts)
        return {'provider':'local-template','revision':p['revision'],'body':result,'state':'DRAFT','note':'Локальный шаблон, без ИИ и сетевых запросов.'}

    # Only the dedicated prepare/confirm use-case may publish. Generic routes stay disabled.
    from .vk_api import register_vk
    register_vk(app,body)

    @app.api_route('/api/publish/{path:path}',methods=['POST','PUT','GET'])
    def disabled(path:str):return JSONResponse({'error':'Используйте подготовку и подтверждение конкретного VK-поста. Запись на сайт отключена.'},403)

    return app


import zipfile
