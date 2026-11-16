"""Synthetic account/access QA: no PostgreSQL, source HTTP or real credentials."""
from copy import deepcopy
import json
import pytest
from fastapi.testclient import TestClient

from test_readiness_panel import PanelStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.security import hash_password, SessionSigner, verify_password
from universal_supplier.control_plane.accounts import Accounts
from universal_supplier.control_plane.access_policy import application_link, validate_profile, validate_new_password

PASSWORD = "Synthetic-Only!Aa17"
SECRET = "synthetic-release-signing-key-never-deployed-2026"


@pytest.fixture
def setup(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_INSECURE_HTTP", "YES")
    monkeypatch.delenv("CONTENT_STUDIO_AUTHENTICATED_URL", raising=False)
    encoded = hash_password(PASSWORD)
    rows = [
        dict(id=1, username="admin_a", display_name="Admin A", role="ADMIN", password_hash=encoded),
        dict(id=2, username="admin_b", display_name="Admin B", role="ADMIN", password_hash=encoded),
        dict(id=3, username="observer", display_name="Observer", role="VIEWER", password_hash=encoded,
             access_profile="OBSERVER", permissions=["catalog.read"]),
        dict(id=4, username="editor", display_name="Editor", role="OPERATOR", password_hash=encoded,
             access_profile="CONTENT_MANAGER", permissions=["competitor.read","applications.read"]),
        dict(id=5, username="temporary", display_name="Temporary", role="ADMIN", password_hash=encoded,
             must_change_password=True),
    ]
    store = InMemoryAdminStore(rows)
    app = create_app(store=PanelStore("kami"), admin_store=store, auth_required=True, session_secret=SECRET)
    return app, store


def signed(app, username="admin_a"):
    client = TestClient(app)
    response = client.post("/login", data={"username":username, "password":PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    token = SessionSigner(SECRET).read(client.cookies.get(SessionSigner.cookie_name))
    return client, token["csrf"]


def test_two_admins_and_pages(setup):
    app, store = setup
    for username in ("admin_a","admin_b"):
        client, _ = signed(app, username)
        for url in ("/users","/roles","/applications","/account/password"):
            response = client.get(url)
            assert response.status_code == 200
            assert response.headers["cache-control"] == "no-store"
    assert len(store._users) == 5


@pytest.mark.parametrize("username",["admin_a","observer","editor"])
def test_every_role_can_change_only_its_own_password(setup,username):
    app,store=setup
    client,csrf=signed(app,username)
    new="Changed-For-Self!Aa19"
    response=client.post("/account/password",data={
        "csrf_token":csrf,"current_password":PASSWORD,"new_password":new,"confirmation":new,
    },follow_redirects=False)
    assert response.status_code==303
    assert verify_password(new,store.user_by_username(username)[1])
    other="admin_b" if username!="admin_b" else "admin_a"
    assert verify_password(PASSWORD,store.user_by_username(other)[1])


@pytest.mark.parametrize("username,path",[
    ("observer","/users"),("observer","/roles"),("observer","/applications"),
    ("editor","/users"),("editor","/roles"),("editor","/products"),("editor","/settings"),
])
def test_limited_profile_direct_url_denied(setup,username,path):
    client,_ = signed(setup[0],username)
    assert client.get(path).status_code == 403


def test_content_role_can_read_applications(setup):
    client,_ = signed(setup[0],"editor")
    response = client.get("/applications")
    assert response.status_code == 200
    assert "ещё не подключён" in response.text
    assert "target=\"_blank\"" not in response.text


@pytest.mark.parametrize("path",["/users/1/reset-password","/users/1/profile","/roles","/settings/users/1/active"])
def test_vertical_mutation_denied_before_body(setup,path):
    client,csrf = signed(setup[0],"observer")
    assert client.post(path,data={"csrf_token":csrf,"current_password":PASSWORD}).status_code == 403


def test_reset_reauthentication_csrf_and_no_secrets_in_audit(setup):
    app,store = setup
    before = deepcopy(store._users)
    client,csrf = signed(app)
    assert client.post("/users/2/reset-password",data={"current_password":PASSWORD}).status_code == 403
    assert client.post("/users/2/reset-password",data={"csrf_token":csrf,"current_password":"wrong"}).status_code == 403
    assert store._users == before
    response = client.post("/users/2/reset-password",data={"csrf_token":csrf,"current_password":PASSWORD})
    assert response.status_code == 200
    target = store.user_by_username("admin_b")
    assert target[0].must_change_password
    assert not verify_password(PASSWORD,target[1])
    assert store._audit_events[-1]["action"] == "USER_PASSWORD_RESET"
    assert "password_hash" not in json.dumps(store._audit_events)
    assert response.headers["cache-control"] == "no-store"


def test_reset_revokes_other_admin_session(setup):
    app,store = setup
    other,_ = signed(app,"admin_b")
    actor,csrf = signed(app)
    assert actor.post("/users/2/reset-password",data={"csrf_token":csrf,"current_password":PASSWORD}).status_code == 200
    assert other.get("/users",follow_redirects=False).status_code == 303


def test_forced_change_only_own_account_and_old_sessions_revoked(setup):
    app,store = setup
    client,csrf = signed(app,"temporary")
    assert client.get("/users",follow_redirects=False).headers["location"] == "/account/password"
    assert client.get("/account/password").status_code == 200
    old_cookie = client.cookies.get(SessionSigner.cookie_name)
    new = "Different-Synthetic!Aa18"
    response = client.post("/account/password",data={
        "csrf_token":csrf,"current_password":PASSWORD,"new_password":new,"confirmation":new,"user_id":"1",
    },follow_redirects=False)
    assert response.status_code == 303
    assert not store.user_by_id(5).must_change_password
    assert verify_password(new,store.user_by_username("temporary")[1])
    assert verify_password(PASSWORD,store.user_by_username("admin_a")[1])
    client.cookies.set(SessionSigner.cookie_name,old_cookie)
    assert client.get("/users",follow_redirects=False).status_code == 303


def test_create_configure_assign_role_and_revoke_cookie(setup):
    app,store = setup
    client,csrf = signed(app)
    response = client.post("/roles",data={
        "csrf_token":csrf,"current_password":PASSWORD,"code":"CUSTOM_VIEW","name":"Read apps",
        "permission:applications.read":"on",
    })
    assert response.status_code == 200
    # Use existing user assignment; it invalidates pre-existing sessions.
    observer,_ = signed(app,"observer")
    response = client.post("/users/3/profile",data={
        "csrf_token":csrf,"current_password":PASSWORD,"profile":"CUSTOM_VIEW",
    })
    assert response.status_code == 200
    assert observer.get("/applications",follow_redirects=False).status_code == 303
    observer,_ = signed(app,"observer")
    assert observer.get("/applications").status_code == 200
    assert observer.get("/users").status_code == 403
    response = client.post("/users",data={
        "csrf_token":csrf,"current_password":PASSWORD,"username":"new_observer","display_name":"New observer",
        "profile":"OBSERVER","new_password":PASSWORD,"confirmation":PASSWORD,
    })
    assert response.status_code == 200
    assert store.user_by_username("new_observer")[0].must_change_password


def test_last_admin_cannot_be_removed_or_demoted(setup):
    app,store = setup
    store._users = [store._users[0]]
    with pytest.raises(ValueError):
        store.set_user_active(1,False,1)
    actor = store.user_by_id(1)
    with pytest.raises(ValueError):
        Accounts(store).change_profile(1,"OBSERVER",actor,store.user_by_username("admin_a")[1])
    assert store.user_by_id(1).role == "ADMIN"


def test_stale_reauthentication_cannot_mutate_after_password_change(setup):
    _,store = setup
    actor,old_hash = store.user_by_username("admin_a")
    service = Accounts(store)
    service.set_password(1,"Different-Only!Aa18",actor,old_hash,temporary=False)
    with pytest.raises(PermissionError):
        service.set_password(2,PASSWORD,actor,old_hash,temporary=True)


@pytest.mark.parametrize("value",["http://example.com","https://u:p@example.com","javascript:alert(1)","//example.com",""])
def test_app_link_has_no_invented_or_unsafe_redirect(value):
    assert application_link(value) == ""


@pytest.mark.parametrize("value",["short","a"*14,"A"*20])
def test_password_strength(value):
    with pytest.raises(ValueError):
        validate_new_password(value,value)


def test_profile_cannot_escalate_to_admin_or_unknown_permission():
    for permissions in (["*"],["accounts.admin"],["unknown"]):
        with pytest.raises(ValueError):
            validate_profile("CUSTOM","Custom",permissions)
