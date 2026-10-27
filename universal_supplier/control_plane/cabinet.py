"""Operator views over the existing manual service, LIVE11 and competitor readers.

The databases stay separate. This module has no enqueue, ingest or activation code.
"""
from datetime import datetime, timezone
import csv
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import UUID

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from jinja2 import ChoiceLoader, FileSystemLoader

ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT.parent
FEATURE = WORK / 'sku-service-unified-rc-20261019'
COMPETITOR = WORK / 'sterbrust-competitive-intelligence'
MANUAL = {'beka_mak', 'beka_mak_tr', 'intervesp'}
SOURCES = ('partner_st', 'optimum', 'beka_mak', 'beka_mak_tr', 'intervesp',
           'vekprom', 'kami', 'ltt_msk', 'ltt_sib', 'ltt_yug', 'uss_stanko')
REASONS = {
    'LIVE11_TARGET_NOT_CONNECTED_OR_IDENTITY_UNVERIFIED':
        'Target 55461 не подключён или его идентичность не подтверждена. Штатно запустить именно существующий target; 55449 не подставлять.',
    'BATCH_ROLE_PREREQUISITE_UNRESOLVED':
        'LIVE11 требует rc_runtime без SUPERUSER на exact target55461. Bootstrap rc_owner не принимается; identity и разрешения проверяются backend.',
    'LIVE11_EXPLICIT_APPROVAL_AND_SETUP_REQUIRED':
        'Нужны точное approval пакета, разрешённые enabled11 и host policies. Панель ничего не включает.',
    'BATCH_DISABLED_REQUIRES_EXPLICIT_ENABLE_RESTORE':
        'Supplier disabled. Нужен отдельный разрешённый enable/restore; кнопка не включает источник.',
    'BATCH_HOST_POLICY_REQUIRES_EXPLICIT_ACTIVATION':
        'Host policy отличается от матрицы. Нужна отдельно разрешённая активация точного профиля.',
    'BATCH_NOT_FOUND': 'Пакет с таким ID не найден в целевой базе.',
    'LIVE11_TARGET_CONNECTION_FAILED': 'Нет подключения к exact target 127.0.0.1:55461 / rc_verify_vi_20261019. Проверьте запуск именно существующего target; 55449 не подставляется.',
    'LIVE11_TARGET_AUTH_FAILED': 'Target55461 отклонил авторизацию. Нужен существующий разрешённый runtime; новые роли или обход пароля не выполняются.',
    'LIVE11_TARGET_SCHEMA_UNVERIFIED': 'Target доступен, но SELECT-проверка схемы/данных не прошла. Writer не запускался.',
    'SOURCE_EXPLICIT_SCOPE_REQUIRED': 'Введите общий cap и откройте read-only preview. Scope не берётся из legacy или исторического ID.',
    'SOURCE_PREVIEW_READY': 'Scope проверен существующим backend. Запуск требует отдельной разрешённой write-session и подтверждения digest.',
    'SOURCE_RUNTIME_NOT_ENABLED': 'STERBRUST_SOURCE_RUNTIME_ENABLED не включён в операторской сессии. Его включение не заменяет отдельное разрешение HTTP/SQL.',
    'SOURCE_CAP_EXCEEDS_LIVE11_MATRIX': 'Cap вне разрешённого потолка LIVE11. Уменьшите cap; матрица не расширяется.',
    'SOURCE_BUDGET_EXCEEDS_LIVE11_MATRIX': 'Сохранённые page/time/gate budgets выходят за матрицу. Нужен отдельно согласованный setup существующего источника.',
    'RESUME_SELECT_EXISTING_PARENT': 'Выберите существующий terminal job этого namespace. Новый REFRESH не является RESUME.',
    'UNCERTAIN_OR_FAILED_RESUME': 'Безопасный RESUME не подтверждён: UNCERTAIN/ошибка или отсутствие checkpoint. Автоматического повтора нет.',
    'SOURCE_RESUME_VERIFIED': 'Checkpoint и SQL reconciliation проверены existing backend; scope исходного job сохраняется.',
    'SOURCE_JOB_STATUS': 'Режим наблюдения за существующим job в target55461. Новый scope или запуск не создаётся.',
    'SOURCE_JOB_NOT_FOUND': 'Job с таким ID не найден у выбранного namespace в target55461.',
}


