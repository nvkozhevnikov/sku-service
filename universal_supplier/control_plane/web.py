from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import logging
import os
import json
import re
import secrets
from pathlib import Path
from urllib.parse import parse_qs, quote, urlparse
import tempfile
import time
import uuid

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.concurrency import run_in_threadpool

from universal_supplier.postgres import PostgresConfig
from universal_supplier.stage6e import (
    InMemoryStage6EService, PostgresStage6EService, PRODUCT_COLUMNS, csv_bytes, csv_chunks,
    effective_crawl_settings, product_workbook, safe_export_filename, validate_supplier_url,
    validate_fetch_targets, xlsx_bytes,
)
from universal_supplier.url_safety import is_http_url
from universal_supplier.xml_export import XmlExportService
from .admin_store import InMemoryAdminStore, PostgresAdminStore
from .commercial_qa import CommercialQaService, CommercialQaUnavailable
from .queue import InMemoryJobQueue, PostgresJobQueue
from .rate_control import DomainRatePolicy, OPTIMUM_DEFAULT_RATE_POLICY, PARTNER_ST_DEFAULT_RATE_POLICY
from .scheduler import controls_to_schedule, schedule_to_controls, validate_schedule
from .security import AuthUser, LoginThrottle, PostgresLoginThrottle, SessionSigner, dummy_password_hash, hash_password, verify_password
from .store import MAX_PAGE, PostgresControlPlaneStore, ProductFilters, SnapshotControlPlaneStore, UnavailableControlPlaneStore

LOGGER = logging.getLogger(__name__)
PACKAGE = Path(__file__).resolve().parent
ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_WEB_CRAWL_TYPES = frozenset({"full"})
MUTATING_ROLES = {"ADMIN", "OPERATOR"}
RU_LABELS = {
    "queued":"В очереди", "running":"Выполняется", "succeeded":"Успешно", "partial":"Частично",
    "failed":"Ошибка", "cancelled":"Отменён", "skipped_locked":"Пропущен: источник занят",
    "skipped_already_queued":"Пропущен: уже в очереди", "stale_failed":"Прерван после сбоя",
    "OPEN":"Открыто", "ASSIGNED":"Назначено", "POSTPONED":"Отложено", "RESOLVED":"Решено",
    "REOPENED_SOURCE_CHANGED":"Изменилось после проверки", "REVIEW_EXISTING":"Проверить существующий товар",
    "CONFLICT":"Противоречивые признаки", "INSUFFICIENT_IDENTITY":"Недостаточно данных",
    "SAFE_NEW_PRODUCT_CANDIDATE":"Возможно, новый товар", "ACCESSORY_OR_COMPATIBILITY_ITEM":"Аксессуар или совместимость",
    "EXACT_EXISTING":"Точное существующее совпадение", "HIGH_CONFIDENCE_EXISTING":"Надёжное существующее совпадение",
    "MANUAL_CONFIRMED":"Подтверждено вручную", "MANUAL_CONFIRMED_NEW":"Подтверждён новый товар",
    "MANUAL_ACCESSORY":"Подтверждён аксессуар", "MARKED_REVIEWED":"Просмотрено",
    "ADMIN":"Администратор", "OPERATOR":"Оператор", "VIEWER":"Наблюдатель",
    "USER_CREATED":"Создан пользователь", "USER_ACTIVE_CHANGED":"Изменено состояние пользователя", "USER_SESSIONS_REVOKED":"Завершены сессии пользователя",
    "SUPPLIER_ENABLED_CHANGED":"Изменено состояние поставщика", "SCHEDULE_CHANGED":"Изменено расписание",
    "MANUAL_CRAWL_ENQUEUED":"Ручной сбор поставлен в очередь", "CANDIDATE_REJECTED":"Кандидат отклонён",
    "REVIEW_REQUIRED_SOURCE_CHANGED":"Требуется повторная проверка после изменения",
    "LOGIN_SUCCEEDED":"Успешный вход", "LOGIN_FAILED":"Неудачный вход",
}

PRODUCT_KIND_LABELS = {
    "accessory": "Аксессуар", "beveling_machine": "Кромкооблицовочный станок",
    "chuck": "Патрон", "collet": "Цанга", "controller": "Контроллер",
    "disk": "Диск", "drill_press": "Сверлильный станок", "fixture": "Оснастка",
    "grinder": "Заточной станок", "handle": "Ручка управления", "jaw": "Губки",
    "lathe": "Токарный станок", "machine": "Оборудование / станок",
    "manipulator": "Манипулятор", "milling_machine": "Фрезерный станок",
    "stand": "Подставка", "table": "Стол", "tool": "Инструмент",
    "unknown": "Другой тип", "vise": "Тиски",
}
RELATION_TYPE_LABELS = {
    "accessory": "Аксессуар", "related": "Связанный товар",
    "compatible": "Совместимый товар", "replacement": "Замена",
    "navigation": "Вариант товара", "parent_reference": "Родительский товар",
}


def russian_label(value):
    return RU_LABELS.get(str(value), str(value) if value not in {None, ""} else "—")


def product_kind_label(value):
    return PRODUCT_KIND_LABELS.get(str(value), "Другой тип") if value not in {None, ""} else "—"


def relation_type_label(value):
    return RELATION_TYPE_LABELS.get(str(value), "Другая связь") if value not in {None, ""} else "—"


async def form_values(request: Request) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")
    return {key: values[-1] for key, values in parse_qs(body, keep_blank_values=True).items()}


async def form_dependency(request: Request) -> dict[str, str]:
    """Read the form on the event loop so a plain ``def`` handler can run in the threadpool."""
    return await form_values(request)


INTERACTIVE_MAX_ATTEMPTS = 2
INTERACTIVE_REQUEST_TIMEOUT_SECONDS = 15.0
INTERACTIVE_TOTAL_BUDGET_SECONDS = 60.0


def interactive_fetch_policy(policy: DomainRatePolicy) -> DomainRatePolicy:
    """Clamp a supplier policy for one operator-triggered fetch: a panel request must not hang for minutes."""
    return replace(policy,
        max_attempts=min(policy.max_attempts, INTERACTIVE_MAX_ATTEMPTS),
        request_timeout_seconds=min(policy.request_timeout_seconds, INTERACTIVE_REQUEST_TIMEOUT_SECONDS),
        max_run_duration_seconds=min(policy.max_run_duration_seconds, INTERACTIVE_TOTAL_BUDGET_SECONDS))


def interactive_transport(policy: DomainRatePolicy):
    from universal_supplier.http import HttpTransport
    return HttpTransport(domain_policy=interactive_fetch_policy(policy),
                         deadline=time.monotonic() + INTERACTIVE_TOTAL_BUDGET_SECONDS)


def safe_http_url(value) -> str:
    """Return the URL only for http(s) with a host; anything else (javascript:, data:, relative) renders no link."""
    return str(value).strip() if is_http_url(value) else ""


def allowed_hosts() -> list[str]:
    raw = os.environ.get("CONTROL_PLANE_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver")
    values = [value.strip().lower() for value in raw.split(",") if value.strip()]
    return list(dict.fromkeys(values)) or ["127.0.0.1", "localhost", "testserver"]


def require_trusted_origin(request: Request) -> None:
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if host not in allowed_hosts():
        raise HTTPException(403, "Недоверенное имя узла")
    origin = request.headers.get("origin") or request.headers.get("referer")
    if origin:
        from urllib.parse import urlparse
        if (urlparse(origin).hostname or "").lower() not in allowed_hosts():
            raise HTTPException(403, "Недоверенный источник запроса")


def safe_next(value: str | None) -> str:
    """Only same-site absolute paths; anything with scheme/netloc or protocol-relative goes to '/'."""
    value = (value or "").strip()
    if not value.startswith("/") or value.startswith(("//", "/\\")) or "\\" in value or any(ord(c) < 32 for c in value): return "/"
    parsed = urlparse(value)
    return "/" if parsed.scheme or parsed.netloc else value


# At most 18 digits: always fits PostgreSQL bigint.
_ID_RE = re.compile(r"[0-9]{1,18}")


def is_id(value) -> bool:
    """ASCII digits only: str.isdigit() also accepts '²' and other digits int() rejects."""
    return bool(_ID_RE.fullmatch(str(value).strip()))


def optional_int(value: str | None, label: str) -> int | None:
    if value in {None, ""}: return None
    if not is_id(value): raise HTTPException(400, f"Некорректное значение: {label}")
    return int(str(value).strip())


def session_cookie_secure() -> bool:
    """Secure by default; plain HTTP must be opted into explicitly."""
    return os.environ.get("CONTROL_PLANE_INSECURE_HTTP") != "YES"


PUBLIC_COMPONENT_STATES = frozenset({"ok", "stale", "failing"})
HEALTHY_DATABASE_STATES = frozenset({"ok", "demo_snapshot"})


def public_health(status: dict) -> tuple[dict, int]:
    """Unauthenticated /health body: states only. Versions, errors, DB identity go to the log."""
    healthy = status.get("postgresql") in HEALTHY_DATABASE_STATES
    body: dict = {"application": "ok" if status.get("application") == "ok" else "error",
                  "postgresql": "ok" if healthy else "error"}
    components = status.get("components")
    degraded = bool(status.get("degraded")) or components == "unavailable"
    if isinstance(components, dict):
        public = {}
        for name, item in components.items():
            state = item.get("status") if isinstance(item, dict) else None
            # not_running: the loop is silent, which is what "stale" means publicly.
            public[str(name)] = state if state in PUBLIC_COMPONENT_STATES else ("stale" if state == "not_running" else "failing")
        body["components"] = public
        degraded = degraded or any(state != "ok" for state in public.values())
    body["degraded"] = degraded
    if not healthy or degraded:
        LOGGER.warning("health check not ok: %s", status)
    return body, 200 if healthy else 503


def filters_from_request(request: Request, *, forced_decision: str = "") -> ProductFilters:
    q = request.query_params
    try:
        return ProductFilters(supplier=q.get("supplier", ""), brand=q.get("brand", ""),
            decision=forced_decision or q.get("decision", ""), product_kind=q.get("product_kind", ""),
            category=q.get("category", ""), availability=q.get("availability", ""), content=q.get("content", ""),
            media=q.get("media", ""), search=q.get("search", ""), quality_issue=q.get("quality_issue", ""), page=int(q.get("page", "1")),
            page_size=int(q.get("page_size", "50")))
    except (ValueError, TypeError) as error:
        raise HTTPException(400, str(error)) from error


