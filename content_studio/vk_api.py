import asyncio
from fastapi import Request
from fastapi.responses import JSONResponse,Response

from .vk import VkService
from .vk_transport import VkError


def register_vk(app,body):
    service=VkService(app.state.store,vault=app.state.vk_vault);app.state.vk=service
    app.state.vk_tasks=set()

    async def operation(fn,*args):
        try:return await asyncio.to_thread(fn,*args)
        except VkError as e:
            return JSONResponse({'error':'VK не подтвердил операцию. Код ошибки: '+str(e.code or 'ответ не получен'),'error_code':e.code},400)

    @app.get('/api/vk/status')
    def status():return service.status()

    @app.post('/api/vk/connect-desktop')
    async def desktop():return await operation(service.import_desktop)

    @app.post('/api/vk/connect')
    async def connect(request:Request):
        v=await body(request)
        def connect_and_check():
            kind=v.get('kind','community')
            result=service.connect(v.get('token'),kind)
            return service.probe_photos() if kind=='user' else result
        return await operation(connect_and_check)

    @app.post('/api/vk/check-photos')
    async def check():return await operation(service.probe_photos)

    @app.post('/api/vk/prepare/{pid}')
    async def prepare(pid:str,request:Request):
        v=await body(request)
        return await operation(service.prepare,pid,v.get('mode','publish'),v.get('publish_date'))

    @app.get('/api/vk/attempts/{ident}/photos')
    async def photos(ident:str):
        data=await asyncio.to_thread(service.media_package,ident)
        return Response(data,media_type='application/zip',headers={'Content-Disposition':'attachment; filename="vk-photos-'+ident[:8]+'.zip"'})

    @app.post('/api/vk/confirm/{ident}')
    async def confirm(ident:str,request:Request):
        v=await body(request)
        try:claimed=await asyncio.to_thread(service.claim,ident,v.get('digest'),v.get('acknowledged'))
        except VkError as e:return JSONResponse({'error':'Не удалось проверить подключение VK.','error_code':e.code},400)
        if claimed:
            async def work():
                # dispatch catches and durably records failures. Unawaited exceptions are consumed too.
                try:await asyncio.to_thread(service.dispatch,ident)
                except Exception:
                    service._finish(ident,'UNCERTAIN',{'message':'Сбой локальной задачи; перед повтором проверьте VK.'})
            task=asyncio.create_task(work());app.state.vk_tasks.add(task);task.add_done_callback(app.state.vk_tasks.discard)
        return service.get(ident)

    @app.get('/api/vk/attempts')
    def attempts(request:Request):
        rows=service.history()
        return app.state.shared.visible_attempts(rows,request.state.studio_actor) if app.state.shared else rows

    @app.get('/api/vk/attempts/{ident}')
    def attempt(ident:str):return service.get(ident)

    @app.post('/api/vk/attempts/{ident}/record-found')
    async def found(ident:str,request:Request):
        v=await body(request)
        if v.get('acknowledged') is not True:raise ValueError('Подтвердите ручную проверку найденного поста.')
        return service.record_found(ident,v.get('post_id'))
