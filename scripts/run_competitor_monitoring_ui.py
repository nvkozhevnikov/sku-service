"""Existing application, localhost read-only review. No supplier DB/worker bootstrap."""
import os
from pathlib import Path
import sys
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.commercial_qa import DisabledCommercialQaService
from competitor_intel.monitoring import MonitoringService,MonitoringRepository

class LocalReadOnlyStore(UnavailableControlPlaneStore):
    read_only=True
    local_monitoring_review=True

def build_app(enable_collection=False):
    service=MonitoringService(MonitoringRepository(ROOT/'competitor-data/dev-config.json'))
    from competitor_intel.execution import CollectionService
    collection=CollectionService(ROOT/'competitor-data/dev-config.json') if enable_collection else None
    app=create_app(store=LocalReadOnlyStore('Данные поставщиков не подключаются в этом локальном запуске.'),queue=InMemoryJobQueue(),auth_required=False,commercial_qa_service=DisabledCommercialQaService('Локальный просмотр без сборщиков.'),competitor_service=service,competitor_collection=collection)
    app.state.local_operator_collection=enable_collection
    # Same main application; choose its new tab as the local review landing page.
    app.router.routes=[r for r in app.router.routes if not (getattr(r,'path',None)=='/' and 'GET' in getattr(r,'methods',set()))]
    @app.get('/')
    def home():
        from fastapi.responses import RedirectResponse
        return RedirectResponse('/competitor-monitoring',status_code=303)
    @app.middleware('http')
    async def read_only_review(request,call_next):
        if request.method not in ('GET','HEAD') and not (enable_collection and request.method=='POST' and request.url.path=='/competitor-monitoring/runs'):
            from fastapi.responses import PlainTextResponse
            return PlainTextResponse('Локальная версия доступна только для просмотра.',status_code=403)
        if request.url.path!='/' and not request.url.path.startswith(('/competitor-monitoring','/api/competitor-monitoring','/static/')):
            return app.state.monitoring_templates.TemplateResponse(request,'monitoring_local_scope.html',app.state.monitoring_template_context(request,active='',title='Локальный просмотр'))
        return await call_next(request)
    return app

if __name__=='__main__':
    import argparse
    parser=argparse.ArgumentParser();parser.add_argument('--allow-collection',action='store_true');args=parser.parse_args()
    import uvicorn
    uvicorn.run(build_app(args.allow_collection),host='127.0.0.1',port=58124)