def create_app(*, store=None, queue=None, admin_store=None, stage6e_service=None,
               session_secret: str | None = None, auth_required: bool | None = None,
               commercial_qa_service=None, competitor_service=None, competitor_collection=None,
               release_dispatch=None,cabinet_service=None,content_studio_options=None,manual_launcher=None) -> FastAPI:
    injected = store is not None; config = None
    if store is None:
        if os.environ.get("CONTROL_PLANE_DEMO_SNAPSHOT") == "YES":
            store = SnapshotControlPlaneStore(ROOT / "reports"); queue = queue or InMemoryJobQueue()
        else:
            config = PostgresConfig.from_env(); store = PostgresControlPlaneStore(config); queue = queue or PostgresJobQueue(config)
    queue = queue or InMemoryJobQueue(); auth_required = (not injected) if auth_required is None else auth_required
    if admin_store is None:
        admin_store = PostgresAdminStore(config or store.config) if auth_required and hasattr(store, "config") else InMemoryAdminStore()
    preview_password = os.environ.get("PREVIEW_ADMIN_PASSWORD")
    if preview_password and not injected and hasattr(admin_store, "users_exist") and not admin_store.users_exist():
        admin_store.create_user("preview", "Preview Admin", hash_password(preview_password), "ADMIN")
    if stage6e_service is None:
        stage6e_service = PostgresStage6EService(config or store.config) if isinstance(store,PostgresControlPlaneStore) else InMemoryStage6EService()
    commercial_qa_service = commercial_qa_service or CommercialQaService.from_env()
    xml_service = XmlExportService(store)
    preview_root=Path(os.environ.get('CONTROL_PLANE_EXPORT_ROOT',str(ROOT/'reports/xml-previews')))
    if session_secret:
        signer = SessionSigner(session_secret)
    elif auth_required:
        signer = SessionSigner.from_env()
    else:
        signer = SessionSigner("stage6d-test-only-session-secret-32bytes")
    app = FastAPI(title="Универсальный поставщик — операторская панель", docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
    app.state.store = store; app.state.queue = queue; app.state.admin_store = admin_store; app.state.stage6e_service = stage6e_service
    app.state.auth_required = auth_required; app.state.direct_crawl_executions = 0
    app.state.commercial_qa_service = commercial_qa_service
    app.state.competitor_monitoring_available = competitor_service is not None
    login_throttle = app.state.login_throttle = PostgresLoginThrottle(admin_store.config,connection_factory=getattr(admin_store,'connection_factory',None)) if isinstance(admin_store, PostgresAdminStore) else LoginThrottle()
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    templates.env.filters["ru"] = russian_label
    templates.env.globals['safe_http_url']=safe_http_url
    templates.env.filters["product_kind_ru"] = product_kind_label
    from .presentation import phase_summary, price_label, duration_label, mark_quality_repeats, display_timestamp, moscow_datetime, supplier_display_name, supplier_status, job_message
    templates.env.globals.update(phase_summary=phase_summary,price_label=price_label,duration_label=duration_label)
    templates.env.filters['msk'] = display_timestamp
    from .presentation import compact_number, collection_notice, error_presentation
    from .presentation import run_collection_progress
    templates.env.globals['run_collection_progress'] = run_collection_progress
    templates.env.filters['number'] = compact_number
    templates.env.globals['collection_notice'] = collection_notice
    templates.env.globals.update(supplier_display_name=supplier_display_name, supplier_status=supplier_status, moscow_datetime=moscow_datetime, job_message=job_message)
    templates.env.filters["relation_ru"] = relation_type_label
    templates.env.filters["safe_http_url"] = safe_http_url

    def payload(request: Request):
        return signer.read(request.cookies.get(signer.cookie_name)) if auth_required else {"uid": 0, "role": "ADMIN", "csrf": "test-csrf"}
    def current_user(request: Request):
        data = payload(request)
        if not data: return None
        if not auth_required:
            return AuthUser(0,'local','Локальный просмотр','VIEWER') if getattr(type(store),'local_monitoring_review',False) else AuthUser(0, "test", "Тестовый администратор", "ADMIN")
        user = admin_store.user_by_id(int(data["uid"]))
        # A session is valid only while its session_version matches the user's (bumped on logout/revocation).
        return user if user and user.is_active and user.role == data.get("role") and data.get("sv") == user.session_version else None
    def guard(request: Request, roles: set[str] | None = None):
        user = current_user(request)
        if not user: raise HTTPException(401, "Требуется вход")
        if roles and user.role not in roles: raise HTTPException(403, "Недостаточно прав для этого действия")
        return user
    def check_csrf(request: Request, values: dict[str, str]):
        if auth_required and not signer.csrf_matches(payload(request), values.get("csrf_token")):
            raise HTTPException(403, "Защитный токен формы недействителен. Обновите страницу")
    def context(request: Request, **values):
        data = payload(request)
        read_only_source = bool(getattr(type(store), "read_only", False))
        local_review = getattr(type(store), 'local_monitoring_review', False)
        return {"request": request, "csp_nonce":getattr(request.state,'csp_nonce',''), "current_user": current_user(request), "csrf_token": data.get("csrf", "") if data else "",
            "safety_banner": "СИНТЕТИЧЕСКАЯ QA-БАЗА · НЕ LIVE · СБОР ЗАБЛОКИРОВАН" if os.environ.get('CONTROL_PLANE_QA_INSTANCE')=='YES'
                else "ЛОКАЛЬНЫЙ ПРОСМОТР · ИЗМЕНЕНИЯ И ФОНОВЫЙ СБОР ВЫКЛЮЧЕНЫ" if local_review
                else "ИМПОРТ В STERBRUST ОТКЛЮЧЁН — КОНТРАКТ ИМПОРТА НЕ ПОДТВЕРЖДЁН",
            "local_monitoring_review": local_review,
            "fixture_preview": getattr(type(store), 'fixture_preview', False),
            "commercial_qa_available": bool(getattr(commercial_qa_service, "enabled", False)),
            "final_rc_available": bool(os.environ.get("FINAL_RC_ARTIFACTS_DIR")),
            "monitoring_available": competitor_service is not None,
            "cabinet_available":cabinet_service is not None,
            "release_replay_available": release_dispatch is not None,
            "control_plane_read_only": read_only_source,
            "partner_pilot_enabled":__import__('universal_supplier.partner_pilot',fromlist=['enabled']).enabled(),
            "manifest_sources":__import__('universal_supplier.control_plane.source_jobs',fromlist=['RUNTIME_SOURCES']).RUNTIME_SOURCES,
            "source_runtime_enabled":__import__('universal_supplier.control_plane.source_runtime',fromlist=['runtime_enabled']).runtime_enabled(),
            "manual_collection_enabled":__import__('universal_supplier.control_plane.manual_collection',fromlist=['enabled']).enabled(),
            "source_wrappers":__import__('universal_supplier.control_plane.source_jobs',fromlist=['SOURCES']).SOURCES, **values}

    @app.get('/automatic-matching', response_class=HTMLResponse)
    def automatic_matching(request: Request):
        guard(request)
        import json
        from universal_supplier.automatic_matching import runtime_matching_policy, POLICY_VERSION
        policy = runtime_matching_policy(config or vars(store).get('config'))
        report = None
        report_path = os.environ.get('AUTOMATIC_MATCHING_REPORT')
        if report_path:
            try:
                report = json.loads(Path(report_path).read_text(encoding='utf-8'))
                if report.get('policy_version') != POLICY_VERSION:
                    report = None
            except (OSError, ValueError):
                report = None
        return templates.TemplateResponse(request, 'automatic_matching.html', context(request,
            title='Автоматическое сопоставление', active='automatic_matching', policy=policy, report=report))

    if competitor_service is not None:
        app.state.monitoring_templates = templates
        app.state.monitoring_template_context = context
        from .competitor_ui import register_monitoring
        register_monitoring(app, templates, context, guard, competitor_service,
                            competitor_collection, check_csrf)

    if release_dispatch is not None:
        @app.get('/release-jobs', response_class=HTMLResponse)
        def release_jobs(request: Request):
            guard(request)
            return templates.TemplateResponse(request, 'release_jobs.html', context(request,
                title='Проверка сохранённых данных', active='release_jobs',
                sources=release_dispatch.sources(), jobs=release_dispatch.history()))

        @app.post('/release-jobs/enqueue')
        async def release_enqueue(request: Request):
            guard(request, MUTATING_ROLES); require_trusted_origin(request)
            values=await form_values(request); check_csrf(request, values)
            try: result=release_dispatch.enqueue(values.get('source_key',''),'OPERATOR_UI')
            except ValueError as e: raise HTTPException(400,str(e))
            if result['status']=='queued' and 'text/html' in request.headers.get('accept',''):
                return RedirectResponse('/release-jobs',status_code=303)
            return JSONResponse(result,status_code=202 if result['status']=='queued' else 409)

    @app.middleware("http")
    async def block_qa_source_writes(request: Request, call_next):
        from universal_supplier.partner_pilot import enabled
        if enabled() and request.method in {'POST','PUT','PATCH','DELETE'} and request.url.path not in {'/login','/logout'} and not (request.method=='POST' and request.url.path.startswith('/suppliers/') and (request.url.path.endswith('/start') or request.url.path.endswith('/stop-pilot'))):
            return PlainTextResponse('Разрешён только тестовый запуск Partner-ST',status_code=403)
        allowed_posts = {"/login", "/logout", "/commercial-qa/runs"}
        if competitor_collection is not None:
            allowed_posts.add('/competitor-monitoring/runs')
        if release_dispatch is not None:
            allowed_posts.add('/release-jobs/enqueue')
        if getattr(type(store), "read_only", False) and request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in allowed_posts and not request.url.path.startswith('/content-studio/'):
            if request.url.path.startswith("/suppliers/") and request.url.path.endswith("/start") and commercial_qa_service.enabled:
                return HTMLResponse(
                    "<h1>Обычный запуск недоступен</h1>"
                    "<p>Источник доступен только для чтения; обычные изменения поставщика заблокированы. "
                    "Для коммерческих источников используйте отдельный защищённый ручной сбор.</p>"
                    '<p><a href="/commercial-qa">Открыть ручной сбор</a></p>', status_code=403)
            return PlainTextResponse("Источник доступен только для чтения; обычные изменения заблокированы.", status_code=403)
        return await call_next(request)
    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        request.state.csp_nonce=secrets.token_urlsafe(24)
        from .access_policy import allowed, request_permission
        path = request.url.path
        public = path in {"/login", "/health", "/ready", "/favicon.ico"} or path.startswith("/static/")
        user = current_user(request) if auth_required and not public else None
        if auth_required and path.startswith('/content-studio') and not user:
            response=JSONResponse({'error':'Требуется вход Unified RC.'},401) if '/api/' in path else RedirectResponse('/login?next='+quote(path),303)
        elif user and user.must_change_password and path not in {"/account/password", "/logout"}:
            response = RedirectResponse("/account/password", 303)
        elif user and path not in {"/account/password", "/logout"} and not allowed(user, request_permission(path, request.method)):
            response = PlainTextResponse("Недостаточно прав для этого раздела", status_code=403)
        else:
            response=await call_next(request)
        studio_policy=response.headers.get('Content-Security-Policy') if path.startswith('/content-studio/') else None
        response.headers.update({
            'X-Frame-Options':'DENY','X-Content-Type-Options':'nosniff',
            # Chromium otherwise sends Origin:null on same-origin form POSTs.
            # Keep the origin guard strict and suppress referrers off-site.
            'Referrer-Policy':'same-origin','Cache-Control':'no-store',
            'Content-Security-Policy':"default-src 'self'; script-src 'self' 'nonce-"+request.state.csp_nonce+"'; style-src 'self' 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"})
        if studio_policy:
            response.headers['Content-Security-Policy']=studio_policy
            if '/api/previews/' in path or '/preview-window/' in path:
                response.headers['X-Frame-Options']='SAMEORIGIN'
        return response

    from .account_routes import register_accounts
    register_accounts(app, templates, context, guard, check_csrf, signer,
                      login_throttle, form_values, require_trusted_origin)
    from .content_studio import attach as attach_content_studio
    from .access_policy import allowed as content_allowed
    attach_content_studio(app,current_user,payload,content_allowed,content_studio_options)

    def page_error(request: Request, message: str, status_code=400, details=""):
        user = current_user(request)
        error = error_presentation(message, request.url.path)
        return templates.TemplateResponse(request, "error.html", context(request, message=error['title'], error=error,
            details=(details or message) if user and user.role == "ADMIN" else "", active="suppliers"), status_code=status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if exc.status_code == 401 and request.method == "GET":
            return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
        return page_error(request, str(exc.detail), exc.status_code)

    @app.exception_handler(PermissionError)
    async def runtime_guard_error(request,exc):
        if exc.errno is not None:
            # OS-level permission error (file/directory), not a runtime guard.
            LOGGER.error("filesystem permission error on %s: %s", request.url.path, exc)
            return PlainTextResponse('Нет доступа к файлу или папке на сервере; подробности в журнале.',status_code=500)
        text=str(exc)
        return PlainTextResponse(text if text.startswith(('CANARY_','AUTOMATION_','PANEL_')) else 'SQL safety guard blocked this action',status_code=503)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request): return RedirectResponse("/", 303)
        try: users_exist = admin_store.users_exist()
        except Exception: users_exist = False
        return templates.TemplateResponse(request, "login.html", context(request, users_exist=users_exist, error="", active=""))

    @app.post("/login")
    async def login(request: Request):
        require_trusted_origin(request); values = await form_values(request)
        throttle_key = (values.get("username", "").strip().lower(), request.client.host if request.client else "")
        def audit_login(action: str, user_id, reason: str) -> None:
            # Never the password; a failed audit write must not break login.
            try: admin_store.audit(user_id, action, "user", user_id if user_id is not None else throttle_key[0][:150], None,
                                   {"username": throttle_key[0][:150], "client": throttle_key[1], "reason": reason})
            except Exception: LOGGER.warning("could not audit %s", action, exc_info=True)
        try: locked = login_throttle.locked(throttle_key)
        except PermissionError as error:
            return PlainTextResponse(str(error), status_code=503)
        if locked:
            audit_login("LOGIN_FAILED", None, "throttled")
            return templates.TemplateResponse(request, "login.html", context(request, users_exist=admin_store.users_exist(), error="Слишком много неудачных попыток входа. Повторите позже", active=""), status_code=429)
        found = admin_store.user_by_username(values.get("username", ""))
        # Always run scrypt so unknown usernames cost the same as wrong passwords.
        password_ok = verify_password(values.get("password", ""), found[1] if found else dummy_password_hash())
        if not found or not found[0].is_active or not password_ok:
            login_throttle.failure(throttle_key)
            audit_login("LOGIN_FAILED", found[0].id if found else None,
                        "unknown_user" if not found else "inactive" if not found[0].is_active else "bad_password")
            return templates.TemplateResponse(request, "login.html", context(request, users_exist=admin_store.users_exist(), error="Неверное имя пользователя или пароль", active=""), status_code=401)
        login_throttle.reset(throttle_key)
        user, _ = found; admin_store.touch_login(user.id); token, _ = signer.issue(user)
        audit_login("LOGIN_SUCCEEDED", user.id, "password")
        response = RedirectResponse(safe_next(values.get("next")), 303)
        response.set_cookie(signer.cookie_name, token, httponly=True, samesite="lax", secure=session_cookie_secure(), max_age=signer.ttl_seconds, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        user = guard(request); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        if auth_required: admin_store.revoke_sessions(user.id, user.id, "logout")
        response = RedirectResponse("/login", 303); response.delete_cookie(signer.cookie_name, path="/"); return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        guard(request); suppliers = store.dashboard(); counts = admin_store.review_counts()
        counts["errors"] = sum(int(row.get("errors_30d") or 0) for row in suppliers)
        counts["queued"] = sum(row.get("has_queued_job",row.get("job_status") == "queued") for row in suppliers)
        counts["running"] = sum(row.get("has_running_job",row.get("job_status") == "running") for row in suppliers)
        return templates.TemplateResponse(request, "dashboard.html", context(request, suppliers=suppliers, review_counts=counts, active="dashboard"))

    @app.get("/products", response_class=HTMLResponse)
    def products(request: Request):
        guard(request); filters = filters_from_request(request)
        return templates.TemplateResponse(request, "products.html", context(request, result=store.products(filters), filters=filters, options=store.filter_options(), active="products", title="Товары"))

    @app.get("/products/{supplier_code}/{external_id}", response_class=HTMLResponse)
    def product_detail(request: Request, supplier_code: str, external_id: str):
        guard(request); item = store.product(supplier_code, external_id)
        if not item: raise HTTPException(404, "Товар не найден")
        item["history"] = store.product_history(supplier_code, external_id) if hasattr(store,"product_history") else []
        return templates.TemplateResponse(request, "product_detail.html", context(request, item=item, active="products"))

    @app.get("/products/export.csv")
    def products_csv(request: Request):
        user=guard(request); filters=filters_from_request(request); total=store.products(filters)["total"]
        rows=store.iter_export_rows(filters) if hasattr(store,"iter_export_rows") else store.export_rows(filters)
        admin_store.audit(user.id or None,"EXPORT_CREATED","products","csv",None,{"rows":total,"filters":dict(request.query_params),"streamed":True})
        return StreamingResponse(csv_chunks(rows),media_type="text/csv; charset=utf-8",headers={"Content-Disposition":f'attachment; filename="{safe_export_filename("products","csv",filters.supplier)}"'})

    @app.get("/products/export.xlsx")
    def products_xlsx(request: Request):
        user=guard(request); filters=filters_from_request(request); total=store.products(filters)["total"]
        rows=store.iter_export_rows(filters) if hasattr(store,"iter_export_rows") else store.export_rows(filters)
        if hasattr(store,"iter_export_detail"):
            details={kind:store.iter_export_detail(filters,kind) for kind in ("properties","images","documents","relations","matches","decisions")}
        else:
            materialized=list(rows); rows=materialized
            details=store.export_details([int(row["source_product_id"]) for row in materialized if is_id(row.get("source_product_id",""))])
        admin_store.audit(user.id or None,"EXPORT_CREATED","products","xlsx",None,{"rows":total,"filters":dict(request.query_params),"write_only":True,"chunk_size":500})
        return Response(product_workbook(rows,details),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{safe_export_filename("products","xlsx",filters.supplier)}"'})

    def filtered_review_cases(request: Request):
        q = request.query_params; optional_int(q.get("assignee"), "исполнитель")
        try: return admin_store.review_cases(status=q.get("status", ""), supplier=q.get("supplier", ""), decision=q.get("decision", ""), assignee=q.get("assignee", ""), brand=q.get("brand", ""), category=q.get("category", ""), opened_from=q.get("opened_from", ""), opened_to=q.get("opened_to", ""))
        except ValueError as error: raise HTTPException(400, str(error)) from error

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request):
        guard(request); rows = filtered_review_cases(request)
        return templates.TemplateResponse(request, "review_queue.html", context(request, rows=rows, counts=admin_store.review_counts(), suppliers=store.suppliers(), users=admin_store.list_users(), filters=request.query_params, active="review"))

    @app.get("/review/{case_id}", response_class=HTMLResponse)
    def review_case(request: Request, case_id: int):
        guard(request); item = admin_store.review_case(case_id)
        if not item: raise HTTPException(404, "Карточка проверки не найдена")
        return templates.TemplateResponse(request, "review_case.html", context(request, item=item, search_results=[], active="review"))

    @app.get("/review/{case_id}/search", response_class=HTMLResponse)
    def review_search(request: Request, case_id: int):
        guard(request); item = admin_store.review_case(case_id)
        if not item: raise HTTPException(404, "Карточка проверки не найдена")
        return templates.TemplateResponse(request, "review_case.html", context(request, item=item, search_results=admin_store.search_candidates(case_id, request.query_params.get("search", "")), active="review"))

    @app.post("/review/{case_id}/decision")
    async def review_decision(request: Request, case_id: int):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        if values.get("selection_mode") == "other" and not values.get("comment", "").strip():
            return page_error(request, "При выборе другого товара обязателен комментарий")
        try: admin_store.decide(case_id, user.id, values.get("decision_class", ""), sterbrust_product_id=values.get("sterbrust_product_id") or None, parent_sterbrust_product_id=values.get("parent_sterbrust_product_id") or None, comment=values.get("comment", ""))
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        next_rows = [row for row in admin_store.review_cases(limit=200)
                     if int(row["id"]) != case_id and row.get("lifecycle_status") in {"OPEN", "ASSIGNED", "REOPENED_SOURCE_CHANGED"}]
        target = f"/review/{next_rows[0]['id']}?saved=1" if next_rows else "/review?saved=1"
        return RedirectResponse(target, 303)

    @app.post("/review/{case_id}/reject")
    async def reject_candidate(request: Request, case_id: int):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        try: admin_store.reject_candidate(case_id, user.id, values.get("sterbrust_product_id", ""), values.get("comment", ""))
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse(f"/review/{case_id}?rejected=1", 303)

    @app.post("/review/bulk/assign")
    async def bulk_assign(request: Request):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        case_ids = [int(x) for x in values.get("case_ids", "").split(",") if is_id(x)]
        try: admin_store.assign(case_ids, optional_int(values.get("assigned_to_user_id"), "исполнитель"), user.id)
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/review?bulk_saved=1", 303)

    @app.post("/review/bulk")
    async def safe_bulk(request: Request):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        case_ids = [int(value) for key, value in values.items() if key.startswith("case_") and is_id(value)]
        if not case_ids: return page_error(request, "Выберите хотя бы одну карточку")
        action = values.get("bulk_action", "")
        skipped = 0
        try:
            if action == "assign":
                admin_store.assign(case_ids, optional_int(values.get("assigned_to_user_id"), "исполнитель"), user.id)
            else:
                skipped = int((admin_store.bulk_update(case_ids, action, user.id, values.get("comment", "")) or {}).get("skipped_resolved", 0))
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse(f"/review?bulk_saved=1&skipped_resolved={skipped}" if skipped else "/review?bulk_saved=1", 303)

    @app.get("/suppliers", response_class=HTMLResponse)
    def suppliers(request: Request):
        guard(request)
        rows=store.suppliers();automation={}
        if callable(getattr(type(store),'automation_overview',None)):automation=store.automation_overview(rows)
        return templates.TemplateResponse(request, "suppliers.html", context(request, suppliers=rows,automation=automation,active="suppliers"))

    def parallel_collection_available():
        from .manual_collection import enabled as manual_enabled
        from .source_runtime import runtime_enabled
        from universal_supplier.partner_pilot import enabled as pilot_enabled

        if getattr(type(store), 'read_only', False) or not manual_enabled() or not runtime_enabled() or pilot_enabled():
            raise HTTPException(403, 'Параллельный сбор здесь недоступен')

    def parallel_collection_guard(request):
        user = guard(request, {'ADMIN'})
        parallel_collection_available()
        return user

    async def parallel_collection_post_guard(request):
        # Role, origin and CSRF come before any feature or business check,
        # as on every other mutating route (security review group 5).
        user = guard(request, {'ADMIN'})
        require_trusted_origin(request)
        raw = await form_values(request)
        check_csrf(request, raw)
        parallel_collection_available()
        return user, raw

    def parallel_collection_sources():
        from .manual_collection import manual_launch_settings
        from .source_jobs import RUNTIME_SOURCES
        from .supplier_automation import settings_for
        from universal_supplier.adapters.ivstankomaster import CODE as discovery_source

        sources = []
        for row in store.suppliers():
            if row['code'] not in RUNTIME_SOURCES:
                continue
            available = min(100000, row.get('products') or 0)
            busy = row.get('job_status') in ('queued', 'running') or row.get('has_queued_job') or row.get('has_running_job')
            sources.append(dict(id=row['id'], name=row.get('name') or row['code'],
                                mode='DISCOVERY' if row['code']==discovery_source or not available else 'REFRESH',
                                busy=bool(busy),limit=available or 20,
                                settings=manual_launch_settings(row['code'], settings_for(row))))
        return sources

    @app.get('/suppliers/manual-parallel', response_class=HTMLResponse)
    def parallel_collection_page(request: Request):
        parallel_collection_guard(request)
        sources = parallel_collection_sources()
        count = len(sources) if any(row['limit'] > 0 and not row['busy'] for row in sources) else 0
        resume_candidates = queue.manual_resume_candidates()
        return templates.TemplateResponse(request, 'manual_parallel.html',
            context(request, active='suppliers', sources=sources, parallel_options=range(1, count + 1),
                    resume_candidates=resume_candidates, resume_options=range(1, len(sources) + 1)))

    def launch_parallel_jobs(planned, user_id, key):
        from .manual_collection import launch
        from .presentation import MANUAL_RESUME_BLOCKERS

        results = []
        for row,count,approval in planned:
            sid = row['id']
            try:
                result = queue.enqueue(sid, trigger_type='manual', crawl_type='full', requested_by_user_id=user_id,
                    source_mode=approval.mode, source_limit=count, resume_job_id=row.get('parent_job_id'),
                    manual_request=approval)
            except (ValueError, PermissionError, KeyError) as error:
                if approval.mode == 'RESUME':
                    message = MANUAL_RESUME_BLOCKERS.get(str(error),
                        'Очередь не прошла проверку. Откройте предыдущий запуск для подробностей.')
                else:
                    message = 'Запуск не подготовлен. Проверьте настройки поставщика.'
                results.append(dict(row, message=message))
                continue
            except Exception:
                results.append(dict(row, message='Не удалось подтвердить постановку в очередь. Проверьте задания поставщика.'))
                continue
            if not result.enqueued:
                results.append(dict(row, job_id=result.deduplicated_job_id,
                                    message='У поставщика уже есть активное задание.'))
                continue
            try:
                if manual_launcher is not None:
                    manual_launcher(result.job_id)
                else:
                    launch(result.job_id, key=key)
            except Exception:
                message = 'Задание сохранено. Его можно запустить со страницы поставщика.'
            else:
                message = 'В очереди.'
            results.append(dict(row, job_id=result.job_id, message=message))
        return results

    @app.post('/suppliers/manual-parallel', response_class=HTMLResponse)
    async def parallel_collection_start(request: Request):
        from .manual_collection import ManualRequest, parallel_limit

        user, raw = await parallel_collection_post_guard(request)
        sources = {f"include_{row['id']}": row for row in await run_in_threadpool(parallel_collection_sources)}
        selected = {key for key,value in raw.items() if key.startswith('include_') and value == 'yes'}
        if not selected or not selected <= sources.keys():
            return page_error(request, 'Выберите доступных поставщиков')
        key = signer.secret.decode('utf-8')
        planned = []
        try:
            maximum = parallel_limit(optional_int(raw.get('max_parallel_suppliers'), 'Одновременных сборов'))
            if maximum > len(sources):
                raise ValueError('Число одновременных сборов превышает число доступных поставщиков')
            hours = optional_int(raw.get('duration_hours'), 'Лимит времени')
            group_mode=raw.get('collection_mode','')
            if group_mode not in ('','DISCOVERY'):
                raise ValueError('Выберите режим сбора')
            for field,row in sources.items():
                if field not in selected:
                    continue
                sid = row['id']
                count = optional_int(raw.get(f'limit_{sid}'), 'Количество карточек')
                cfg = row['settings']
                revision = optional_int(raw.get(f'revision_{sid}'), 'Версия настроек')
                if revision != cfg['revision'] or not row['limit']:
                    raise ValueError('Список или настройки изменились. Откройте форму заново')
                mode=group_mode or row['mode']
                approval = ManualRequest(user, key, str(cfg['delay_min_seconds']), str(cfg['delay_max_seconds']),
                    revision, mode, hours, max_parallel_suppliers=maximum)
                approval.validate(sid, count, 'manual', mode)
                planned.append((row, count, approval))
        except (ValueError, PermissionError) as error:
            return page_error(request, str(error))

        results = await run_in_threadpool(launch_parallel_jobs, planned, user.id, key)
        return templates.TemplateResponse(request, 'manual_parallel.html',
            context(request, active='suppliers', results=results))

    @app.post('/suppliers/manual-parallel/resume', response_class=HTMLResponse)
    async def parallel_collection_resume(request: Request):
        from .manual_collection import ManualRequest, parallel_limit

        user, raw = await parallel_collection_post_guard(request)
        selected = {key:value for key,value in raw.items() if key.startswith('resume_')}
        if not selected:
            return page_error(request, 'Выберите сборы для продолжения')
        candidates = {f"resume_{row['id']}": row
                      for row in await run_in_threadpool(queue.manual_resume_candidates)}
        if not selected.keys() <= candidates.keys():
            return page_error(request, 'Список незавершённых сборов изменился. Обновите страницу.')
        key = signer.secret.decode('utf-8')
        planned = []
        try:
            maximum = parallel_limit(optional_int(raw.get('max_parallel_suppliers'), 'Одновременных сборов'))
            for field,value in selected.items():
                row = candidates[field]
                if optional_int(value, 'Исходное задание') != row['parent_job_id']:
                    raise ValueError('Исходное задание изменилось. Обновите страницу.')
                hours=optional_int(raw.get(f"hours_{row['id']}"), 'Лимит времени')
                if hours is None:hours=row['time']['hours']
                approval = ManualRequest(user, key, '', '', -1, 'RESUME', duration_hours=hours,
                                         max_parallel_suppliers=maximum)
                approval.validate(row['id'], None, 'manual', 'RESUME')
                planned.append((row, None, approval))
        except (ValueError, PermissionError) as error:
            return page_error(request, str(error))

        results = await run_in_threadpool(launch_parallel_jobs, planned, user.id, key)
        return templates.TemplateResponse(request, 'manual_parallel.html',
            context(request, active='suppliers', results=results))

    @app.get('/scheduler/execution',response_class=HTMLResponse)
    def automation_executions(request: Request):
        guard(request)
        from .automation_status import overview
        return templates.TemplateResponse(request,'automation_execution.html',context(request,sessions=overview(),active='scheduler'))

    @app.post('/scheduler/execution/{identifier}/stop')
    async def automation_execution_stop(request: Request,identifier:str):
        user=guard(request,MUTATING_ROLES);require_trusted_origin(request)
        values=await form_values(request);check_csrf(request,values)
        from .automation_status import stop
        try:stop(identifier)
        except ValueError as error:return page_error(request,str(error))
        admin_store.audit(user.id,'AUTOMATION_STOP_REQUESTED','execution',identifier,None,{'stop_requested':True})
        return RedirectResponse('/scheduler/execution',303)

    @app.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
    def supplier_detail(request: Request, supplier_id: int):
        guard(request); item = next((x for x in store.suppliers() if int(x["id"]) == supplier_id), None)
        if not item: raise HTTPException(404, "Поставщик не найден")
        policies = effective_crawl_settings(item.get("crawl_settings"))
        from .supplier_automation import public_settings
        return templates.TemplateResponse(request, "supplier_detail.html", context(request, item=item,
            parsing=public_settings(item), policies=policies, active="suppliers"))

    @app.post('/suppliers/{supplier_id}/manual-start')
    async def manual_start(request: Request,supplier_id: int):
        user=guard(request,MUTATING_ROLES);require_trusted_origin(request)
        raw=await form_values(request);check_csrf(request,raw)
        if getattr(type(store),'read_only',False):raise HTTPException(403,'Источник доступен только для чтения')
        from .manual_collection import ManualRequest, launch
        result=None
        try:
            count=optional_int(raw.get('max_products'),'Количество карточек')
            mode=raw.get('collection_mode','REFRESH')
            parent_id=optional_int(raw.get('resume_job_id'),'Номер задания')
            hours=optional_int(raw.get('duration_hours'),'Лимит времени')
            approval=ManualRequest(user,signer.secret.decode('utf-8'),raw.get('delay_min_seconds',''),raw.get('delay_max_seconds',''),
                                   int(raw.get('revision','-1')),mode,hours,retry_network=raw.get('retry_network')=='yes')
            approval.validate(supplier_id,count,'manual',mode)
            result=queue.enqueue(supplier_id,trigger_type='manual',crawl_type='full',requested_by_user_id=user.id,
                source_mode=mode,source_limit=count,resume_job_id=parent_id,manual_request=approval)
            if result.enqueued:
                if manual_launcher is not None:manual_launcher(result.job_id)
                else:launch(result.job_id,key=signer.secret.decode('utf-8'))
        except (ValueError,PermissionError,KeyError,TypeError) as error:
            return page_error(request,str(error))
        except Exception:
            message = ('Задание сохранено. Откройте поставщика и нажмите «Запустить ожидающее задание».'
                       if result and result.enqueued else 'Не удалось подготовить сбор. Повторите попытку позже.')
            return page_error(request, message, 503)
        target=result.job_id if result.enqueued else (result.deduplicated_job_id or result.job_id)
        return RedirectResponse(f'/runs?job_id={target}&enqueue_status={result.status}',303)

    @app.post('/suppliers/{supplier_id}/recover-queued-job')
    async def recover_queued_job(request: Request, supplier_id: int):
        user = guard(request, MUTATING_ROLES)
        require_trusted_origin(request)
        values = await form_values(request)
        check_csrf(request, values)
        if getattr(type(store), 'read_only', False):
            raise HTTPException(403, 'Источник доступен только для чтения')
        from .manual_collection import launch
        from .source_runtime import runtime_enabled
        if not runtime_enabled():
            return page_error(request, 'SOURCE_RUNTIME_NOT_ENABLED', 403)
        try:
            job_id = optional_int(values.get('job_id'), 'Номер задания')
            action = values.get('action')
            if not job_id or job_id < 1 or action not in ('launch', 'cancel'):
                raise ValueError('Выберите ожидающее задание и действие')
            key = signer.secret.decode('utf-8')
            queue.recover_queued_manual_job(supplier_id, job_id, user, key, cancel=action=='cancel')
            if action == 'cancel':
                return RedirectResponse(f'/suppliers/{supplier_id}', 303)
            if manual_launcher is not None:
                manual_launcher(job_id)
            else:
                launch(job_id, key=key)
        except (PermissionError, ValueError) as error:
            return page_error(request, str(error))
        except Exception:
            return page_error(request, 'Не удалось восстановить задание. Обновите страницу поставщика.', 503)
        return RedirectResponse(f'/runs?job_id={job_id}', 303)

    @app.post("/suppliers/{supplier_id}/enabled")
    async def supplier_enabled(request: Request, supplier_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        if getattr(type(store), 'read_only', False):raise HTTPException(403, 'Источник доступен только для чтения')
        before = next((x for x in store.suppliers() if int(x["id"]) == supplier_id), None); enabled = values.get("enabled") == "true"
        store.set_supplier_enabled(supplier_id, enabled); admin_store.audit(user.id, "SUPPLIER_ENABLED_CHANGED", "supplier", supplier_id, {"enabled": before.get("enabled") if before else None}, {"enabled": enabled}, values.get("comment") or None)
        return RedirectResponse(f"/suppliers/{supplier_id}" if values.get('return_to')=='source' else '/suppliers', 303)

    @app.get('/suppliers/{supplier_id}/parsing',response_class=HTMLResponse)
    def parsing_settings_page(request: Request,supplier_id: int):
        guard(request)
        if not hasattr(store,'parsing_settings'):raise HTTPException(503,'Настройки недоступны: подключён read-only snapshot')
        try:item=store.parsing_settings(supplier_id)
        except KeyError:raise HTTPException(404,'Поставщик не найден')
        return templates.TemplateResponse(request,'supplier_parsing.html',context(request,
            parsing=item,item=item['supplier'],saved=request.query_params.get('saved'),active='suppliers'))

    @app.post('/suppliers/{supplier_id}/parsing')
    async def parsing_settings_save(request: Request,supplier_id: int):
        user=guard(request,{'ADMIN'});require_trusted_origin(request)
        raw=await form_values(request);check_csrf(request,raw)
        if getattr(store,'read_only',False) or not hasattr(store,'save_parsing_settings'):
            raise HTTPException(403,'Подключён read-only источник; настройки не сохранены')
        from .supplier_automation import BOOLEAN_FIELDS,INTEGER_FIELDS,NUMBER_FIELDS
        try:
            values={k:raw[k] for k in set(INTEGER_FIELDS)|set(NUMBER_FIELDS)|{'start_time','timezone'} if k in raw}
            values.update({k:raw.get(k,'false') for k in BOOLEAN_FIELDS})
            store.save_parsing_settings(supplier_id,values,int(raw['revision']),user.id or None)
        except (KeyError,ValueError,TypeError) as error:
            return page_error(request,'Настройки не сохранены: '+str(error))
        target=f'/suppliers/{supplier_id}' if raw.get('return_to')=='source' else f'/suppliers/{supplier_id}/parsing'
        return RedirectResponse(target+'?saved=1',303)

    @app.post("/suppliers/{supplier_id}/data-quality-policy")
    async def supplier_data_quality_policy(request: Request,supplier_id:int):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        try:
            policy={key:float(values[key]) for key in ("count_ratio_min","fill_rate_drop","fill_rate_min")}
            store.save_data_quality_policy(supplier_id,policy)
        except (ValueError,KeyError,AttributeError) as error: return page_error(request,f"Настройки качества не сохранены: {error}")
        admin_store.audit(user.id,"DATA_QUALITY_POLICY_CHANGED","supplier",supplier_id,None,policy)
        return RedirectResponse(f"/suppliers/{supplier_id}?saved=1",303)

    @app.post("/suppliers/{supplier_id}/missing-policy")
    async def supplier_missing_policy(request: Request,supplier_id:int):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        try:
            policy={"warning_at":int(values["warning_at"]),"candidate_at":int(values["candidate_at"])}
            store.save_missing_policy(supplier_id,policy)
        except (ValueError,KeyError,AttributeError,TypeError) as error:
            return page_error(request,f"Политика отсутствующих товаров не сохранена: {error}")
        admin_store.audit(user.id,"MISSING_POLICY_CHANGED","supplier",supplier_id,None,policy)
        return RedirectResponse(f"/suppliers/{supplier_id}?saved=1",303)

    @app.get('/suppliers/{supplier_id}/source-canary',response_class=HTMLResponse)
    def preview_source_canary(request:Request,supplier_id:int):
        guard(request,MUTATING_ROLES)
        from .source_runtime import runtime_enabled
        if not runtime_enabled():raise HTTPException(403,'Режим collectors не включён')
        if getattr(type(store),'read_only',False):raise HTTPException(403,'Источник доступен только для чтения')
        limit=optional_int(request.query_params.get('max_products'),'количество карточек')
        try:
            from .source_canary import limit_value
            limit_value(limit)
            if not hasattr(queue,'preview_source_canary'):raise ValueError('Очередь не поддерживает безопасный preview canary')
            preview=queue.preview_source_canary(supplier_id,limit,request.query_params.get('collection_mode','REFRESH'))
        except (ValueError,PermissionError) as error:raise HTTPException(400,str(error))
        return templates.TemplateResponse(request,'source_canary.html',context(request,preview=preview,supplier_id=supplier_id,active='suppliers'))

    @app.post("/suppliers/{supplier_id}/start")
    async def start_now(request: Request, supplier_id: int):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        if values.get("crawl_type", "full") not in SUPPORTED_WEB_CRAWL_TYPES: raise HTTPException(400, "Можно поставить в очередь только полный сбор")
        from universal_supplier.partner_pilot import enabled,limit_value
        mode=values.get('launch_mode','full')
        if mode not in ('full','test','source','source_canary'):raise HTTPException(400,'Неизвестный режим запуска')
        if enabled() and mode!='test':raise HTTPException(403,'Разрешён только ограниченный тест Partner-ST')
        kwargs={}
        if mode=='source_canary':
            from .source_runtime import runtime_enabled
            from .source_canary import limit_value
            if not runtime_enabled():raise HTTPException(403,'Режим collectors не включён')
            try:
                limit=limit_value(optional_int(values.get('max_products'),'количество карточек'))
                if values.get('collection_mode') not in ('REFRESH','DISCOVERY') or values.get('resume_job_id'):raise ValueError('Нужен REFRESH или DISCOVERY; RESUME запускается из checkpoint')
                source=next((s for s in store.suppliers() if s['id']==supplier_id),None)
                if not source or not source.get('enabled'):raise ValueError('Поставщик выключен; запуск canary заблокирован. Нужно отдельное подтверждение временного включения.')
                kwargs.update(source_mode=values['collection_mode'],source_limit=limit,source_preview_sha256=values.get('source_preview_sha256',''))
            except (ValueError,PermissionError) as error:raise HTTPException(400,str(error))
        if mode=='source':
            from .source_runtime import runtime_enabled
            if not runtime_enabled():raise HTTPException(403,'Режим collectors не включён')
            kwargs['source_mode']=values.get('collection_mode','')
            if kwargs['source_mode'] not in ('REFRESH','DISCOVERY','RESUME'):raise HTTPException(400,'Неизвестный режим collector')
            if kwargs['source_mode']=='RESUME':
                raw=values.get('resume_job_id','')
                if not raw.isascii() or not raw.isdecimal() or int(raw)<1:raise HTTPException(400,'Нужен исходный job')
                kwargs['resume_job_id']=int(raw)
            else:raise HTTPException(400,'Сначала подтвердите общий лимит и snapshot на странице подготовки сбора')
        if mode=='test':
            try:
                raw=values.get('max_products','')
                if not raw.isascii() or not raw.isdecimal():raise ValueError('Лимит должен быть целым числом от 1 до 50')
                kwargs['pilot_limit']=limit_value(int(raw))
                from universal_supplier.partner_pilot import rate_profile
                kwargs['pilot_profile']=values.get('pilot_profile','conservative')
                rate_profile(kwargs['pilot_profile'])
            except ValueError as error:raise HTTPException(400,str(error))
        try:result = queue.enqueue(supplier_id, trigger_type="manual", crawl_type="full", requested_by_user_id=user.id or None,**kwargs)
        except (ValueError,PermissionError) as error:raise HTTPException(400,str(error))
        admin_store.audit(user.id or None, "MANUAL_CRAWL_ENQUEUED", "crawl_job", result.job_id, None, {"supplier_id": supplier_id, "crawl_type": "full", "status": result.status, "deduplicated_job_id": result.deduplicated_job_id}, values.get("comment") or None, str(uuid.uuid4()))
        return RedirectResponse(f"/runs?enqueue_status={result.status}&job_id={result.job_id}", 303)

    @app.post('/suppliers/{supplier_id}/stop-source')
    async def stop_source(request:Request,supplier_id:int):
        guard(request,MUTATING_ROLES);require_trusted_origin(request)
        values=await form_values(request);check_csrf(request,values)
        try:
            raw=values.get('job_id','')
            if not raw.isascii() or not raw.isdecimal() or int(raw)<1:raise ValueError('Нужен активный job')
            queue.stop_source(supplier_id,int(raw))
        except (ValueError,PermissionError,AttributeError) as error:raise HTTPException(400,str(error))
        return RedirectResponse(f'/suppliers/{supplier_id}',303)

    @app.post('/suppliers/{supplier_id}/close-expired-job')
    async def close_expired_job(request: Request, supplier_id: int):
        user=guard(request,MUTATING_ROLES);require_trusted_origin(request)
        values=await form_values(request);check_csrf(request,values)
        if getattr(type(store),'read_only',False):raise HTTPException(403,'Источник доступен только для чтения')
        try:
            job_id=optional_int(values.get('job_id'),'Номер задания')
            if not job_id:raise ValueError('Укажите номер прерванного задания')
            queue.close_expired_manual_job(supplier_id,job_id,actor_id=user.id)
        except (ValueError,PermissionError,AttributeError) as error:raise HTTPException(400,str(error))
        return RedirectResponse(f'/suppliers/{supplier_id}',303)

    @app.post('/suppliers/{supplier_id}/stop-pilot')
    async def stop_pilot(request:Request,supplier_id:int):
        from universal_supplier.partner_pilot import enabled
        guard(request,MUTATING_ROLES);require_trusted_origin(request)
        values=await form_values(request);check_csrf(request,values)
        if not enabled():raise HTTPException(403,'Тестовый режим не включён')
        item=next((x for x in store.suppliers() if int(x['id'])==supplier_id),None)
        if not item or item.get('code')!='partner_st':raise HTTPException(403,'Только Partner-ST')
        stop_file=os.environ.get('PARTNER_PILOT_STOP_FILE','').strip()
        if not stop_file:raise HTTPException(503,'Не задан PARTNER_PILOT_STOP_FILE: остановка пилота не настроена')
        Path(stop_file).touch(exist_ok=True)
        return RedirectResponse('/runs',303)

    @app.get("/commercial-qa", response_class=HTMLResponse)
    def commercial_qa_page(request: Request):
        guard(request)
        return templates.TemplateResponse(request, "commercial_qa.html", context(
            request, data=commercial_qa_service.overview(), active="commercial_qa"))

    @app.post("/commercial-qa/runs")
    async def commercial_qa_start(request: Request):
        guard(request, MUTATING_ROLES); require_trusted_origin(request)
        values = await form_values(request); check_csrf(request, values)
        try:
            scope_args = {"full_scope": True} if values.get("full_scope") == "full" else {}
            run = commercial_qa_service.start(values.get("source", ""), int(values.get("limit", "0")),
                                               values.get("dry_run") == "true",
                                               full_manifest=values.get("full_manifest") == "true",
                                               write_confirmed=values.get("write_confirmed") == "true",
                                               **scope_args)
        except (CommercialQaUnavailable, ValueError) as error:
            return page_error(request, str(error))
        return RedirectResponse(f"/commercial-qa/runs/{run.id}", 303)

    @app.get("/commercial-qa/runs/{run_id}", response_class=HTMLResponse)
    def commercial_qa_run(request: Request, run_id: str):
        guard(request)
        run = commercial_qa_service.run(run_id)
        if run is None:
            raise HTTPException(404, "Тестовый запуск не найден")
        return templates.TemplateResponse(request, "commercial_qa_run.html", context(
            request, run=run, active="commercial_qa"))

    @app.get("/api/commercial-qa/runs/{run_id}")
    def commercial_qa_status(request: Request, run_id: str):
        guard(request)
        run = commercial_qa_service.run(run_id)
        if run is None:
            raise HTTPException(404, "Тестовый запуск не найден")
        allowed = {"id", "source", "limit", "mode", "status", "started_at", "finished_at", "error",
                   "processed", "total", "site_summary", "counts_before", "counts_after",
                   "xml_sha256", "xml_products", "proposals_sha256"}
        return JSONResponse({key: value for key, value in run.items() if key in allowed})

    @app.get("/commercial-qa/runs/{run_id}/report")
    def commercial_qa_report(request: Request, run_id: str):
        guard(request)
        try:
            body = commercial_qa_service.report_bytes(run_id)
        except (CommercialQaUnavailable, KeyError):
            raise HTTPException(404, "Диагностический отчёт пока недоступен")
        return Response(body, media_type="application/json; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="commercial-qa-{run_id}.json"'})

    @app.get("/commercial-qa/xml", response_class=HTMLResponse)
    def commercial_qa_xml(request: Request):
        guard(request)
        try:
            body, info = commercial_qa_service.diagnostic_xml()
        except CommercialQaUnavailable as error:
            return page_error(request, str(error))
        return templates.TemplateResponse(request, "commercial_qa_xml.html", context(
            request, info=info, preview=body.decode("utf-8")[:16000], active="commercial_qa"))

    @app.get("/commercial-qa/xml/download")
    def commercial_qa_xml_download(request: Request):
        guard(request)
        try:
            body, _ = commercial_qa_service.diagnostic_xml()
        except CommercialQaUnavailable as error:
            return page_error(request, str(error))
        return Response(body, media_type="application/xml; charset=utf-8",
                        headers={"Content-Disposition": 'attachment; filename="stage5d-commercial-qa.xml"'})

    @app.get("/commercial-qa/proposals", response_class=HTMLResponse)
    def commercial_qa_proposals(request: Request):
        guard(request)
        try:
            proposals = commercial_qa_service.proposals()
            unavailable = ""
        except CommercialQaUnavailable as error:
            proposals = None
            unavailable = str(error)
        return templates.TemplateResponse(request, "commercial_proposals.html", context(
            request, proposals=proposals, unavailable=unavailable, active="commercial_qa"))

    @app.get("/commercial-qa/proposals/files/{filename}")
    def commercial_qa_proposal_file(request: Request, filename: str):
        guard(request)
        try:
            body = commercial_qa_service.proposal_artifact(filename)
        except CommercialQaUnavailable:
            raise HTTPException(404, "Диагностический файл недоступен")
        kind = ("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" if filename.endswith(".xlsx")
                else "text/csv; charset=utf-8" if filename.endswith(".csv") else "application/xml; charset=utf-8")
        return Response(body, media_type=kind,
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/runs", response_class=HTMLResponse)
    def runs(request: Request):
        guard(request)
        job_status=request.query_params.get('job_status')
        rows=store.runs(job_status=job_status) if job_status in ('queued','running') else store.runs()
        job_id=request.query_params.get("job_id")
        return templates.TemplateResponse(request, "runs.html", context(request, runs=rows, active="runs", job_id=job_id if is_id(job_id) else None))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: int):
        from .manual_collection import ManualRequest, TERMINAL, enabled as manual_enabled, resume_time_suggestion
        from .presentation import MANUAL_RESUME_BLOCKERS, collection_issue_message
        from .source_runtime import runtime_enabled
        from .transport_diagnostics import safe_message
        user = guard(request); item = store.run(run_id)
        if not item: raise HTTPException(404, "Запуск не найден")
        parent_id = item.get('manual_job_id')
        if not parent_id and request.query_params.get('check_source_resume')=='1' and (item.get('metadata') or {}).get('source_runtime'):
            from copy import deepcopy
            item=deepcopy(item)
            try:
                queue.source_resume_status(item['metadata']['job_id'])
                item['metadata']['source_runtime']['resume_available']=True
                item['metadata']['source_runtime']['resume_check']='SQL и checkpoint сверены; повторная сверка перед enqueue'
            except (ValueError,PermissionError,KeyError,AttributeError) as error:
                item['metadata']['source_runtime']['resume_available']=False
                item['metadata']['source_runtime']['resume_check']='Продолжение заблокировано: '+str(error)
        metadata = item.get('metadata') or {}
        runtime = metadata.get('source_runtime') or {}
        manual_resume_available = None
        manual_resume_blocker = None
        manual_resume_time = None
        if (parent_id and item.get('latest_supplier_job_id', parent_id) == parent_id
                and item['status'] in TERMINAL and user.role in MUTATING_ROLES
                and not getattr(type(store), 'read_only', False) and manual_enabled() and runtime_enabled()):
            remaining = runtime.get('remaining') or runtime.get('discovery_remaining') or runtime.get('resume_available')
            retry_network = (item['status'] == 'partial' and runtime.get('remaining') == 0
                             and not runtime.get('termination_reason') and bool(runtime.get('deferred')))
            if remaining or retry_network:
                approval = ManualRequest(user, signer.secret.decode('utf-8'), '', '', -1, 'RESUME',
                                         retry_network=not remaining)
                try:
                    queue.manual_resume_status(item['supplier_id'], parent_id, approval)
                except Exception as error:
                    code = str(error) if isinstance(error, (ValueError, PermissionError)) else ''
                    manual_resume_blocker = MANUAL_RESUME_BLOCKERS.get(
                        code, 'Не удалось подтвердить сохранённую очередь. Обновите страницу или обратитесь к администратору.')
                else:
                    manual_resume_available = 'retry_network' if approval.retry_network else 'continue'
                    if manual_resume_available=='continue' and user.role=='ADMIN':
                        started,finished=item.get('started_at'),item.get('finished_at')
                        elapsed=(finished-started).total_seconds() if isinstance(started,datetime) and isinstance(finished,datetime) else None
                        manual_resume_time=resume_time_suggestion(runtime,elapsed)
        receipts = runtime.get('quarantined') or []
        progress = run_collection_progress(metadata)
        issues = []
        for receipt in receipts[:50]:
            proof = (receipt.get('isolation') or {}).get('rejection_proof') or {}
            linked_id = proof.get('sterbrust_product_id')
            issues.append({'message': collection_issue_message(receipt),
                           'url': safe_http_url(safe_message(receipt.get('url') or '')),
                           'linked_id': linked_id if is_id(linked_id) else None})
        return templates.TemplateResponse(request, "run_detail.html",
            context(request, item=item, active="runs", collection_progress=progress,
                    collection_issues=issues, collection_issues_total=len(receipts),
                    manual_resume_available=manual_resume_available, manual_resume_blocker=manual_resume_blocker,
                    manual_resume_time=manual_resume_time))

    def progress_response(item):
        if item is None:raise HTTPException(404, "Запуск не найден")
        allowed={"run_id","supplier_name","run_status","job_status","phase","phase_label_ru","status",
                 "total","processed","succeeded","errors","percent","saved_percent","deferred","durable_progress","rate_per_second",
                 "matching_conflicts","other_card_issues",
                 "eta_seconds","eta_human_ru","elapsed_seconds","fetch_elapsed_seconds","updated_at"}
        return JSONResponse({key:value for key,value in item.items() if key in allowed},headers={'Cache-Control':'no-store'})

    @app.get("/api/jobs/{job_id}/progress")
    def job_progress(request: Request,job_id: int):
        guard(request)
        return progress_response(store.job_progress(job_id) if hasattr(store,'job_progress') else None)

    @app.get("/api/runs/{run_id}/progress")
    def run_progress(request: Request, run_id: int):
        guard(request)
        item = store.run_progress(run_id) if hasattr(store, "run_progress") else None
        return progress_response(item)

    @app.get("/scheduler", response_class=HTMLResponse)
    def scheduler_page(request: Request):
        guard(request)
        advanced = request.query_params.get("advanced") == "1"
        suppliers = store.suppliers()
        for supplier in suppliers: supplier["schedule_controls"] = schedule_to_controls(supplier.get("cron_expression"))
        return templates.TemplateResponse(request, "scheduler.html", context(request, suppliers=suppliers, active="scheduler", advanced=advanced, saved=request.query_params.get("saved")))

    @app.post('/suppliers/{supplier_id}/parsing/approve-host')
    async def parsing_host_approval(request: Request,supplier_id: int):
        user=guard(request,{'ADMIN'});require_trusted_origin(request)
        raw=await form_values(request);check_csrf(request,raw)
        if getattr(store,'read_only',False) or not hasattr(store,'approve_parsing_host'):raise HTTPException(403,'Read-only источник')
        try:store.approve_parsing_host(supplier_id,int(raw['revision']),raw['policy_sha256'],user.id or None)
        except (KeyError,ValueError) as error:return page_error(request,'Профиль не утверждён: '+str(error))
        target=f'/suppliers/{supplier_id}' if raw.get('return_to')=='source' else f'/suppliers/{supplier_id}/parsing'
        return RedirectResponse(target+'?host_saved=1',303)

    @app.post("/scheduler/{supplier_id}")
    async def scheduler_save(request: Request, supplier_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); raw = await form_values(request); check_csrf(request, raw)
        try:
            timezone_name = raw.get("timezone") or "UTC"; enabled = raw.get("enabled") == "true"
            weekdays = [value for key,value in raw.items() if key.startswith("weekday_")]
            expression = controls_to_schedule(raw.get("schedule_mode","custom"), raw.get("schedule_time","02:00"), weekdays, raw.get("cron_expression", ""))
            if raw.get("crawl_type", "full") not in SUPPORTED_WEB_CRAWL_TYPES: raise ValueError("доступен только полный сбор")
            if enabled: validate_schedule(expression, timezone_name)
            values = {"enabled":enabled,"cron_expression":expression,"timezone":timezone_name,"crawl_type":"full",
                "max_concurrency":int(raw.get("max_concurrency","1")),"min_request_interval_seconds":float(raw.get("min_request_interval_seconds","0.2")),
                "jitter_min_seconds":float(raw.get("jitter_min_seconds","0")),"jitter_max_seconds":float(raw.get("jitter_max_seconds","0.1")),
                "request_timeout_seconds":float(raw.get("request_timeout_seconds","40")),"max_attempts":int(raw.get("max_attempts","3")),
                "backoff_base_seconds":float(raw.get("backoff_base_seconds","0.5")),"backoff_max_seconds":float(raw.get("backoff_max_seconds","8")),
                "respect_retry_after":raw.get("respect_retry_after","true")=="true","consecutive_error_threshold":int(raw.get("consecutive_error_threshold","5")),
                "cooldown_seconds":float(raw.get("cooldown_seconds","30")),"max_run_duration_seconds":float(raw.get("max_run_duration_seconds","7200"))}
            from .source_jobs import SOURCES
            source=next((r for r in store.suppliers() if r['id']==supplier_id),None)
            if source and source['code'] in SOURCES:
                if not raw.get('min_request_interval_seconds'):raise HTTPException(400,'Укажите интервал утверждённой host policy')
                from .host_fetch import compile_policy
                from .source_discovery import HOSTS
                try:compile_policy(source['code'],source.get('base_url') or 'https://'+HOSTS[source['code']],values,approval=(source.get('crawl_settings') or {}).get('host_policy_approval'))
                except PermissionError as error:raise ValueError('Профиль host не утверждён: '+str(error))
            DomainRatePolicy.from_mapping(values)
        except (TypeError, ValueError) as error:
            suppliers = store.suppliers()
            for supplier in suppliers: supplier["schedule_controls"] = schedule_to_controls(supplier.get("cron_expression"))
            return templates.TemplateResponse(request, "scheduler.html", context(request, suppliers=suppliers, active="scheduler", advanced=True, validation_error=f"Настройки не сохранены: {error}"), status_code=400)
        store.save_schedule(supplier_id, values); admin_store.audit(user.id, "SCHEDULE_CHANGED", "supplier_schedule", supplier_id, None, values)
        return RedirectResponse("/scheduler?saved=1", 303)

    @app.get('/exports/previews',response_class=HTMLResponse)
    def dual_previews_page(request: Request):
        guard(request)
        from universal_supplier.dual_preview import latest,publication_status
        from universal_supplier.export_history import listing
        manifest=None;error=None
        try:manifest=latest(preview_root,attempt=(preview_root/'latest_attempt.json').exists())
        except FileNotFoundError:pass
        except (ValueError,KeyError,OSError):error='PREVIEW_INTEGRITY_ERROR: артефакт не выдан'
        return templates.TemplateResponse(request,'dual_previews.html',context(request,
            preview=manifest,publication=publication_status(preview_root),history=listing(preview_root),error=error,active='xml_export'))

    @app.get('/exports/previews/history/{artifact_id}')
    def historical_preview(request: Request,artifact_id:str):
        guard(request)
        from universal_supplier.export_history import read
        try:raw=read(preview_root,artifact_id)
        except (ValueError,KeyError,OSError):raise HTTPException(404,'HISTORY_UNAVAILABLE')
        return Response(raw,media_type='application/xml',headers={'Content-Disposition':'attachment; filename="historical-not-current.xml"','X-Sterbrust-Import-Allowed':'false','X-Sterbrust-Artifact-Status':'HISTORICAL_NOT_CURRENT'})

    @app.get('/exports/previews/diagnostics/download')
    def preview_diagnostics(request: Request):
        guard(request)
        from universal_supplier.dual_preview import latest
        try:manifest=latest(preview_root,attempt=True)
        except (ValueError,KeyError,OSError):raise HTTPException(409,'PREVIEW_DIAGNOSTICS_UNAVAILABLE')
        return Response(json.dumps(manifest,ensure_ascii=False,default=str),media_type='application/json',headers={'Content-Disposition':'attachment; filename="current-preview-exclusions.json"','X-Sterbrust-Import-Allowed':'false'})

    @app.get('/exports/previews/preserved/{kind}')
    def preserved_preview(request: Request,kind:str):
        guard(request)
        from universal_supplier.dual_preview import latest,NAMES
        if kind not in NAMES:raise HTTPException(404,'Неизвестный XML')
        try:data=latest(preview_root,kind)
        except (ValueError,KeyError,OSError):raise HTTPException(409,'PRESERVED_PREVIEW_UNAVAILABLE')
        return Response(data,media_type='application/xml',headers={'Content-Disposition':'attachment; filename="preserved-not-current.xml"','X-Sterbrust-Import-Allowed':'false','X-Sterbrust-Artifact-Status':'HISTORICAL_NOT_CURRENT_REVALIDATION_REQUIRED'})

    @app.get('/exports/previews/candidates/download')
    def preserved_candidates(request: Request):
        guard(request)
        from universal_supplier.dual_preview import latest
        try:
            cohort=json.loads((preview_root/'historical_new_candidates.json').read_text(encoding='utf-8'))
            current=latest(preview_root,attempt=True)
            rows={r['source_product_id']:r for r in current['record_trace']['new_products']}
            members=[{**member,'current':rows.get(member['source_product_id'],
                {'selected':False,'reasons':['SOURCE_ROW_MISSING_REQUIRES_REVIEW']})} for member in cohort['members']]
        except (ValueError,KeyError,OSError):raise HTTPException(409,'CANDIDATE_DIAGNOSTICS_UNAVAILABLE')
        return Response(json.dumps({**cohort,'as_of':current['generated_at'],'members':members,'import_allowed':False},ensure_ascii=False),
            media_type='application/json',headers={'Content-Disposition':'attachment; filename="preserved-new-candidates.json"','X-Sterbrust-Import-Allowed':'false'})

    @app.post('/exports/previews/generate')
    async def dual_previews_generate(request: Request):
        user=guard(request,MUTATING_ROLES);require_trusted_origin(request)
        raw=await form_values(request);check_csrf(request,raw)
        try:manifest=xml_service.publish_previews(preview_root)
        except (ValueError,KeyError,OSError) as error:return page_error(request,'XML не заменён: '+str(error))
        admin_store.audit(user.id or None,'LOCAL_PREVIEW_EXPORTED','xml_preview',manifest['version'],None,
            {'counts':manifest['counts'],'checksums':manifest['checksums'],'import_allowed':False})
        return RedirectResponse('/exports/previews',303)

    @app.get('/exports/previews/{kind}/download')
    def dual_previews_download(request: Request,kind: str):
        guard(request)
        from universal_supplier.dual_preview import latest,NAMES
        if kind not in NAMES:raise HTTPException(404,'Неизвестный XML-предпросмотр')
        try:data=latest(preview_root,kind,attempt=(preview_root/'latest_attempt.json').exists(),require_continuity=True)
        except FileNotFoundError:raise HTTPException(404,'XML ещё не сформирован')
        except (ValueError,KeyError,OSError):raise HTTPException(409,'PREVIEW_INTEGRITY_ERROR')
        return Response(data,media_type='application/xml',headers={'Content-Disposition':f'attachment; filename="{NAMES[kind]}"','X-Sterbrust-Import-Allowed':'false','X-Sterbrust-Artifact-Status':'LATEST_ATTEMPT_NOT_IMPORT_READY'})

    @app.get('/suppliers/{supplier_id}/freshness',response_class=HTMLResponse)
    def supplier_freshness(request: Request,supplier_id: int):
        guard(request)
        if not hasattr(store,'dual_preview_snapshot'):raise HTTPException(503,'Freshness требует PostgreSQL snapshot')
        from universal_supplier.commercial_freshness import assess
        from .supplier_automation import settings_for
        snapshot=store.dual_preview_snapshot()
        item=next((s for s in snapshot['suppliers'] if s['id']==supplier_id),None)
        if not item:raise HTTPException(404,'Поставщик не найден')
        cfg=settings_for(item);seen=set();rows=[]
        for row in snapshot['records']:
            if row['supplier_id']!=supplier_id or row['source_product_id'] in seen:continue
            seen.add(row['source_product_id'])
            rows.append({'product':row,'freshness':assess(row,cfg,snapshot['as_of'])})
        return templates.TemplateResponse(request,'supplier_freshness.html',context(request,
            item=item,rows=rows,as_of=snapshot['as_of'],active='suppliers'))

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        user = guard(request); return templates.TemplateResponse(request, "settings.html", context(request, users=admin_store.list_users() if user.role == "ADMIN" else [], audit=admin_store.audit_events() if user.role == "ADMIN" else [], active="settings"))

    @app.get("/data-quality", response_class=HTMLResponse)
    def data_quality(request: Request):
        guard(request); history=stage6e_service.quality_history(request.query_params.get("supplier") or None,10) if hasattr(stage6e_service,"quality_history") else []
        return templates.TemplateResponse(request,"data_quality.html",context(request,rows=stage6e_service.quality(),history=mark_quality_repeats(history),active="data_quality"))

    @app.get("/rules", response_class=HTMLResponse)
    def rules(request: Request):
        guard(request); rows=stage6e_service.list_rules() if hasattr(stage6e_service,"list_rules") else stage6e_service.rules
        return templates.TemplateResponse(request,"rules.html",context(request,rows=rows,suppliers=store.suppliers(),active="rules"))

    @app.post("/rules")
    async def add_rule(request: Request):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        if not values.get("reason","").strip(): return page_error(request,"Для правила обязательна причина")
        rule_type=values.get("rule_type","FORCE_REVIEW"); config_value=values.get("config_value","").strip(); config={}
        if rule_type in {"BRAND_OVERRIDE","CATEGORY_OVERRIDE"}: config={"value":config_value}
        elif rule_type=="DO_NOT_USE_SUPPLIER_FIELD": config={"field":config_value}
        elif rule_type=="PROPERTY_MAPPING_OVERRIDE" and is_id(config_value): config={"canonical_property_id":int(config_value)}
        before=None
        try: created=stage6e_service.add_rule({"supplier_id":values.get("supplier_id"),"scope":values.get("scope","product"),"entity_key":values.get("entity_key",""),"rule_type":rule_type,"config":config,"reason":values["reason"].strip()},user.id)
        except (ValueError,KeyError) as error: return page_error(request,f"Правило не сохранено: {error}")
        admin_store.audit(user.id,"BUSINESS_RULE_CREATED","business_rule",created["id"],before,dict(created))
        return RedirectResponse("/rules?saved=1",303)

    @app.post("/rules/{rule_id}/active")
    async def rule_active(request: Request, rule_id: int):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        try: row=stage6e_service.set_rule_active(rule_id,values.get("active")=="true",user.id,values.get("reason",""))
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"BUSINESS_RULE_LIFECYCLE_CHANGED","business_rule",rule_id,None,dict(row))
        return RedirectResponse("/rules?saved=1",303)

    @app.get("/properties", response_class=HTMLResponse)
    def properties(request: Request):
        guard(request); return templates.TemplateResponse(request,"properties.html",context(request,data=stage6e_service.property_dashboard(),active="properties"))

    @app.post("/properties/mappings/{mapping_id}/confirm")
    async def confirm_property_mapping(request: Request,mapping_id:int):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        try: row=stage6e_service.confirm_mapping(mapping_id,int(values.get("canonical_property_id","0")),user.id)
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"PROPERTY_MAPPING_CONFIRMED","supplier_property_mapping",mapping_id,None,dict(row))
        return RedirectResponse("/properties?saved=1",303)

    @app.post("/properties/mappings/{mapping_id}/decision")
    async def decide_property_mapping(request: Request,mapping_id:int):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        try: row=stage6e_service.decide_mapping(mapping_id,values.get("status",""),user.id,values.get("comment",""))
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"PROPERTY_MAPPING_DECIDED","supplier_property_mapping",mapping_id,None,dict(row))
        return RedirectResponse("/properties?saved=1",303)

    @app.post("/properties/canonical")
    async def create_canonical_property(request: Request):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        for key in ("show_in_characteristics","usable_for_identity","identity_critical","usable_as_filter"):
            values[key]=values.get(key)=="true"
        try: row=stage6e_service.create_canonical_property(values,user.id)
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"CANONICAL_PROPERTY_CREATED","canonical_property",row["id"],None,dict(row))
        return RedirectResponse("/properties?saved=1",303)

    @app.post("/properties/profiles")
    async def save_property_profile(request: Request):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        values["use_as_characteristic"]=values.get("use_as_characteristic")=="true"; values["use_as_filter"]=values.get("use_as_filter")=="true"
        try: row=stage6e_service.save_profile_property(values,user.id)
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"CATEGORY_FILTER_PROFILE_UPDATED","category_filter_profile",row["profile_id"],None,dict(row))
        return RedirectResponse("/properties?saved=1",303)

    @app.post("/properties/profiles/{profile_id}/{canonical_property_id}/remove")
    async def remove_property_profile(request: Request,profile_id:int,canonical_property_id:int):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        stage6e_service.remove_profile_property(profile_id,canonical_property_id)
        admin_store.audit(user.id,"CATEGORY_FILTER_PROFILE_PROPERTY_REMOVED","category_filter_profile",profile_id,{"canonical_property_id":canonical_property_id},None)
        return RedirectResponse("/properties?saved=1",303)

    @app.post("/properties/profiles/{profile_id}/{canonical_property_id}/update")
    async def update_property_profile(request: Request,profile_id:int,canonical_property_id:int):
        user=guard(request,{"ADMIN"}); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        action=values.get("action","")
        payload={}
        if action=="toggle_characteristic": payload["use_as_characteristic"]=values.get("use_as_characteristic")=="true"
        elif action=="toggle_filter": payload["use_as_filter"]=values.get("use_as_filter")=="true"
        elif action=="set_position":
            try: payload["position"]=int(values.get("position","0"))
            except ValueError: return page_error(request,"Позиция должна быть числом")
        else: return page_error(request,"Неизвестное действие профиля")
        try: row=stage6e_service.update_profile_property(profile_id,canonical_property_id,payload)
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"CATEGORY_FILTER_PROFILE_PROPERTY_UPDATED","category_filter_profile",profile_id,None,dict(row))
        return RedirectResponse("/properties?saved=1",303)

    @app.get("/properties/export.xlsx")
    def properties_export(request: Request):
        user=guard(request); data=stage6e_service.property_dashboard(); rows=[]
        for row in data["mappings"]:
            rows.append({"category":row.get("category_context"),"supplier":row.get("supplier_name"),"source":row.get("source_name"),"sample":row.get("sample_value"),"type":row.get("inferred_type"),"source_unit":row.get("source_unit"),"canonical":row.get("canonical_name"),"canonical_unit":row.get("canonical_unit"),"sterbrust":row.get("sterbrust_name") or row.get("sterbrust_code") or row.get("sterbrust_property_id") or "","characteristic":"Да" if row.get("show_in_characteristics") else "Нет" if row.get("canonical_property_id") else "","filter":"Да" if row.get("usable_as_filter") else "Нет" if row.get("canonical_property_id") else "","identity":"Да" if row.get("identity_critical") else "Нет" if row.get("canonical_property_id") else "","status":row.get("status"),"comment":row.get("comment")})
        columns=(("category","Категория"),("supplier","Поставщик"),("source","Исходное свойство"),("sample","Пример значения"),("type","Тип значения"),("source_unit","Исходная единица"),("canonical","Каноническое свойство"),("canonical_unit","Каноническая единица"),("sterbrust","Свойство Sterbrust"),("characteristic","Использовать в характеристиках"),("filter","Использовать в фильтре"),("identity","Identity critical"),("status","Статус"),("comment","Комментарий"))
        admin_store.audit(user.id or None,"EXPORT_CREATED","property_mapping","xlsx",None,{"rows":len(rows)})
        return Response(xlsx_bytes({"Сопоставление свойств":(columns,rows)}),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{safe_export_filename("properties_mapping","xlsx")}"'})

    @app.get("/sterbrust-preview", response_class=HTMLResponse)
    def sterbrust_preview(request: Request):
        guard(request); result=stage6e_service.preview(page=min(MAX_PAGE,max(1,optional_int(request.query_params.get("page"),"страница") or 1)),page_size=100)
        return templates.TemplateResponse(request,"sterbrust_preview.html",context(request,result=result,active="preview"))

    def _xml_payload(request: Request):
        from universal_supplier.xml_export import (
            canonical_filename, generate_canonical_xml, iter_supplier_xml, preview_xml_text,
            supplier_filename, supplier_xml_bytes, xml_stats,
        )
        from universal_supplier.yml_feed import FeedGenerationError
        mode = request.query_params.get("mode") or "short"
        mode = {"canonical": "short", "supplier": "full"}.get(mode, mode)
        filters = filters_from_request(request)
        summary = {}; stats = {}; preview_text = ""; error = ""; data = b""; filename = "export.xml"
        offer_count = 0
        if request.query_params.get("action") in {"preview", "download"} or request.url.path.endswith("/download"):
            try:
                if mode == "full":
                    rows = []
                    for index, row in enumerate(xml_service.supplier_rows(filters)):
                        if index >= 10: break
                        rows.append(row)
                    data = supplier_xml_bytes(rows, xml_service.supplier_details)
                    summary = store.full_xml_summary(filters) if hasattr(store, "full_xml_summary") else {"products": len(rows)}
                    offer_count = int(summary.get("products") or 0)
                    filename = supplier_filename(filters.supplier)
                else:
                    artifact = xml_service.canonical_artifact(filters.supplier)
                    data = artifact.data
                    offer_count = int(artifact.manifest.get("exported_offer_count") or 0)
                    summary = xml_service.canonical_summary(filters.supplier)
                    summary.update(artifact.manifest.get("short_xml_exclusion_counts") or {})
                    summary["selected"] = offer_count
                    filename = canonical_filename(filters.supplier)
                stats = xml_stats(data); stats["offer_count"] = offer_count
                preview_text = preview_xml_text(data)
            except FeedGenerationError as exc:
                error = "XML не выдан: " + "; ".join(exc.violations[:20])
            except Exception as exc:
                LOGGER.exception("XML export failed")
                viewer = current_user(request)
                detail = f": {str(exc)[:500]}" if viewer and viewer.role == "ADMIN" else ""
                error = f"XML не выдан: {type(exc).__name__}{detail}"
        return mode, filters, summary, stats, preview_text, error, data, filename

    @app.get("/exports/xml", response_class=HTMLResponse)
    def xml_export_page(request: Request):
        guard(request)
        mode, filters, summary, stats, preview_text, error, data, filename = _xml_payload(request)
        return templates.TemplateResponse(request, "xml_export.html", context(
            request, mode=mode, filters=filters, options=store.filter_options(),
            preview=request.query_params.get("action") == "preview",
            summary=summary, stats=stats, preview_text=preview_text, error=error, active="xml_export"))

    @app.get("/exports/xml/download")
    def xml_export_download(request: Request):
        guard(request)
        requested_mode = request.query_params.get("mode") or "short"
        mode = {"canonical": "short", "supplier": "full"}.get(requested_mode, requested_mode)
        if mode == "full":
            filters = filters_from_request(request)
            from universal_supplier.xml_export import supplier_filename
            # Materialize first (RAM up to 16 MiB, then disk) so a mid-export DB error is a 500, not a truncated 200.
            spool = tempfile.SpooledTemporaryFile(max_size=16 * 1024 * 1024)
            try:
                for chunk in xml_service.iter_full(filters): spool.write(chunk)
                spool.seek(0)
            except Exception as exc:
                spool.close(); return page_error(request, f"XML не выдан: {type(exc).__name__}", status_code=500)
            def spooled():
                try:
                    while chunk := spool.read(256 * 1024): yield chunk
                finally: spool.close()
            return StreamingResponse(spooled(), media_type="application/xml; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{supplier_filename(filters.supplier)}"'})
        mode, filters, summary, stats, preview_text, error, data, filename = _xml_payload(request)
        if error or not data:
            return page_error(request, error or "Нет XML для скачивания")
        return Response(data, media_type="application/xml; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/exports/{entity}.{extension}")
    def section_export(request: Request,entity:str,extension:str):
        user=guard(request)
        if entity=="review": rows=filtered_review_cases(request)
        elif entity=="suppliers": rows=store.suppliers()
        elif entity=="runs": rows=store.runs()
        elif entity=="quality": rows=stage6e_service.quality()
        elif entity=="properties": rows=stage6e_service.property_dashboard()["mappings"]
        else: raise HTTPException(404,"Неизвестный раздел экспорта")
        keys=list(rows[0]) if rows else ["status"]; forbidden={"password_hash","session_secret","proxy_credentials","csrf_secret","signing_secret"}
        columns=tuple((key,key) for key in keys if key not in forbidden)
        admin_store.audit(user.id or None,"EXPORT_CREATED",entity,extension,None,{"rows":len(rows),"filters":dict(request.query_params)})
        if extension=="csv": body=csv_bytes(rows,columns); media="text/csv; charset=utf-8"
        elif extension=="xlsx": body=xlsx_bytes({entity[:31]:(columns,rows)}); media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else: raise HTTPException(400,"Поддерживаются только CSV и XLSX")
        return Response(body,media_type=media,headers={"Content-Disposition":f'attachment; filename="{safe_export_filename(entity,extension,request.query_params.get("supplier",""))}"'})

    @app.get("/changes/export.{extension}")
    def changes_export(request:Request,extension:str):
        user=guard(request); period=request.query_params.get("period","7d")
        if period not in {"last_run","24h","7d","between"} or (period=="between" and not (is_id(request.query_params.get("run_a","")) and is_id(request.query_params.get("run_b","")))):
            raise HTTPException(400,"Для диапазона нужны корректные Run A и Run B")
        try: rows=store.change_rows(period=period,run_a=int(request.query_params["run_a"]) if is_id(request.query_params.get("run_a","")) else None,run_b=int(request.query_params["run_b"]) if is_id(request.query_params.get("run_b","")) else None)
        except ValueError as error: raise HTTPException(400,str(error))
        columns=(("supplier_name","Поставщик"),("external_id","External ID"),("article","Артикул"),("field","Поле"),("old_value","Было"),("new_value","Стало"),("changed_at","Время"),("run_id","Run ID"))
        admin_store.audit(user.id or None,"EXPORT_CREATED","changes",extension,None,{"rows":len(rows),"period":period})
        if extension=="csv": body=csv_bytes(rows,columns); media="text/csv; charset=utf-8"
        elif extension=="xlsx": body=xlsx_bytes({"Изменения":(columns,rows)}); media="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        else: raise HTTPException(400,"Поддерживаются только CSV и XLSX")
        return Response(body,media_type=media,headers={"Content-Disposition":f'attachment; filename="{safe_export_filename("changes_"+period,extension)}"'})

    @app.get("/parser-inspector", response_class=HTMLResponse)
    def parser_inspector(request: Request):
        guard(request); supplier_code=request.query_params.get("supplier",""); external_id=request.query_params.get("external_id",""); known_url=""
        if supplier_code and external_id:
            known=store.product(supplier_code,external_id)
            known_url=(known or {}).get("source_url","")
        return templates.TemplateResponse(request,"parser_inspector.html",context(request,suppliers=store.suppliers(),result=None,selected_supplier=supplier_code,known_url=known_url,active="inspector"))

    @app.post("/parser-inspector", response_class=HTMLResponse)
    def parser_inspector_submit(request: Request, values: dict[str, str] = Depends(form_dependency)):
        # Plain def: the blocking HTTP fetch runs in Starlette's threadpool, not on the event loop.
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); check_csrf(request,values)
        supplier=next((x for x in store.suppliers() if str(x.get("code"))==values.get("supplier")),None)
        if not supplier: return page_error(request,"Поставщик не найден")
        from urllib.parse import urlparse
        allowed=[urlparse(str(supplier.get("base_url",""))).hostname or ""]
        try: checked=validate_supplier_url(values.get("url",""),allowed,resolve_dns=True)
        except ValueError as error: return page_error(request,str(error))
        rate=DomainRatePolicy.from_mapping(supplier.get("rate_settings") or {})
        try:
            transport=interactive_transport(rate)
            validator=lambda target: validate_supplier_url(target,allowed,resolve_dns=True)
            fetched=transport.get_validated(checked,validator)
            validate_fetch_targets(fetched,allowed,resolve_dns=True)
            if supplier.get("code")=="partner_st":
                from universal_supplier.adapters.partner_st import PartnerStAdapter
                card=PartnerStAdapter().parse_product(fetched)
            elif supplier.get("code")=="optimum":
                from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
                from universal_supplier.adapters.optimum import to_product_card
                parsed=OptimumProductParser().parse_product(fetched.text,checked,fetched.final_url,())
                card=to_product_card(parsed,final_url=fetched.final_url)
            else: raise ValueError("Для поставщика не зарегистрирован инспектор")
            result={"url":checked,"supplier":supplier.get("name"),"status":fetched.status_code,"canonical_url":card.canonical_url,"external_id":card.external_id,"sku":card.sku,"name":card.name,"brand":card.brand,"model":card.raw_data.get("model") or card.raw_data.get("source_record",{}).get("identity",{}).get("model"),"price":card.price,"currency":card.currency,"availability":card.availability_normalized,"description":card.description_text,"properties_count":len(card.properties),"images_count":sum(x.media_type=="image" for x in card.media),"documents_count":sum(x.media_type=="document" for x in card.media),"relations_count":len(card.options),"raw":card.raw_data,"normalized":card.as_jsonable()}
        except Exception as error:
            return page_error(request,"Проверка парсера завершилась ошибкой",details=f"{type(error).__name__}: {str(error)[:1000]}")
        admin_store.audit(user.id,"PARSER_INSPECTION_REQUESTED","supplier",supplier.get("id"),None,{"url":checked})
        return templates.TemplateResponse(request,"parser_inspector.html",context(request,suppliers=store.suppliers(),result=result,selected_supplier=supplier.get("code"),known_url=checked,active="inspector"))

    @app.get("/products/{supplier_code}/{external_id}/reparse", response_class=HTMLResponse)
    def product_reparse(request: Request,supplier_code:str,external_id:str):
        guard(request)
        target=stage6e_service.reparse_target(supplier_code,external_id) if hasattr(stage6e_service,"reparse_target") else None
        if not target: raise HTTPException(404,"Товар для повторной проверки не найден")
        return templates.TemplateResponse(request,"reparse.html",context(request,target=target,preview=None,active="products"))

    @app.post("/products/{supplier_code}/{external_id}/reparse", response_class=HTMLResponse)
    def product_reparse_run(request: Request,supplier_code:str,external_id:str,values: dict[str, str] = Depends(form_dependency)):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); check_csrf(request,values)
        target=stage6e_service.reparse_target(supplier_code,external_id) if hasattr(stage6e_service,"reparse_target") else None
        if not target: raise HTTPException(404,"Товар для повторной проверки не найден")
        from urllib.parse import urlparse
        allowed=[urlparse(str(target["base_url"])).hostname or ""]
        try:
            checked=validate_supplier_url(str(target["source_url"]),allowed,resolve_dns=True)
            default_policy=PARTNER_ST_DEFAULT_RATE_POLICY if supplier_code=="partner_st" else OPTIMUM_DEFAULT_RATE_POLICY
            overlay=default_policy.snapshot(); overlay.update(target.get("rate_settings") or {})
            transport=interactive_transport(DomainRatePolicy.from_mapping(overlay))
            fetched=transport.get_validated(checked,lambda value: validate_supplier_url(value,allowed,resolve_dns=True))
            validate_fetch_targets(fetched,allowed,resolve_dns=True)
            if supplier_code=="partner_st":
                from universal_supplier.adapters.partner_st import PartnerStAdapter
                card=PartnerStAdapter().parse_product(fetched)
            elif supplier_code=="optimum":
                from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
                from universal_supplier.adapters.optimum import to_product_card
                card=to_product_card(OptimumProductParser().parse_product(fetched.text,checked,fetched.final_url,()),final_url=fetched.final_url)
            else: raise ValueError("Для поставщика не зарегистрирован повторный парсинг")
            preview=stage6e_service.save_reparse_preview(target,card,user.id)
        except Exception as error: return page_error(request,"Повторная проверка завершилась ошибкой",details=f"{type(error).__name__}: {str(error)[:1000]}")
        admin_store.audit(user.id,"SINGLE_PRODUCT_REPARSE_PREVIEW","source_product",target["source_product_id"],None,{"preview_id":preview["id"],"http_calls":1,"discovery_calls":0,"full_crawl_calls":0})
        return templates.TemplateResponse(request,"reparse.html",context(request,target=target,preview=preview,active="products"))

    @app.post("/reparse/{preview_id}/persist")
    async def reparse_persist(request: Request,preview_id:int):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        if values.get("confirm")!="true": return page_error(request,"Нужно подтвердить сохранение одного товара")
        try: row=stage6e_service.persist_reparse_preview(preview_id,user.id)
        except (ValueError,KeyError) as error: return page_error(request,str(error))
        admin_store.audit(user.id,"SINGLE_PRODUCT_REPARSE_PERSISTED","reparse_preview",preview_id,None,dict(row))
        return RedirectResponse(f"/products?reparse_persisted={preview_id}",303)

    @app.post("/settings/users")
    async def create_user_route(request: Request):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        # Same re-authentication as POST /users: a live admin session alone must
        # not be enough to create another persistent account (review 2026-11-26 B3).
        reauth_key = ("reauth:" + user.username, request.client.host if request.client else "")
        if login_throttle.locked(reauth_key):
            raise HTTPException(429, "Слишком много попыток подтверждения пароля")
        found = admin_store.user_by_username(user.username)
        if not found or found[0].id != user.id or not verify_password(values.get("current_password", ""), found[1]):
            login_throttle.failure(reauth_key)
            raise HTTPException(403, "Текущий пароль неверен")
        login_throttle.reset(reauth_key)
        from .access_policy import validate_new_password
        try: validate_new_password(values.get("password",""),values.get("password",""))
        except ValueError as error: return page_error(request,str(error))
        try: admin_store.create_user(values.get("username", ""), values.get("display_name", ""), hash_password(values.get("password", "")), values.get("role", "VIEWER"), actor_id=user.id, must_change_password=True)
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/settings?user_saved=1", 303)

    @app.post("/settings/users/{user_id}/active")
    async def user_active(request: Request, user_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        try: admin_store.set_user_active(user_id, values.get("active") == "true", user.id)
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/settings", 303)

    @app.post("/settings/users/{user_id}/revoke-sessions")
    async def user_revoke_sessions(request: Request, user_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        try: admin_store.revoke_sessions(user_id, user.id, "admin")
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/login" if user_id == user.id else "/settings", 303)

    def final_release_store():
        from .final_release_store import FinalReleaseStore
        directory=os.environ.get("FINAL_RC_ARTIFACTS_DIR")
        if not directory: raise HTTPException(503,"Финальный RC пакет не подключён")
        try: return FinalReleaseStore(directory,os.environ.get("FINAL_RC_MANIFEST_SHA256",""))
        except (ValueError,OSError): raise HTTPException(503,"Проверка SHA финального RC пакета не пройдена")

    @app.get("/rc-final", response_class=HTMLResponse)
    def final_release_page(request:Request):
        guard(request)
        try:
            data=final_release_store().page(request.query_params.get("classification",""),
                                           request.query_params.get("supplier",""),min(MAX_PAGE,int(request.query_params.get("page","1"))))
        except ValueError as error: raise HTTPException(400,str(error))
        return templates.TemplateResponse(request,"final_release.html",context(request,release=data,active="rc_final"))

    @app.get("/rc-final/files/{filename}")
    def final_release_file(request:Request,filename:str):
        guard(request)
        if not filename.endswith((".xml",".csv",".xlsx")): raise HTTPException(404,"Файл не найден")
        try: body=final_release_store().file(filename)
        except ValueError: raise HTTPException(404,"Файл не прошёл SHA/allowlist проверку")
        except OSError: raise HTTPException(404,"Файл не найден")
        return Response(body,media_type="application/octet-stream",headers={"Content-Disposition":f'attachment; filename="{filename}"'})

    @app.get("/health")
    def health():
        body, status_code = public_health(store.health()); return JSONResponse(body, status_code=status_code)
    @app.get("/ready")
    def ready():
        status = store.health(); ok = status.get("postgresql") in HEALTHY_DATABASE_STATES; return JSONResponse({"ready":ok}, status_code=200 if ok else 503)
    if cabinet_service is not None:
        from .cabinet import register_cabinet
        register_cabinet(app,templates,context,guard,cabinet_service,check_csrf,preserve_supplier_landing=True)
    return app


def create_unavailable_app(message: str) -> FastAPI:
    """Fail closed: no auth bypass when configuration is broken; only health probes answer."""
    store = UnavailableControlPlaneStore(message)
    app = FastAPI(title="Универсальный поставщик — недоступно", docs_url=None, redoc_url=None)
    app.state.store = store; app.state.auth_required = True

    @app.get("/health")
    def health(): return JSONResponse(public_health(store.health())[0], status_code=503)
    @app.get("/ready")
    def ready(): return JSONResponse({"ready": False}, status_code=503)
    @app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
    def unavailable(path: str): return PlainTextResponse("Сервис недоступен: конфигурация не загружена", status_code=503)
    return app


if os.environ.get('CONTROL_PLANE_SKIP_DEFAULT_APP')=='YES':
    app = None  # Explicit local factory launcher; no default supplier DB bootstrap.
else:
    try:
        app = create_app()
    except RuntimeError as error:
        app = create_unavailable_app(str(error))
