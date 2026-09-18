from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from universal_supplier.control_plane.admin_store import InMemoryAdminStore, canonical_fingerprint, explanation_for, priority_for
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.security import AuthUser, SessionSigner, hash_password, normalize_username, role_allows, verify_password
from universal_supplier.control_plane.store import SnapshotControlPlaneStore
from universal_supplier.control_plane.web import create_app

ROOT = Path(__file__).resolve().parents[1]
SECRET = "stage6d-qa-secret-value-at-least-32-bytes"
PASSWORD = "Correct-Horse-2026"
PASSWORD_HASH = hash_password(PASSWORD, salt=b"0123456789abcdef")


def user(user_id=1, role="ADMIN", active=True):
    return {"id": user_id, "username": f"user{user_id}", "display_name": f"Пользователь {user_id}", "role": role, "is_active": active, "password_hash": PASSWORD_HASH}


def case(case_id=1):
    return {"id": case_id, "source_product_id": 10, "supplier_name": "Optimum", "supplier_code": "optimum",
        "external_id": "QA-1", "name": "Станок QA", "brand_raw": "Optimum", "sku": "QA-1", "own_model": "QA-1",
        "reference_model": None, "product_kind": "machine", "automatic_decision": "REVIEW_EXISTING",
        "lifecycle_status": "OPEN", "priority_rank": 1, "priority_reason": "Высокая вероятность существующего товара",
        "explanation": {"summary": "Совпала модель", "positive": ["Модель QA-1"]}, "source_identity_fingerprint": "a"*64,
        "source_identity_snapshot": {"external_id": "QA-1"}, "candidates": [{"sterbrust_product_id":"500","name":"Станок QA","brand_raw":"Optimum","model_raw":"QA-1","category_path":"Станки"}]}


def client_for(role="ADMIN", cases=None):
    admin = InMemoryAdminStore([user(role=role)], cases or [case()]); queue = InMemoryJobQueue()
    app = create_app(store=SnapshotControlPlaneStore(ROOT / "reports"), queue=queue, admin_store=admin,
                     session_secret=SECRET, auth_required=True)
    client = TestClient(app)
    response = client.post("/login", data={"username":"user1","password":PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    token = client.cookies.get(SessionSigner.cookie_name); payload = SessionSigner(SECRET).read(token)
    return client, admin, queue, payload["csrf"]


def test_stage6d_migration_is_013_and_prior_hashes_are_immutable():
    expected = json.loads((ROOT / "stage6c1_migration_baseline_sha256.json").read_text(encoding="utf-8"))["files"]
    actual = {name: hashlib.sha256((ROOT / "migrations" / name).read_bytes()).hexdigest() for name in expected}
    assert actual == expected
    assert [p.name for p in sorted((ROOT / "migrations").glob("013_*.sql"))] == ["013_operator_admin_review.sql"]


@pytest.mark.parametrize("token", ["CREATE TABLE app_users", "CREATE TABLE review_cases", "CREATE TABLE review_decisions",
    "CREATE TABLE candidate_rejections", "CREATE TABLE audit_events", "prevent_audit_mutation", "requested_by_user_id"])
def test_migration_contains_structured_security_review_and_audit_objects(token):
    assert token in (ROOT / "migrations/013_operator_admin_review.sql").read_text(encoding="utf-8")


def test_password_hashing_is_scrypt_salted_and_verifiable():
    first = hash_password(PASSWORD); second = hash_password(PASSWORD)
    assert first.startswith("scrypt$") and first != second
    assert verify_password(PASSWORD, first) and not verify_password("wrong-password-value", first)
    assert PASSWORD not in first


@pytest.mark.parametrize("bad", ["ab", "space user", "x/../../"])
def test_username_validation_fails_closed(bad):
    with pytest.raises(ValueError): normalize_username(bad)


def test_username_is_normalized_before_storage():
    assert normalize_username("User.Name") == "user.name"


def test_signed_session_rejects_tampering_and_exposes_csrf_only_inside_signature():
    signer = SessionSigner(SECRET); token, csrf = signer.issue(AuthUser(1,"admin","Администратор","ADMIN"))
    assert signer.read(token)["csrf"] == csrf
    assert signer.read(token[:-1] + ("A" if token[-1] != "A" else "B")) is None
    assert signer.csrf_matches(signer.read(token), csrf)
    assert not signer.csrf_matches(signer.read(token), "wrong")


@pytest.mark.parametrize(("role","allowed","expected"), [("ADMIN",{"ADMIN"},True),("OPERATOR",{"ADMIN"},False),("VIEWER",{"ADMIN","OPERATOR"},False)])
def test_role_matrix(role, allowed, expected):
    assert role_allows(role, allowed) is expected


def test_anonymous_pages_redirect_to_login_but_health_stays_public():
    admin = InMemoryAdminStore([user()]); app = create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), admin_store=admin, session_secret=SECRET, auth_required=True)
    client = TestClient(app)
    assert client.get("/", follow_redirects=False).headers["location"].startswith("/login")
    assert client.get("/health").status_code == 200


