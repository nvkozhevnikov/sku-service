from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, quote
import uuid

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.trustedhost import TrustedHostMiddleware

from universal_supplier.postgres import PostgresConfig
from universal_supplier.stage6e import (
    InMemoryStage6EService, PostgresStage6EService, PRODUCT_COLUMNS, csv_bytes, csv_chunks,
    effective_crawl_settings, product_workbook, safe_export_filename, validate_supplier_url,
    validate_fetch_targets, xlsx_bytes,
)
from universal_supplier.xml_export import XmlExportService
from .admin_store import InMemoryAdminStore, PostgresAdminStore
from .commercial_qa import CommercialQaService, CommercialQaUnavailable
from .queue import InMemoryJobQueue, PostgresJobQueue
from .rate_control import DomainRatePolicy, OPTIMUM_DEFAULT_RATE_POLICY, PARTNER_ST_DEFAULT_RATE_POLICY
from .scheduler import controls_to_schedule, schedule_to_controls, validate_schedule
from .security import AuthUser, SessionSigner, hash_password, verify_password
from .store import PostgresControlPlaneStore, ProductFilters, SnapshotControlPlaneStore, UnavailableControlPlaneStore

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
    "USER_CREATED":"Создан пользователь", "USER_ACTIVE_CHANGED":"Изменено состояние пользователя",
    "SUPPLIER_ENABLED_CHANGED":"Изменено состояние поставщика", "SCHEDULE_CHANGED":"Изменено расписание",
    "MANUAL_CRAWL_ENQUEUED":"Ручной сбор поставлен в очередь", "CANDIDATE_REJECTED":"Кандидат отклонён",
    "REVIEW_REQUIRED_SOURCE_CHANGED":"Требуется повторная проверка после изменения",
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
               commercial_qa_service=None) -> FastAPI:
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
    app.mount("/static", StaticFiles(directory=PACKAGE / "static"), name="static")
    templates = Jinja2Templates(directory=PACKAGE / "templates")
    templates.env.filters["ru"] = russian_label
    templates.env.filters["product_kind_ru"] = product_kind_label
    templates.env.filters["relation_ru"] = relation_type_label

    def payload(request: Request):
        return signer.read(request.cookies.get(signer.cookie_name)) if auth_required else {"uid": 0, "role": "ADMIN", "csrf": "test-csrf"}
    def current_user(request: Request):
        data = payload(request)
        if not data: return None
        if not auth_required: return AuthUser(0, "test", "Тестовый администратор", "ADMIN")
        user = admin_store.user_by_id(int(data["uid"]))
        return user if user and user.is_active and user.role == data.get("role") else None
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
        return {"request": request, "current_user": current_user(request), "csrf_token": data.get("csrf", "") if data else "",
            "safety_banner": "ИМПОРТ В STERBRUST ОТКЛЮЧЁН — КОНТРАКТ ИМПОРТА НЕ ПОДТВЕРЖДЁН",
            "commercial_qa_available": bool(getattr(commercial_qa_service, "enabled", False)),
            "final_rc_available": bool(os.environ.get("FINAL_RC_ARTIFACTS_DIR")),
            "control_plane_read_only": read_only_source, **values}

    @app.middleware("http")
    async def block_qa_source_writes(request: Request, call_next):
        allowed_posts = {"/login", "/logout", "/commercial-qa/runs"}
        if getattr(type(store), "read_only", False) and request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in allowed_posts:
            if request.url.path.startswith("/suppliers/") and request.url.path.endswith("/start") and commercial_qa_service.enabled:
                return HTMLResponse(
                    "<h1>Обычный запуск недоступен</h1>"
                    "<p>Источник доступен только для чтения; обычные изменения поставщика заблокированы. "
                    "Для коммерческих источников используйте отдельный защищённый ручной сбор.</p>"
                    '<p><a href="/commercial-qa">Открыть ручной сбор</a></p>', status_code=403)
            return PlainTextResponse("Источник доступен только для чтения; обычные изменения заблокированы.", status_code=403)
        return await call_next(request)
    def page_error(request: Request, message: str, status_code=400, details=""):
        user = current_user(request)
        return templates.TemplateResponse(request, "error.html", context(request, message=message,
            details=details if user and user.role == "ADMIN" else "", active=""), status_code=status_code)

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        if exc.status_code == 401 and request.method == "GET":
            return RedirectResponse("/login?next=" + quote(request.url.path), status_code=303)
        return page_error(request, str(exc.detail), exc.status_code)

    @app.get("/login", response_class=HTMLResponse)
    def login_page(request: Request):
        if current_user(request): return RedirectResponse("/", 303)
        try: users_exist = admin_store.users_exist()
        except Exception: users_exist = False
        return templates.TemplateResponse(request, "login.html", context(request, users_exist=users_exist, error="", active=""))

    @app.post("/login")
    async def login(request: Request):
        require_trusted_origin(request); values = await form_values(request); found = admin_store.user_by_username(values.get("username", ""))
        if not found or not found[0].is_active or not verify_password(values.get("password", ""), found[1]):
            return templates.TemplateResponse(request, "login.html", context(request, users_exist=admin_store.users_exist(), error="Неверное имя пользователя или пароль", active=""), status_code=401)
        user, _ = found; admin_store.touch_login(user.id); token, _ = signer.issue(user)
        response = RedirectResponse(values.get("next") or "/", 303)
        response.set_cookie(signer.cookie_name, token, httponly=True, samesite="lax", secure=os.environ.get("HTTPS_ENABLED") == "YES", max_age=signer.ttl_seconds, path="/")
        return response

    @app.post("/logout")
    async def logout(request: Request):
        guard(request); values = await form_values(request); check_csrf(request, values)
        response = RedirectResponse("/login", 303); response.delete_cookie(signer.cookie_name, path="/"); return response

    @app.get("/", response_class=HTMLResponse)
    def dashboard(request: Request):
        guard(request); suppliers = store.dashboard(); counts = admin_store.review_counts()
        counts["errors"] = sum(int(row.get("errors_30d") or 0) for row in suppliers)
        counts["queued"] = sum(row.get("job_status") == "queued" for row in suppliers)
        counts["running"] = sum(row.get("job_status") == "running" for row in suppliers)
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
            details=store.export_details([int(row["source_product_id"]) for row in materialized if str(row.get("source_product_id","")).isdigit()])
        admin_store.audit(user.id or None,"EXPORT_CREATED","products","xlsx",None,{"rows":total,"filters":dict(request.query_params),"write_only":True,"chunk_size":500})
        return Response(product_workbook(rows,details),media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",headers={"Content-Disposition":f'attachment; filename="{safe_export_filename("products","xlsx",filters.supplier)}"'})

    @app.get("/review", response_class=HTMLResponse)
    def review(request: Request):
        guard(request); rows = admin_store.review_cases(status=request.query_params.get("status", ""), supplier=request.query_params.get("supplier", ""), decision=request.query_params.get("decision", ""), assignee=request.query_params.get("assignee", ""), brand=request.query_params.get("brand", ""), category=request.query_params.get("category", ""), opened_from=request.query_params.get("opened_from", ""), opened_to=request.query_params.get("opened_to", ""))
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
        case_ids = [int(x) for x in values.get("case_ids", "").split(",") if x.strip().isdigit()]
        admin_store.assign(case_ids, int(values["assigned_to_user_id"]) if values.get("assigned_to_user_id") else None, user.id)
        return RedirectResponse("/review?bulk_saved=1", 303)

    @app.post("/review/bulk")
    async def safe_bulk(request: Request):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        case_ids = [int(value) for key, value in values.items() if key.startswith("case_") and value.isdigit()]
        if not case_ids: return page_error(request, "Выберите хотя бы одну карточку")
        action = values.get("bulk_action", "")
        try:
            if action == "assign":
                admin_store.assign(case_ids, int(values["assigned_to_user_id"]) if values.get("assigned_to_user_id") else None, user.id)
            else:
                admin_store.bulk_update(case_ids, action, user.id, values.get("comment", ""))
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/review?bulk_saved=1", 303)

    @app.get("/suppliers", response_class=HTMLResponse)
    def suppliers(request: Request):
        guard(request); return templates.TemplateResponse(request, "suppliers.html", context(request, suppliers=store.suppliers(), active="suppliers"))

    @app.get("/suppliers/{supplier_id}", response_class=HTMLResponse)
    def supplier_detail(request: Request, supplier_id: int):
        guard(request); item = next((x for x in store.suppliers() if int(x["id"]) == supplier_id), None)
        if not item: raise HTTPException(404, "Поставщик не найден")
        policies = effective_crawl_settings(item.get("crawl_settings"))
        return templates.TemplateResponse(request, "supplier_detail.html", context(request, item=item, policies=policies, active="suppliers"))

    @app.post("/suppliers/{supplier_id}/enabled")
    async def supplier_enabled(request: Request, supplier_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        before = next((x for x in store.suppliers() if int(x["id"]) == supplier_id), None); enabled = values.get("enabled") == "true"
        store.set_supplier_enabled(supplier_id, enabled); admin_store.audit(user.id, "SUPPLIER_ENABLED_CHANGED", "supplier", supplier_id, {"enabled": before.get("enabled") if before else None}, {"enabled": enabled}, values.get("comment") or None)
        return RedirectResponse("/suppliers", 303)

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

    @app.post("/suppliers/{supplier_id}/start")
    async def start_now(request: Request, supplier_id: int):
        user = guard(request, MUTATING_ROLES); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        if values.get("crawl_type", "full") not in SUPPORTED_WEB_CRAWL_TYPES: raise HTTPException(400, "Можно поставить в очередь только полный сбор")
        result = queue.enqueue(supplier_id, trigger_type="manual", crawl_type="full", requested_by_user_id=user.id or None)
        admin_store.audit(user.id or None, "MANUAL_CRAWL_ENQUEUED", "crawl_job", result.job_id, None, {"supplier_id": supplier_id, "crawl_type": "full", "status": result.status, "deduplicated_job_id": result.deduplicated_job_id}, values.get("comment") or None, str(uuid.uuid4()))
        return RedirectResponse(f"/runs?enqueue_status={result.status}&job_id={result.job_id}", 303)

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
        guard(request); return templates.TemplateResponse(request, "runs.html", context(request, runs=store.runs(), active="runs", enqueue_status=request.query_params.get("enqueue_status"), job_id=request.query_params.get("job_id")))

    @app.get("/runs/{run_id}", response_class=HTMLResponse)
    def run_detail(request: Request, run_id: int):
        guard(request); item = store.run(run_id)
        if not item: raise HTTPException(404, "Запуск не найден")
        return templates.TemplateResponse(request, "run_detail.html", context(request, item=item, active="runs"))

    @app.get("/api/runs/{run_id}/progress")
    def run_progress(request: Request, run_id: int):
        guard(request)
        item = store.run_progress(run_id) if hasattr(store, "run_progress") else None
        if item is None:
            raise HTTPException(404, "Запуск не найден")
        allowed = {"run_id", "supplier_name", "run_status", "phase", "phase_label_ru", "status",
                   "total", "processed", "succeeded", "errors", "percent", "rate_per_second",
                   "eta_seconds", "eta_human_ru", "elapsed_seconds", "fetch_elapsed_seconds", "updated_at"}
        return JSONResponse({key: value for key, value in item.items() if key in allowed})

    @app.get("/scheduler", response_class=HTMLResponse)
    def scheduler_page(request: Request):
        guard(request)
        advanced = request.query_params.get("advanced") == "1"
        suppliers = store.suppliers()
        for supplier in suppliers: supplier["schedule_controls"] = schedule_to_controls(supplier.get("cron_expression"))
        return templates.TemplateResponse(request, "scheduler.html", context(request, suppliers=suppliers, active="scheduler", advanced=advanced, saved=request.query_params.get("saved")))

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
            DomainRatePolicy.from_mapping(values)
        except (TypeError, ValueError) as error:
            suppliers = store.suppliers()
            for supplier in suppliers: supplier["schedule_controls"] = schedule_to_controls(supplier.get("cron_expression"))
            return templates.TemplateResponse(request, "scheduler.html", context(request, suppliers=suppliers, active="scheduler", advanced=True, validation_error=f"Настройки не сохранены: {error}"), status_code=400)
        store.save_schedule(supplier_id, values); admin_store.audit(user.id, "SCHEDULE_CHANGED", "supplier_schedule", supplier_id, None, values)
        return RedirectResponse("/scheduler?saved=1", 303)

    @app.get("/settings", response_class=HTMLResponse)
    def settings(request: Request):
        user = guard(request); return templates.TemplateResponse(request, "settings.html", context(request, users=admin_store.list_users() if user.role == "ADMIN" else [], audit=admin_store.audit_events() if user.role == "ADMIN" else [], active="settings"))

    @app.get("/data-quality", response_class=HTMLResponse)
    def data_quality(request: Request):
        guard(request); history=stage6e_service.quality_history(request.query_params.get("supplier") or None,10) if hasattr(stage6e_service,"quality_history") else []
        return templates.TemplateResponse(request,"data_quality.html",context(request,rows=stage6e_service.quality(),history=history,active="data_quality"))

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
        elif rule_type=="PROPERTY_MAPPING_OVERRIDE" and config_value.isdigit(): config={"canonical_property_id":int(config_value)}
        before=None; created=stage6e_service.add_rule({"supplier_id":values.get("supplier_id"),"scope":values.get("scope","product"),"entity_key":values.get("entity_key",""),"rule_type":rule_type,"config":config,"reason":values["reason"].strip()},user.id)
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
        guard(request); result=stage6e_service.preview(page=int(request.query_params.get("page","1") or 1),page_size=100)
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
                error = f"XML не выдан: {type(exc).__name__}: {str(exc)[:500]}"
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
            return StreamingResponse(xml_service.iter_full(filters), media_type="application/xml; charset=utf-8",
                headers={"Content-Disposition": f'attachment; filename="{supplier_filename(filters.supplier)}"'})
        mode, filters, summary, stats, preview_text, error, data, filename = _xml_payload(request)
        if error or not data:
            return page_error(request, error or "Нет XML для скачивания")
        return Response(data, media_type="application/xml; charset=utf-8",
                        headers={"Content-Disposition": f'attachment; filename="{filename}"'})

    @app.get("/exports/{entity}.{extension}")
    def section_export(request: Request,entity:str,extension:str):
        user=guard(request)
        if entity=="review": rows=admin_store.review_cases(status=request.query_params.get("status",""),supplier=request.query_params.get("supplier",""),decision=request.query_params.get("decision",""),assignee=request.query_params.get("assignee",""),brand=request.query_params.get("brand",""),category=request.query_params.get("category",""),opened_from=request.query_params.get("opened_from",""),opened_to=request.query_params.get("opened_to",""))
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
        if period not in {"last_run","24h","7d","between"} or (period=="between" and not (request.query_params.get("run_a","").isdigit() and request.query_params.get("run_b","").isdigit())):
            raise HTTPException(400,"Для диапазона нужны корректные Run A и Run B")
        try: rows=store.change_rows(period=period,run_a=int(request.query_params["run_a"]) if request.query_params.get("run_a","").isdigit() else None,run_b=int(request.query_params["run_b"]) if request.query_params.get("run_b","").isdigit() else None)
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
    async def parser_inspector_submit(request: Request):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        supplier=next((x for x in store.suppliers() if str(x.get("code"))==values.get("supplier")),None)
        if not supplier: return page_error(request,"Поставщик не найден")
        from urllib.parse import urlparse
        allowed=[urlparse(str(supplier.get("base_url",""))).hostname or ""]
        try: checked=validate_supplier_url(values.get("url",""),allowed,resolve_dns=True)
        except ValueError as error: return page_error(request,str(error))
        rate=DomainRatePolicy.from_mapping(supplier.get("rate_settings") or {})
        try:
            from universal_supplier.http import HttpTransport
            transport=HttpTransport(domain_policy=rate)
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
    async def product_reparse_run(request: Request,supplier_code:str,external_id:str):
        user=guard(request,MUTATING_ROLES); require_trusted_origin(request); values=await form_values(request); check_csrf(request,values)
        target=stage6e_service.reparse_target(supplier_code,external_id) if hasattr(stage6e_service,"reparse_target") else None
        if not target: raise HTTPException(404,"Товар для повторной проверки не найден")
        from urllib.parse import urlparse
        allowed=[urlparse(str(target["base_url"])).hostname or ""]
        try:
            checked=validate_supplier_url(str(target["source_url"]),allowed,resolve_dns=True)
            from universal_supplier.http import HttpTransport
            default_policy=PARTNER_ST_DEFAULT_RATE_POLICY if supplier_code=="partner_st" else OPTIMUM_DEFAULT_RATE_POLICY
            overlay=default_policy.snapshot(); overlay.update(target.get("rate_settings") or {})
            transport=HttpTransport(domain_policy=DomainRatePolicy.from_mapping(overlay))
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
        try: admin_store.create_user(values.get("username", ""), values.get("display_name", ""), hash_password(values.get("password", "")), values.get("role", "VIEWER"), actor_id=user.id)
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/settings?user_saved=1", 303)

    @app.post("/settings/users/{user_id}/active")
    async def user_active(request: Request, user_id: int):
        user = guard(request, {"ADMIN"}); require_trusted_origin(request); values = await form_values(request); check_csrf(request, values)
        try: admin_store.set_user_active(user_id, values.get("active") == "true", user.id)
        except (ValueError, KeyError) as error: return page_error(request, str(error))
        return RedirectResponse("/settings", 303)

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
                                           request.query_params.get("supplier",""),int(request.query_params.get("page","1")))
        except ValueError as error: raise HTTPException(400,str(error))
        return templates.TemplateResponse(request,"final_release.html",context(request,release=data,active="rc_final"))

    @app.get("/rc-final/files/{filename}")
    def final_release_file(request:Request,filename:str):
        guard(request)
        if not filename.endswith((".xml",".csv",".xlsx")): raise HTTPException(404,"Файл не найден")
        try: body=final_release_store().file(filename)
        except ValueError: raise HTTPException(404,"Файл не прошёл SHA/allowlist проверку")
        return Response(body,media_type="application/octet-stream",headers={"Content-Disposition":f'attachment; filename="{filename}"'})

    @app.get("/health")
    def health():
        status = store.health(); return JSONResponse(status, status_code=200 if status.get("postgresql") in {"ok", "demo_snapshot"} else 503)
    @app.get("/ready")
    def ready():
        status = store.health(); ok = status.get("postgresql") in {"ok", "demo_snapshot"}; return JSONResponse({"ready":ok}, status_code=200 if ok else 503)
    return app


try:
    app = create_app()
except RuntimeError as error:
    app = create_app(store=UnavailableControlPlaneStore(str(error)), queue=InMemoryJobQueue(), auth_required=False)