class CabinetService:
    def __init__(self, store, commercial, feature=FEATURE):
        self.store, self.commercial, self.feature = store, commercial, Path(feature)

    def baseline(self):
        return json.loads((ROOT / 'docs/evidence/live11_glm_baseline_20261022.json').read_text(encoding='utf-8'))

    @property
    def write_enabled(self):
        return os.environ.get('STERBRUST_CABINET_LIVE11_WRITE_CONFIRM') == 'rc_verify_vi_20261019'

    def command(self, action, values):
        if not self.write_enabled or action not in {'enqueue', 'stop', 'resume', 'confirm-beka',
                                                   'source-enqueue', 'source-stop', 'source-resume'}:
            raise PermissionError('LIVE11_WRITE_SESSION_NOT_APPROVED')
        result = subprocess.run([sys.executable, str(self.feature / 'scripts/cabinet_batch_read.py'), action],
            cwd=self.feature, input=json.dumps(values), capture_output=True, text=True,
            encoding='utf-8', timeout=30, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
        if result.returncode:
            raise ValueError('LIVE11_COMMAND_FAILED')
        response = json.loads(result.stdout)
        if response.get('error'):
            raise ValueError(response['error'])
        return response

    def source(self, code, mode='REFRESH', limit=None, job_id=None):
        if code not in SOURCES or mode not in {'REFRESH', 'DISCOVERY', 'RESUME'}:
            raise ValueError('SOURCE_NAMESPACE_OR_MODE')
        if limit is not None and (type(limit) is not int or not 1 <= limit <= 100000):
            raise ValueError('SOURCE_LIMIT')
        if job_id is not None and (type(job_id) is not int or job_id < 1):
            raise ValueError('SOURCE_JOB_ID')
        if mode == 'RESUME' and limit is not None:
            raise ValueError('RESUME_SCOPE_CANNOT_BE_REPLACED')
        args = [sys.executable, str(self.feature / 'scripts/cabinet_batch_read.py'), 'source',
                '--source', code, '--mode', mode]
        if limit is not None:
            args += ['--limit', str(limit)]
        if job_id is not None:
            args += ['--job-id', str(job_id)]
        try:
            process = subprocess.run(args, cwd=self.feature, capture_output=True, text=True,
                encoding='utf-8', timeout=15, env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            if process.returncode:
                raise ValueError('SOURCE_READ_ADAPTER_FAILED')
            data = json.loads(process.stdout)
        except (OSError, ValueError, subprocess.TimeoutExpired):
            data = {'code': code, 'mode': mode, 'limit': limit, 'job_id': job_id,
                    'connected': False, 'ready': False, 'source': None, 'jobs': [],
                    'preview': None, 'reason': 'SOURCE_READ_ADAPTER_FAILED'}
        data['explanation'] = REASONS.get(data['reason'], data['reason'])
        data['write_session_enabled'] = self.write_enabled
        data['launch_allowed'] = bool(self.write_enabled and data.get('ready') and data.get('superuser') is False and data.get('role') == 'rc_runtime')
        return data

    def batch(self, batch_id=None):
        args = [sys.executable, str(self.feature / 'scripts/cabinet_batch_read.py'),
                'status' if batch_id else 'preview']
        if batch_id:
            args += ['--batch-id', str(UUID(batch_id))]
        try:
            process = subprocess.run(args, cwd=self.feature, capture_output=True, text=True,
                                     encoding='utf-8', timeout=15,
                                     env={**os.environ, 'PYTHONIOENCODING': 'utf-8'})
            if process.returncode:
                raise ValueError('READ_ADAPTER_UNAVAILABLE')
            result = json.loads(process.stdout)
        except (OSError, subprocess.TimeoutExpired, ValueError):
            result = {'connected': False, 'payload': None, 'matrix': [],
                      'launch_allowed': False, 'reason': 'LIVE11_READ_ADAPTER_UNAVAILABLE'}
        result['explanation'] = REASONS.get(result.get('reason'), result.get('reason'))
        result['write_session_enabled'] = self.write_enabled
        result['launch_allowed'] = bool(self.write_enabled and result.get('connected') and
            result.get('payload') and result.get('superuser') is False and result.get('role') == 'rc_runtime')
        return result

    def sources(self):
        baseline = self.baseline()
        historical = {r['supplier_code']: r for r in baseline['suppliers']}
        error = None
        try:
            current = {r['code']: r for r in self.store.dashboard()}
        except Exception:
            current, error = {}, 'RC55449_NOT_CONNECTED'
        commercial = self.commercial.overview()
        runs = commercial.get('runs', [])
        runtime = self.batch()
        readiness = {r['code']: r for r in runtime.get('sources', [])}
        rows = []
        for code in SOURCES:
            t0, rc = historical[code], current.get(code)
            manual_runs = [r for r in runs if r['source'] in {code, 'all'}]
            latest = manual_runs[0] if manual_runs else None
            row = {'code': code, 'name': t0['supplier_name'], 'url': t0['base_url'],
                   't0': t0, 'rc': rc, 'manual': code in MANUAL, 'latest': latest,
                   'manual_allowed': code in MANUAL and commercial.get('write', {}).get('enabled', False),
                   'manual_reason': commercial.get('write', {}).get('reason', 'Ручной backend не подключён'),
                   'historical_date': t0['observations_to'],
                   'products': rc.get('products') if rc else None,
                   'runtime': readiness.get(code)}
            rows.append(row)
        return {'rows': rows, 'rc_error': error, 'commercial': commercial, 'runtime': runtime,
                'checked_at': datetime.now(timezone.utc).isoformat(), 'baseline': baseline}


def register_cabinet(app, templates, context, guard, service, check_csrf, *, preserve_supplier_landing=False):
    def page(request, active='suppliers', source=None):
        guard(request)
        data = service.sources()
        if source and source not in SOURCES:
            raise HTTPException(404, 'Неизвестный namespace')
        if source:
            data['rows'] = [r for r in data['rows'] if r['code'] == source]
        return templates.TemplateResponse(request, 'cabinet_suppliers.html',
            context(request, active=active, cabinet=data, source_detail=source))
    # Replace only existing landing GETs; keep every supplier/product/run route.
    if not preserve_supplier_landing:
        app.router.routes = [r for r in app.router.routes if not (
            getattr(r, 'path', None) in {'/', '/suppliers'} and 'GET' in getattr(r, 'methods', set()))]
        @app.get('/')
        def home(request: Request):return page(request,'dashboard')
        @app.get('/suppliers')
        def suppliers(request: Request):return page(request)
    @app.get('/cabinet/sources/{code}')
    def source(request: Request, code: str):
        guard(request)
        if code not in SOURCES:
            raise HTTPException(404, 'Неизвестный namespace')
        q = request.query_params
        try:
            mode = q.get('mode', 'REFRESH')
            limit = int(q['limit']) if q.get('limit') else None
            job_id = int(q['job_id']) if q.get('job_id') else None
            data = service.source(code, mode, limit, job_id)
        except ValueError as error:
            raise HTTPException(400, str(error))
        return templates.TemplateResponse(request, 'cabinet_source.html', context(request,
            active='suppliers', source=data))
    @app.get('/api/cabinet/sources/{code}')
    def source_status(request: Request, code: str):
        guard(request)
        try:
            return JSONResponse(service.source(code))
        except ValueError:
            raise HTTPException(400, 'SOURCE_NAMESPACE_OR_MODE')
    @app.post('/cabinet/sources/{code}/{action}')
    async def source_action(request: Request, code: str, action: str):
        guard(request, {'ADMIN', 'OPERATOR'})
        from .web import require_trusted_origin, form_values
        from starlette.concurrency import run_in_threadpool
        require_trusted_origin(request)
        values = await form_values(request)
        check_csrf(request, values)
        if code not in SOURCES or action not in {'enqueue', 'stop', 'resume'}:
            raise HTTPException(404, 'SOURCE_ACTION_NOT_ALLOWED')
        if not service.write_enabled:
            raise HTTPException(403, 'LIVE11_WRITE_SESSION_NOT_APPROVED')
        try:
            command_values = {'source': code, 'mode': values.get('mode', 'RESUME' if action == 'resume' else 'REFRESH')}
            if action == 'enqueue':
                command_values.update(limit=int(values.get('limit', '')),
                    confirmation_sha256=values.get('confirmation_sha256', ''))
            else:
                command_values['job_id'] = int(values.get('job_id', ''))
            result = await run_in_threadpool(service.command, 'source-' + action, command_values)
        except (ValueError, PermissionError) as error:
            raise HTTPException(403, str(error))
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'SOURCE_COMMAND_UNAVAILABLE: проверьте status перед повтором')
        if 'text/html' in request.headers.get('accept', ''):
            job_id = (result.get('result') or {}).get('job_id')
            return RedirectResponse(f'/cabinet/sources/{code}?job_id={job_id}' if job_id else f'/cabinet/sources/{code}', 303)
        return JSONResponse(result)
    @app.get('/live11')
    def batch(request: Request):
        guard(request)
        bid = request.query_params.get('batch_id') or None
        try:
            if bid:
                bid = str(UUID(bid))
            data = service.batch(bid)
        except ValueError:
            raise HTTPException(400, 'Некорректный batch UUID')
        return templates.TemplateResponse(request, 'cabinet_batch.html',
            context(request, active='live11', batch=data, batch_id=bid))
    @app.get('/api/live11/status')
    def batch_status(request: Request, batch_id: str):
        guard(request)
        try:
            return JSONResponse(service.batch(str(UUID(batch_id))))
        except ValueError:
            raise HTTPException(400, 'Некорректный batch UUID')
    @app.post('/live11/{action}')
    async def batch_action(request: Request, action: str):
        guard(request, {'ADMIN', 'OPERATOR'})
        from .web import require_trusted_origin, form_values
        from starlette.concurrency import run_in_threadpool
        require_trusted_origin(request)
        values = await form_values(request)
        check_csrf(request, values)
        if not service.write_enabled:
            raise HTTPException(403, 'LIVE11_WRITE_SESSION_NOT_APPROVED: требуется отдельный допуск точного target; 55449 не используется')
        if action not in {'enqueue', 'stop', 'resume', 'confirm-beka'}:
            raise HTTPException(404, 'Неизвестное действие')
        try:
            approval = json.loads(values.get('approval', '{}')) if action == 'enqueue' else {
                'batch_id': str(UUID(values.get('batch_id', '')))}
            if action == 'confirm-beka':
                approval.update(receipt_json=values.get('receipt_json', ''),
                                receipt_sha256=values.get('receipt_sha256', ''))
            result = await run_in_threadpool(service.command, action, approval)
        except (ValueError, PermissionError) as error:
            raise HTTPException(403, str(error))
        except (OSError, subprocess.TimeoutExpired):
            raise HTTPException(503, 'LIVE11_COMMAND_UNAVAILABLE: проверьте status перед повтором')
        if 'text/html' in request.headers.get('accept', ''):
            result_value = result.get('result') or {}
            batch_id = result_value.get('batch_id') if isinstance(result_value, dict) else None
            batch_id = str(UUID(batch_id or approval['batch_id']))
            return RedirectResponse('/live11?batch_id=' + batch_id, 303)
        return JSONResponse(result)
    @app.get('/cabinet/quality')
    def quality(request: Request):
        guard(request)
        priority_path = ROOT / 'docs/evidence/glm_review_priority_20261022.csv'
        with priority_path.open(encoding='utf-8-sig', newline='') as handle:
            priority = list(csv.DictReader(handle))
        return templates.TemplateResponse(request, 'cabinet_quality.html', context(request,
            active='quality', baseline=service.baseline(), priority=priority))
    @app.get('/live11/jobs/{job_id}/evidence/{kind}')
    def evidence(request: Request, job_id: int, kind: str, source: str):
        guard(request)
        filenames = {'input': 'input.json', 'checkpoint': 'checkpoint.json',
                     'result': 'result.json', 'stop': 'FIRST_STOP.json'}
        if job_id < 1 or source not in SOURCES or kind not in filenames:
            raise HTTPException(404, 'Evidence не найдено')
        root = Path(os.environ.get('SOURCE_RUNNER_EVIDENCE_ROOT', service.feature / 'reports/source-runner-evidence'))
        path = root / source / f'job-{job_id}' / filenames[kind]
        try:
            if path.stat().st_size > 4_000_000:
                raise HTTPException(413, 'Evidence превышает лимит просмотра; открыть локальный файл')
            data = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            raise HTTPException(404, 'Evidence ещё не создано или owner checkpoint находится у родительского job')
        return templates.TemplateResponse(request, 'cabinet_evidence.html', context(request,
            active='live11', evidence=data, evidence_title=f'{source} · job {job_id} · {kind}'))
    @app.get('/news')
    def news(request: Request):
        guard(request)
        return RedirectResponse('/competitor-monitoring/news', 303)
    @app.get('/cabinet/competitor-runs')
    def competitor_runs(request: Request):
        guard(request)
        rows, error = [], None
        try:
            from competitor_intel.store import Store, json_value
            config = json.loads((COMPETITOR / 'competitor-data/dev-config.json').read_text(encoding='utf-8'))
            repository = Store(config['dsn'])
            try:
                repository.conn.read_only = True
                with repository.conn.transaction(), repository.conn.cursor() as cursor:
                    cursor.execute('SELECT id,started_at,finished_at,status,since_date,result FROM competitor_runs ORDER BY id DESC LIMIT 100')
                    rows = json_value(cursor.fetchall())
            finally:
                repository.close()
        except Exception:
            error = 'NOT_CONNECTED: журнал competitor backend недоступен'
        return templates.TemplateResponse(request, 'cabinet_competitor_runs.html', context(request,
            active='competitor_monitoring', competitor_runs=rows, competitor_error=error))

    try:
        # Reuse the existing repository, normalized business view and routes.
        # No CollectionService/worker is constructed in this read-only bridge.
        if str(COMPETITOR) not in sys.path:
            sys.path.append(str(COMPETITOR))
        from competitor_intel.monitoring import MonitoringRepository, MonitoringService
        spec = importlib.util.spec_from_file_location('cabinet_existing_competitor_ui',
            COMPETITOR / 'universal_supplier/control_plane/competitor_ui.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        templates.env.loader = ChoiceLoader([templates.env.loader,
            FileSystemLoader(str(COMPETITOR / 'universal_supplier/control_plane/templates'))])
        for route in app.routes:
            if getattr(route, 'path', None) == '/static':
                route.app.all_directories.append(str(COMPETITOR / 'universal_supplier/control_plane/static'))
        module.register_monitoring(app, templates, context, guard,
            MonitoringService(MonitoringRepository(COMPETITOR / 'competitor-data/dev-config.json')))
        app.state.cabinet_competitor_connected = True
    except (ImportError, OSError):
        app.state.cabinet_competitor_connected = False
        @app.get('/competitor-monitoring/{tab:path}')
        def disconnected(request: Request, tab: str):
            guard(request)
            return templates.TemplateResponse(request, 'error.html', context(request,
                message='NOT_CONNECTED: existing competitor backend недоступен', details='', active=''))
