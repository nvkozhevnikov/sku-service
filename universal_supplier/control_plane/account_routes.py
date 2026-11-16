"""User/role/password pages on the existing authenticated application."""
import os
import secrets
from fastapi import Request, HTTPException
from fastapi.responses import RedirectResponse

from .accounts import Accounts
from .access_policy import PERMISSIONS, application_link, validate_new_password
from .security import verify_password


def register_accounts(app, templates, context, guard, check_csrf, signer,
                      throttle, form_values, trusted_origin):
    accounts = Accounts(app.state.admin_store)
    app.state.accounts = accounts
    store = app.state.admin_store

    def page(request, **values):
        return templates.TemplateResponse(request, "accounts.html", context(request, **values))

    async def form(request, *, admin=False):
        user = guard(request, {"ADMIN"} if admin else None)
        trusted_origin(request)
        values = await form_values(request)
        check_csrf(request, values)
        return user, values

    def reauthenticate(user, values, request):
        key = ("reauth:" + user.username, request.client.host if request.client else "")
        if throttle.locked(key):
            raise HTTPException(429, "Слишком много попыток подтверждения пароля")
        found = store.user_by_username(user.username)
        if not found or found[0].id != user.id or not verify_password(values.get("current_password", ""), found[1]):
            throttle.failure(key)
            raise HTTPException(403, "Текущий пароль неверен")
        throttle.reset(key)
        return found[1]

    @app.get("/account/password")
    def password_page(request: Request):
        user = guard(request)
        return page(request, active="account", title="Изменить пароль", mode="password",
                    forced=user.must_change_password)

    @app.post("/account/password")
    async def password_change(request: Request):
        user, values = await form(request)
        expected = reauthenticate(user, values, request)
        try:
            password = validate_new_password(values.get("new_password", ""), values.get("confirmation", ""))
            if verify_password(password, expected):
                raise ValueError("Новый пароль должен отличаться от текущего")
            accounts.set_password(user.id, password, user, expected, temporary=False)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None
        response = RedirectResponse("/login?password_changed=1", 303)
        response.delete_cookie(signer.cookie_name, path="/")
        return response

    @app.get("/users")
    def users(request: Request):
        guard(request, {"ADMIN"})
        return page(request, active="users", title="Пользователи", mode="users",
                    users=store.list_users(), profiles=accounts.profiles())

    @app.post("/users")
    async def create(request: Request):
        user, values = await form(request, admin=True)
        expected = reauthenticate(user, values, request)
        try:
            password = validate_new_password(values.get("new_password", ""), values.get("confirmation", ""))
            accounts.create_user(values.get("username", ""), values.get("display_name", ""), password,
                                 values.get("profile", ""), user, expected)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None
        return RedirectResponse("/users?created=1", 303)

    @app.post("/users/{user_id}/reset-password")
    async def reset(request: Request, user_id: int):
        user, values = await form(request, admin=True)
        expected = reauthenticate(user, values, request)
        temporary = secrets.token_urlsafe(24)
        try:
            accounts.set_password(user_id, temporary, user, expected, temporary=True)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None
        # Revealed only in this authenticated, no-store response; never in URL/log/audit.
        return page(request, active="users", title="Пароль сброшен", mode="temporary",
                    temporary_password=temporary, target_id=user_id)

    @app.post("/users/{user_id}/profile")
    async def assign(request: Request, user_id: int):
        user, values = await form(request, admin=True)
        expected = reauthenticate(user, values, request)
        try:
            accounts.change_profile(user_id, values.get("profile", ""), user, expected)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None
        return RedirectResponse("/users", 303)

    @app.get("/roles")
    def roles(request: Request):
        guard(request, {"ADMIN"})
        return page(request, active="roles", title="Роли и разрешения", mode="roles",
                    profiles=accounts.profiles(), permissions=PERMISSIONS)

    @app.post("/roles")
    async def save_role(request: Request):
        user, values = await form(request, admin=True)
        expected = reauthenticate(user, values, request)
        selected = [key for key in PERMISSIONS if values.get("permission:" + key) == "on"]
        try:
            accounts.save_profile(values.get("code", ""), values.get("name", ""), selected, user, expected)
        except (ValueError, PermissionError) as error:
            raise HTTPException(400, str(error)) from None
        return RedirectResponse("/roles", 303)

    @app.get("/applications")
    def applications(request: Request):
        guard(request)
        return page(request, active="applications", title="Приложения", mode="applications",
                    content_studio_url=application_link(os.environ.get("CONTENT_STUDIO_AUTHENTICATED_URL", "")))
