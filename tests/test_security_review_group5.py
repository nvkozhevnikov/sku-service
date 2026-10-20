"""Security review 2026-10, group 5: coverage of CSRF, origin, roles, sessions, XSS and headers.

Every mutating route is discovered from the application itself, so a new POST
route without these protections fails here instead of shipping silently.
"""
from __future__ import annotations

import re
import time

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane import security
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.security import SessionSigner, hash_password
from universal_supplier.control_plane.store import UnavailableControlPlaneStore
from universal_supplier.control_plane.web import create_app
from tests.test_stage6d2 import RenderStore

SECRET = "group5-session-secret-at-least-32-bytes!"
PASSWORD = "Correct-Horse-2026"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")
COOKIE = SessionSigner.cookie_name
EVIL = "https://evil.example"
PATH_VALUES = {"supplier_id": "1", "case_id": "1", "rule_id": "1", "mapping_id": "1", "profile_id": "1",
               "canonical_property_id": "1", "user_id": "2", "preview_id": "1", "supplier_code": "partner_st",
               "external_id": "1"}
ADMIN_ONLY = {
    "/suppliers/{supplier_id}/enabled", "/suppliers/{supplier_id}/data-quality-policy",
    "/suppliers/{supplier_id}/missing-policy", "/scheduler/{supplier_id}", "/rules", "/rules/{rule_id}/active",
    "/properties/canonical", "/properties/profiles", "/properties/profiles/{profile_id}/{canonical_property_id}/remove",
    "/properties/profiles/{profile_id}/{canonical_property_id}/update", "/settings/users",
    "/settings/users/{user_id}/active", "/settings/users/{user_id}/revoke-sessions",
}


def _users():
    return [{"id": index, "username": name, "display_name": name, "role": role, "is_active": True,
             "password_hash": PASSWORD_HASH}
            for index, (name, role) in enumerate((("admin1", "ADMIN"), ("operator1", "OPERATOR"),
                                                  ("viewer1", "VIEWER")), 1)]


def _app():
    admin = InMemoryAdminStore(_users(), [])
    return create_app(store=UnavailableControlPlaneStore("offline"), queue=InMemoryJobQueue(), admin_store=admin,
                      session_secret=SECRET, auth_required=True), admin


def _login(app, username: str) -> TestClient:
    client = TestClient(app)
    assert client.post("/login", data={"username": username, "password": PASSWORD},
                       follow_redirects=False).status_code == 303
    return client


def _csrf(client: TestClient) -> str:
    return SessionSigner(SECRET).read(client.cookies.get(COOKIE))["csrf"]


def _post_routes(app) -> list[str]:
    return sorted(route.path for route in app.routes if "POST" in (getattr(route, "methods", None) or ()))


def _concrete(path: str) -> str:
    return re.sub(r"{(\w+)}", lambda match: PATH_VALUES[match.group(1)], path)


APP, _ = _app()
POST_ROUTES = _post_routes(APP)
CSRF_ROUTES = [path for path in POST_ROUTES if path != "/login"]
MUTATING_ROUTES = [path for path in CSRF_ROUTES if path != "/logout"]


def test_route_inventory_is_what_this_suite_covers():
    assert len(CSRF_ROUTES) == 25
    assert ADMIN_ONLY <= set(MUTATING_ROUTES)


# --- CSRF ----------------------------------------------------------------------------------

@pytest.mark.parametrize("path", CSRF_ROUTES)
def test_post_without_csrf_token_is_refused(path):
    app, _ = _app()
    response = _login(app, "admin1").post(_concrete(path), data={}, follow_redirects=False)
    assert response.status_code == 403 and "Защитный токен" in response.text


@pytest.mark.parametrize("path", CSRF_ROUTES)
def test_post_with_another_sessions_csrf_token_is_refused(path):
    app, _ = _app()
    victim, attacker = _login(app, "admin1"), _login(app, "admin1")
    foreign = _csrf(attacker)
    assert foreign != _csrf(victim)
    response = victim.post(_concrete(path), data={"csrf_token": foreign}, follow_redirects=False)
    assert response.status_code == 403 and "Защитный токен" in response.text


# --- Origin / Referer ------------------------------------------------------------------------

@pytest.mark.parametrize("header", ["origin", "referer"])
def test_login_refuses_foreign_origin_and_referer(header):
    app, admin = _app()
    response = TestClient(app).post("/login", data={"username": "admin1", "password": PASSWORD},
                                    headers={header: EVIL + "/login"}, follow_redirects=False)
    assert response.status_code == 403 and "Недоверенный источник" in response.text
    assert not [event for event in admin.audit_events() if event["action"] == "LOGIN_SUCCEEDED"]


@pytest.mark.parametrize("header", ["origin", "referer"])
@pytest.mark.parametrize("path", CSRF_ROUTES)
def test_mutating_routes_refuse_foreign_origin_and_referer(path, header):
    app, _ = _app()
    client = _login(app, "admin1")
    response = client.post(_concrete(path), data={"csrf_token": _csrf(client)},
                           headers={header: EVIL + "/page"}, follow_redirects=False)
    assert response.status_code == 403 and "Недоверенный источник" in response.text


