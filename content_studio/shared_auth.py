"""Shared-session adapter: callbacks resolve the real Unified RC user each request."""
import hmac
import re
from fastapi import HTTPException
from .postgres_store import CURRENT_ACTOR


def permission(path,method):
    write=method not in ('GET','HEAD','OPTIONS')
    if '/export' in path or path.endswith('/photos') or path=='/api/sources/browser-module':return 'content.export'
    if path=='/api/login' or path=='/api/vk/connect-desktop':return 'accounts.admin'
    if path.startswith('/api/ai/gateway/rules-'):return 'content.rules.manage'
    if path.startswith('/api/ai/'):
        if not write or path.endswith('/offline-check'):return 'content.read'
        return 'accounts.admin'
    if path.startswith(('/api/vk/connect','/api/vk/check-photos')):return 'accounts.admin'
    if path.startswith('/api/vk/'):
        return 'content.vk.publish' if write else 'content.read'
    if '/media' in path and write:return 'content.media'
    return 'content.edit' if write and path.startswith('/api/') else 'content.read'


class SharedAuth:
    def __init__(self,user,payload,allowed,store,*,external_calls=False):
        self.user=user;self.payload=payload;self.allowed=allowed;self.store=store
        self.external_calls=external_calls

    def session(self,request):
        user=self.user(request)
        if not user:raise HTTPException(401,'Требуется вход Unified RC.')
        if user.must_change_password:raise HTTPException(403,'Сначала измените пароль в Unified RC.')
        token=self.payload(request)
        return user,token.get('csrf','')

    def authorize(self,request,path):
        user,csrf=self.session(request)
        needed=permission(path,request.method)
        if path in ('/api/login','/api/vk/connect-desktop'):
            raise HTTPException(403,'Встроенный вход и чтение файла рабочего стола отключены. Используйте Unified RC.')
        if needed=='accounts.admin':valid=user.role=='ADMIN'
        else:valid=self.allowed(user,needed)
        if not valid:raise HTTPException(403,'Недостаточно прав на это действие в Контент-студии.')
        if request.method not in ('GET','HEAD','OPTIONS') and not hmac.compare_digest(request.headers.get('x-csrf-token',''),csrf):
            raise HTTPException(403,'Сессия устарела. Обновите страницу Unified RC.')
        project=re.search(r'^/api/projects/([0-9a-f]{32})(?:/|$)',path)
        if not project:project=re.search(r'^/api/vk/prepare/([0-9a-f]{32})$',path)
        write=request.method not in ('GET','HEAD','OPTIONS')
        if project and not self.store.access(project[1],user,write):raise HTTPException(404,'Материал не найден или недоступен.')
        attempt=re.search(r'^/api/vk/(?:attempts|confirm|reconcile)/([0-9a-f]{32})(?:/|$)',path)
        if attempt:
            with self.store.connection() as c:row=c.execute('SELECT project_id FROM vk_intents WHERE id=?',(attempt[1],)).fetchone()
            if not row or not self.store.access(row[0],user,write):raise HTTPException(404,'Отправка не найдена или недоступна.')
        return user

    async def body_guard(self,request,path,value,user):
        if path.startswith('/api/compose') or '/compose' in path:
            if value.get('engine')=='ai' and not self.allowed(user,'content.ai.use'):
                raise HTTPException(403,'Нет права использовать ИИ.')
        external=(path in ('/api/ai/test','/api/ai/gateway/test-profile','/api/vk/connect','/api/vk/check-photos')
                  or path.startswith('/api/vk/confirm/') or path=='/api/sources/read'
                  or value.get('engine')=='ai' and ('/compose' in path))
        if external and not self.external_calls:
            raise HTTPException(403,'Внешние вызовы в этом контуре отключены. Настройки и локальные шаблоны доступны.')

    def visible_attempts(self,rows,user):
        return [r for r in rows if self.store.access(r['project_id'],user)]
