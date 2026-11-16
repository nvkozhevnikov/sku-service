"""Native Studio mount with the existing Unified RC session, never an iframe/link.

No schema creation or SQLite fallback: until the separately authorized migration,
the native destination explains the pending switch instead of inventing a DB.
"""
import os
from pathlib import Path
from fastapi.responses import HTMLResponse, JSONResponse

PREFIX='/content-studio'


class MigrationRequired:
    async def __call__(self,scope,receive,send):
        if '/api/' in scope.get('path',''):
            response=JSONResponse({'error':'Контент-студия подготовлена. Требуется разрешённый перенос SQLite в схему content_studio основной PostgreSQL.','migration_required':True},503)
        else:
            response=HTMLResponse('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Контент-студия</title><body><main><a href="/">← Unified RC</a><h1>Контент-студия</h1><p>Раздел встроен в общую панель. Перенос прежних материалов в PostgreSQL ожидает отдельного разрешения.</p><p>Исходная SQLite и фотографии сохранены. Второй вход и новая пустая база не создаются.</p><a href="/applications">Приложения</a></main></body></html>',503)
        await response(scope,receive,send)


def attach(app,user,payload,allowed,options=None):
    options=dict(options or {})
    target=MigrationRequired();app.state.content_studio_available=False
    store=options.get('store')
    if store is None and os.environ.get('CONTENT_STUDIO_ENABLED')=='YES':
        root=os.environ.get('CONTENT_STUDIO_DATA_DIR')
        parent_store=app.state.store
        if root and hasattr(parent_store,'config'):
            try:
                from content_studio.postgres_store import PostgresStore
                # Same connection identity/config as the existing main store.
                store=PostgresStore(root,parent_store._connect)
            except Exception:
                store=None  # no credentials/DSN exception text in HTTP/logs
    if store is not None:
        from content_studio.app import create_app
        from content_studio.shared_auth import SharedAuth
        from content_studio.secret_store import ServerVault
        ai_vault=options.get('ai_vault');vk_vault=options.get('vk_vault')
        if ai_vault is None or vk_vault is None:
            secret_dir=os.environ.get('CONTENT_STUDIO_SECRETS_DIR')
            if not secret_dir:raise ValueError('CONTENT_STUDIO_SECRETS_DIR_REQUIRED')
            master=os.environ.get('CONTENT_STUDIO_KEY_FILE')
            ai_vault=ai_vault or ServerVault(Path(secret_dir)/'ai',master)
            vk_vault=vk_vault or ServerVault(Path(secret_dir)/'vk',master)
        shared=SharedAuth(user,payload,allowed,store,external_calls=options.get('external_calls',os.environ.get('CONTENT_STUDIO_EXTERNAL_CALLS')=='YES'))
        target=create_app(store.root,store=store,ai_vault=ai_vault,vk_vault=vk_vault,shared=shared,base_path=PREFIX)
        app.state.content_studio_available=True
    app.state.content_studio=target
    app.mount(PREFIX,target,name='content-studio')