# --- Role matrix -------------------------------------------------------------------------------

@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_viewer_cannot_use_any_mutating_route(path):
    app, _ = _app()
    client = _login(app, "viewer1")
    response = client.post(_concrete(path), data={"csrf_token": _csrf(client)}, follow_redirects=False)
    assert response.status_code == 403 and "Недостаточно прав" in response.text


@pytest.mark.parametrize("path", MUTATING_ROUTES)
def test_operator_is_refused_exactly_on_admin_only_routes(path):
    app, _ = _app()
    client = _login(app, "operator1")
    # Without a CSRF token: an allowed role passes the role guard and stops at CSRF,
    # a forbidden one stops at the role guard. No business code runs either way.
    response = client.post(_concrete(path), data={}, follow_redirects=False)
    assert response.status_code == 403
    expected = "Недостаточно прав" if path in ADMIN_ONLY else "Защитный токен"
    assert expected in response.text, path


# --- Sessions ----------------------------------------------------------------------------------

def _signed_in(client: TestClient) -> bool:
    return client.get("/settings", follow_redirects=False).status_code == 200


def test_role_change_invalidates_the_session_cookie():
    app, admin = _app()
    client = _login(app, "operator1")
    assert _signed_in(client)
    next(user for user in admin._users if user["username"] == "operator1")["role"] = "VIEWER"
    response = client.get("/settings", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
    # A cookie forged with the new role but the old signature still fails.
    assert client.post("/rules", data={"csrf_token": _csrf(client)}, follow_redirects=False).status_code == 401


def test_expired_session_cookie_is_refused(monkeypatch):
    app, _ = _app()
    client = _login(app, "admin1")
    assert _signed_in(client)
    real_time = time.time
    monkeypatch.setattr(security.time, "time", lambda: real_time() + 8 * 60 * 60 + 5)
    assert not _signed_in(client)
    assert client.post("/logout", data={"csrf_token": "x"}, follow_redirects=False).status_code == 401


def test_tampered_session_cookie_is_refused():
    app, _ = _app()
    client = _login(app, "viewer1")
    token = client.cookies.get(COOKIE)
    raw, signature = token.split(".", 1)
    import base64, json
    payload = json.loads(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    payload["role"] = "ADMIN"
    forged = base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).rstrip(b"=").decode()
    client.cookies.set(COOKIE, forged + "." + signature)
    assert not _signed_in(client)


# --- XSS -----------------------------------------------------------------------------------

PAYLOADS = ("javascript:alert(1)", " JAVASCRIPT:alert(1)", "data:text/html,<script>alert(1)</script>",
            "vbscript:msgbox(1)", "java\tscript:alert(1)")


@pytest.mark.parametrize("payload", PAYLOADS)
def test_hostile_product_renders_escaped_without_dangerous_href(payload):
    class Store(RenderStore):
        def product(self, supplier_code, external_id):
            row = super().product(supplier_code, external_id)
            row.update(name="<script>alert('name')</script>", description_text="<img src=x onerror=alert(1)>",
                       source_url=payload,
                       media=[{"source_url": payload, "title": "<script>alert('media')</script>"}],
                       properties=[{"raw_name": "<b>bold</b>", "raw_value": "\"><script>x</script>", "raw_unit": ""}])
            return row

    client = TestClient(create_app(store=Store(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    html = client.get("/products/optimum/3").text
    lowered = html.lower()
    for scheme in ("javascript:", "data:", "vbscript:", "java\tscript:"):
        assert f'href="{scheme}' not in lowered and f"href=\"{payload.strip().lower()}" not in lowered
    assert "<script>alert(" not in html and "<img src=x" not in html
    assert "&lt;script&gt;alert(&#39;media&#39;)&lt;/script&gt;" in html


# --- Security headers ----------------------------------------------------------------------------

@pytest.mark.parametrize("path", ["/", "/products", "/review", "/suppliers", "/runs", "/scheduler", "/settings",
                                  "/rules", "/properties", "/data-quality", "/products/optimum/404-missing"])
def test_security_headers_on_html_pages(path):
    class Store(RenderStore):
        def product(self, supplier_code, external_id):
            return None if external_id == "404-missing" else super().product(supplier_code, external_id)
    client = TestClient(create_app(store=Store(), queue=InMemoryJobQueue(), admin_store=InMemoryAdminStore(),
                                   auth_required=False))
    response = client.get(path)
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["x-frame-options"] == "DENY"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert response.headers["cache-control"] == "no-store"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]


def test_security_headers_on_login_and_forbidden_responses():
    app, _ = _app()
    login_page = TestClient(app).get("/login")
    forbidden = _login(app, "viewer1").post("/rules", data={}, follow_redirects=False)
    for response in (login_page, forbidden):
        assert response.headers["x-frame-options"] == "DENY"
        assert "default-src 'self'" in response.headers["content-security-policy"]
