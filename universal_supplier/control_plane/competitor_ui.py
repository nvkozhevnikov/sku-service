"""Native main-app read-only routes; existing guard and template context."""
from urllib.parse import urlencode
from fastapi import Request,HTTPException
from fastapi.responses import JSONResponse
from competitor_intel.monitoring import MonitoringUnavailable,money_display,stamp

TABS=[('overview','Обзор'),('changes','Изменения'),('prices','Цены'),('promotions','Акции'),('news','Новости'),('assortment','Ассортимент'),('sources','Источники')]

def register_monitoring(app,templates,context,guard,service,collection=None,check_csrf=None):
    templates.env.filters['monitor_money']=money_display
    templates.env.filters['monitor_time']=lambda v:stamp(v).strftime('%d.%m.%Y %H:%M') if v else '—'
    def render(request,tab):
        guard(request)
        if tab not in dict(TABS):raise HTTPException(404,'Раздел не найден')
        error=None;view=None
        try:view=service.view(tab,dict(request.query_params))
        except MonitoringUnavailable:error='База мониторинга сейчас недоступна. Остальные разделы приложения продолжают работать. Проверьте локальное подключение и повторите просмотр.'
        except ValueError as e:error=str(e)
        def link(page):return '/competitor-monitoring/'+tab+'?'+urlencode({**dict(request.query_params),'page':page})
        def content_link(kind):return '/competitor-monitoring/news?'+urlencode({**dict(request.query_params),'content_type':kind,'page':1})
        from competitor_intel.execution import settings
        automation=collection.view() if collection else {'config':settings(),'last':None,'running':False,'manual':None,'scheduled':None,'installed':False,'next_run_at':None}
        return templates.TemplateResponse(request,'competitor_monitoring.html',context(request,title='Мониторинг конкурентов',active='competitor_monitoring',monitoring_available=True,monitoring=view,monitoring_error=error,monitor_tabs=TABS,monitor_page_link=link,monitor_content_link=content_link,collection_available=collection is not None,automation=automation))
    @app.post('/competitor-monitoring/runs')
    async def collect(request:Request):
        if collection is None:raise HTTPException(403,'Ручной сбор не подключён в read-only preview')
        local_operator=getattr(request.app.state,'local_operator_collection',False) and request.client.host in ('127.0.0.1','::1','testclient')
        guard(request,{'VIEWER','ADMIN'} if local_operator else {'ADMIN'})
        from urllib.parse import urlsplit
        from starlette.concurrency import run_in_threadpool
        from fastapi.responses import RedirectResponse
        expected=urlsplit(str(request.base_url)).netloc
        origin=request.headers.get('origin');referer=request.headers.get('referer')
        if not (origin or referer) or any(urlsplit(v).netloc!=expected or urlsplit(v).scheme not in ('http','https') for v in (origin,referer) if v):raise HTTPException(403,'Недоверенный источник формы')
        from urllib.parse import parse_qs
        content=request.headers.get('content-type','')
        if content and not content.startswith('application/x-www-form-urlencoded'):raise HTTPException(415,'Неподдерживаемый формат формы')
        body=bytearray()
        async for chunk in request.stream():
            body.extend(chunk)
            if len(body)>4096:raise HTTPException(413,'Форма слишком большая')
        values={k:v[-1] for k,v in parse_qs(body.decode('utf-8'),max_num_fields=6).items()}
        if check_csrf:check_csrf(request,values)
        try:
            source=values.get('source') or None
            result=await run_in_threadpool(collection.submit,'OPERATOR_UI',[source] if source else None,int(values['max_pages']) if values.get('max_pages') else None)
        except ValueError:raise HTTPException(400,'Некорректные параметры сбора')
        if result['status']=='SKIP_ALREADY_RUNNING':return JSONResponse({'status':result['status'],'message':'Сбор уже выполняется'},status_code=409)
        if result['status']=='START_BLOCKED_ORPHAN':return JSONResponse({'status':result['status'],'message':'Незавершённый запуск требует проверки; новый сбор не начат'},status_code=409)
        if result['status']=='FAILED' and not result.get('run_id'):return JSONResponse({'status':'FAILED','message':'Запуск независимого процесса завершился ошибкой'},status_code=503)
        if 'application/json' in request.headers.get('accept',''):return JSONResponse({k:result.get(k) for k in ('status','request_id','run_id','origin','started_at')},status_code=202)
        # Clear only the read projection cache after completed persistence.
        if hasattr(service,'cached'):
            with service.lock:service.cached=None;service.expires=0
        return RedirectResponse('/competitor-monitoring/sources',status_code=303)
    @app.get('/api/competitor-monitoring/run')
    def run_status(request:Request):
        guard(request)
        if collection is None:raise HTTPException(403,'Сбор не подключён')
        v=collection.view()
        if (v.get('active') or {}).get('status') in ('COMPLETED','PARTIAL','FAILED') and hasattr(service,'cached'):
            with service.lock:service.cached=None;service.expires=0
        return JSONResponse({k:v.get(k) for k in ('last','active','running','worker_liveness','run_health')})
    @app.get('/competitor-monitoring')
    def overview(request:Request):return render(request,'overview')
    @app.get('/competitor-monitoring/{tab}')
    def page(request:Request,tab:str):return render(request,tab)
    @app.get('/api/competitor-monitoring/changes')
    def changes(request:Request):
        guard(request)
        try:
            v=service.view('changes',dict(request.query_params))
            from competitor_intel.store import json_value
            return JSONResponse(json_value({k:v[k] for k in ('rows','total','page','pages')}))
        except MonitoringUnavailable:return JSONResponse({'error':'Данные мониторинга недоступны'},status_code=503)
        except ValueError:return JSONResponse({'error':'Некорректный период'},status_code=400)