def test_empty_user_table_shows_safe_first_admin_instruction():
    app = create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), admin_store=InMemoryAdminStore(), session_secret=SECRET, auth_required=True)
    page = TestClient(app).get("/login")
    assert page.status_code == 200 and "python scripts/create_admin.py" in page.text


def test_login_failure_success_cookie_flags_and_logout():
    admin = InMemoryAdminStore([user()]); client = TestClient(create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), admin_store=admin, session_secret=SECRET, auth_required=True))
    assert client.post("/login", data={"username":"user1","password":"bad"}).status_code == 401
    response = client.post("/login", data={"username":"user1","password":PASSWORD}, follow_redirects=False)
    cookie = response.headers["set-cookie"].lower()
    assert response.status_code == 303 and "httponly" in cookie and "samesite=lax" in cookie
    csrf = SessionSigner(SECRET).read(client.cookies.get(SessionSigner.cookie_name))["csrf"]
    assert client.post("/logout", data={"csrf_token":csrf}, follow_redirects=False).status_code == 303


def test_session_cookie_becomes_secure_when_https_is_enabled(monkeypatch):
    monkeypatch.setenv("HTTPS_ENABLED", "YES")
    admin = InMemoryAdminStore([user()]); client = TestClient(create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), admin_store=admin, session_secret=SECRET, auth_required=True))
    response = client.post("/login", data={"username":"user1","password":PASSWORD}, follow_redirects=False)
    assert "secure" in response.headers["set-cookie"].lower()


@pytest.mark.parametrize("path", ["/", "/products?page_size=25", "/review", "/suppliers", "/runs", "/scheduler", "/settings"])
def test_authenticated_main_pages_are_available(path):
    client, _, _, _ = client_for(); assert client.get(path).status_code == 200


def test_russian_navigation_and_no_qa_suppliers_in_working_ui():
    client, _, _, _ = client_for(); text = client.get("/").text
    for label in ("Главная","Товары","Требует проверки","Поставщики","Запуски","Планировщик","Настройки"):
        assert label in text
    assert "Supplier A" not in text and "Supplier B" not in text and "Supplier C" not in text


def test_csrf_required_for_mutation_and_viewer_is_read_only():
    client, _, queue, _ = client_for(role="VIEWER")
    assert client.post("/suppliers/1/start", data={"crawl_type":"full"}).status_code == 403
    assert queue.jobs == []


def test_operator_manual_full_crawl_is_queue_only_with_user_provenance():
    client, admin, queue, csrf = client_for(role="OPERATOR")
    response = client.post("/suppliers/1/start", data={"crawl_type":"full","csrf_token":csrf}, follow_redirects=False)
    assert response.status_code == 303 and queue.jobs[0]["status"] == "queued" and queue.jobs[0]["requested_by_user_id"] == 1
    assert client.app.state.direct_crawl_executions == 0
    assert admin.audit_events()[0]["action"] == "MANUAL_CRAWL_ENQUEUED"


def test_manual_and_scheduled_collision_creates_skipped_provenance_job():
    client, _, queue, csrf = client_for(role="OPERATOR")
    queue.enqueue(1, trigger_type="schedule", crawl_type="full")
    client.post("/suppliers/1/start", data={"crawl_type":"full","csrf_token":csrf})
    assert len(queue.jobs) == 2 and queue.jobs[1]["status"] == "skipped_already_queued"


def test_unsupported_manual_crawl_type_is_rejected_before_queue():
    client, _, queue, csrf = client_for(role="OPERATOR")
    assert client.post("/suppliers/1/start", data={"crawl_type":"fast","csrf_token":csrf}).status_code == 400
    assert queue.jobs == []


@pytest.mark.parametrize(("decision","rank"), [("REVIEW_EXISTING",1),("ACCESSORY_OR_COMPATIBILITY_ITEM",2),("CONFLICT",3),("INSUFFICIENT_IDENTITY",4)])
def test_review_priority_is_deterministic_and_explainable(decision, rank):
    value, reason = priority_for(decision); assert value == rank and reason


def test_identity_fingerprint_is_order_independent_and_material_change_sensitive():
    assert canonical_fingerprint({"a":1,"b":2}) == canonical_fingerprint({"b":2,"a":1})
    assert canonical_fingerprint({"a":1}) != canonical_fingerprint({"a":2})


def test_deterministic_explanation_uses_existing_evidence_only():
    result = explanation_for({"own_model":"M1","reference_model":"R1","brand_raw":"B","decision_reason":"Причина","identity_characteristics":{"power":"1"},"conflicts":{},"rule_version":"v1"})
    assert result["summary"] == "Причина" and len(result["positive"]) == 3 and result["identity_evidence"]["power"] == "1"


def test_review_page_has_comparison_explanation_history_and_all_actions():
    client, _, _, _ = client_for(); text = client.get("/review/1").text
    for label in ("Товар поставщика","Предложенный товар Sterbrust","Почему система просит проверку","История решений","Это новый товар","Это аксессуар / оснастка","Отложить"):
        assert label in text


@pytest.mark.parametrize(("decision","comment","expected"), [("MANUAL_CONFIRMED_NEW","",400),("MANUAL_CONFIRMED_NEW","Новый",303),("MANUAL_ACCESSORY","",400),("MANUAL_ACCESSORY","Оснастка",303),("POSTPONED","",303)])
def test_comment_policy_and_review_decisions(decision, comment, expected):
    client, _, _, csrf = client_for(); response = client.post("/review/1/decision", data={"csrf_token":csrf,"decision_class":decision,"comment":comment}, follow_redirects=False)
    assert response.status_code == expected


def test_select_other_requires_comment_and_rejection_requires_comment():
    client, _, _, csrf = client_for()
    assert client.post("/review/1/decision", data={"csrf_token":csrf,"decision_class":"MANUAL_CONFIRMED","selection_mode":"other","sterbrust_product_id":"500"}).status_code == 400
    assert client.post("/review/1/reject", data={"csrf_token":csrf,"sterbrust_product_id":"500","comment":""}).status_code == 400
    assert client.post("/review/1/reject", data={"csrf_token":csrf,"sterbrust_product_id":"500","comment":"Не та модель"}, follow_redirects=False).status_code == 303


def test_bulk_existing_and_new_are_visibly_disabled():
    client, _, _, _ = client_for(); text = client.get("/review").text
    assert "Подтвердить совпадения — недоступно" in text and "Новые товары — недоступно" in text
    assert text.count("disabled") >= 2


@pytest.mark.parametrize("action", ["postpone", "mark_reviewed", "assign"])
def test_only_safe_bulk_actions_are_exposed(action):
    client, _, _, _ = client_for(); text = client.get("/review").text
    assert f'value="{action}"' in text


def test_unsafe_bulk_action_fails_closed():
    client, admin, _, csrf = client_for()
    response = client.post("/review/bulk", data={"csrf_token":csrf,"case_1":"1","bulk_action":"confirm_existing"})
    assert response.status_code == 400


def test_scheduler_has_basic_and_advanced_russian_help_and_only_full_type():
    client, _, _, _ = client_for(); basic = client.get("/scheduler").text; advanced = client.get("/scheduler?advanced=1").text
    assert "Планировщик" in basic and "Расширенные настройки" in basic
    assert '<option value="full">Полный сбор</option>' in advanced
    for forbidden in ('value="fast"','value="discovery"','value="identity_validation"'): assert forbidden not in advanced


def test_first_admin_script_never_logs_password_and_package_has_no_write_routes():
    script = (ROOT/"scripts/create_admin.py").read_text(encoding="utf-8")
    assert "getpass.getpass" in script and "print(password" not in script
    routes = [route.path.lower() for route in client_for()[0].app.routes]
    assert not any(x in route for route in routes for x in ("sterbrust/write","esol","product/create"))


def test_audit_memory_store_is_append_only_by_public_api_shape():
    store = InMemoryAdminStore([user()]); store.audit(1,"ACTION","thing",7,None,{"x":1})
    assert store.audit_events()[0]["after_state"] == {"x":1}
    assert not hasattr(store, "update_audit") and not hasattr(store, "delete_audit")
