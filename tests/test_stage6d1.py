from __future__ import annotations

from collections import Counter
import csv
import json
from pathlib import Path

from fastapi.testclient import TestClient

from universal_supplier.control_plane.admin_store import InMemoryAdminStore, explanation_for, identity_snapshot
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.scheduler import controls_to_schedule, schedule_to_controls
from universal_supplier.control_plane.security import hash_password
from universal_supplier.control_plane.store import SnapshotControlPlaneStore
from universal_supplier.control_plane.web import create_app
from universal_supplier.identity_service import IDENTITY_DECISIONS, classify_identity_card


ROOT = Path(__file__).resolve().parents[1]
SECRET = "stage6d1-test-session-secret-long-enough"
PASSWORD = "corrective-test-password"


def admin_store():
    return InMemoryAdminStore([{"id":1,"username":"admin","display_name":"Администратор","role":"ADMIN","is_active":True,"password_hash":hash_password(PASSWORD)}])


def test_packaged_stage6b_parity_exact_counts():
    with (ROOT / "reports/OPTIMUM_IDENTITY_AUDIT.csv").open(encoding="utf-8-sig", newline="") as handle:
        optimum = list(csv.DictReader(handle))
    with (ROOT / "reports/PARTNER_ST_IDENTITY_REGRESSION.csv").open(encoding="utf-8-sig", newline="") as handle:
        partner = list(csv.DictReader(handle))
    assert len(partner) == 1222
    counts = Counter(row["decision"] for row in optimum)
    assert {key: counts[key] for key in IDENTITY_DECISIONS} == {
        "EXACT_EXISTING":0, "HIGH_CONFIDENCE_EXISTING":0, "REVIEW_EXISTING":69,
        "CONFLICT":73, "SAFE_NEW_PRODUCT_CANDIDATE":257,
        "ACCESSORY_OR_COMPATIBILITY_ITEM":932, "INSUFFICIENT_IDENTITY":20,
    }


def test_report_generator_delegates_to_runtime_classifier():
    source = (ROOT / "scripts/generate_stage6b_reports.py").read_text(encoding="utf-8")
    assert "return classify_identity_card(card, match_row, registry, by_model, by_brand_model)" in source


def test_classifier_materializes_all_seven_classes_contract():
    assert IDENTITY_DECISIONS == ("EXACT_EXISTING","HIGH_CONFIDENCE_EXISTING","REVIEW_EXISTING","CONFLICT","SAFE_NEW_PRODUCT_CANDIDATE","ACCESSORY_OR_COMPATIBILITY_ITEM","INSUFFICIENT_IDENTITY")


def test_real_english_reason_is_not_primary_operator_explanation():
    value = explanation_for({"decision":"REVIEW_EXISTING","decision_reason":"plausible existing candidate(s); identity proof insufficient","identity_characteristics":{},"conflicts":{}})
    assert "plausible" not in value["summary"] and "Найден похожий" in value["summary"]
    assert value["technical_reason"].startswith("plausible")


def test_identity_snapshot_excludes_commercial_and_content_fields():
    row = {"supplier":"optimum","external_id":"1","sku":"A","brand_raw":"Optimum","own_model":"M1","reference_model":"","product_kind":"machine","price":1,"availability":"in_stock","quantity":2,"description":"x","identity_characteristics":{"execution":{"base_model":"m1"},"identity_critical_properties":{"voltage":"220 V"}}}
    snapshot = identity_snapshot(row)
    assert not {"price","availability","quantity","description"} & snapshot.keys()
    assert snapshot["execution"]["base_model"] == "m1"


def test_daily_and_weekday_scheduler_roundtrip_without_manual_cron():
    daily = controls_to_schedule("daily", "02:00", [], "")
    weekdays = controls_to_schedule("weekdays", "06:30", ["1","3","5"], "")
    assert daily == "0 2 * * *" and schedule_to_controls(daily)["mode"] == "daily"
    assert weekdays == "30 6 * * 1,3,5" and schedule_to_controls(weekdays)["weekdays"] == ["1","3","5"]


def test_custom_scheduler_roundtrip_never_changes_expression():
    expression = "7 */5 1-20 * 1,4"
    assert controls_to_schedule("custom", "02:00", [], expression) == expression
    assert schedule_to_controls(expression)["custom_cron"] == expression


def test_default_host_policy_blocks_untrusted_host():
    app = create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), queue=InMemoryJobQueue(), admin_store=admin_store(), session_secret=SECRET, auth_required=True)
    assert TestClient(app, base_url="http://untrusted.example").get("/login").status_code == 400


def test_configured_future_host_login_and_csrf_mutation(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_HOSTS", "127.0.0.1,localhost,testserver,admin.example.internal")
    store = admin_store(); app = create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), queue=InMemoryJobQueue(), admin_store=store, session_secret=SECRET, auth_required=True)
    client = TestClient(app, base_url="http://admin.example.internal")
    response = client.post("/login", data={"username":"admin","password":PASSWORD}, follow_redirects=False)
    assert response.status_code == 303
    from universal_supplier.control_plane.security import SessionSigner
    csrf = SessionSigner(SECRET).read(client.cookies.get(SessionSigner.cookie_name))["csrf"]
    assert client.post("/settings/users", data={"csrf_token":csrf,"username":"viewer1","display_name":"Наблюдатель","password":"viewer-password-123","role":"VIEWER"}, follow_redirects=False).status_code == 303


def test_configured_host_does_not_weaken_csrf(monkeypatch):
    monkeypatch.setenv("CONTROL_PLANE_ALLOWED_HOSTS", "admin.example.internal")
    app = create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), queue=InMemoryJobQueue(), admin_store=admin_store(), session_secret=SECRET, auth_required=True)
    client = TestClient(app, base_url="http://admin.example.internal")
    client.post("/login", data={"username":"admin","password":PASSWORD})
    assert client.post("/settings/users", data={"username":"xuser","display_name":"X","password":"long-password-123","role":"VIEWER"}).status_code == 403


def test_candidate_template_uses_exact_proposed_candidate_and_meaningful_empty_state():
    template = (ROOT / "universal_supplier/control_plane/templates/review_case.html").read_text(encoding="utf-8")
    assert "item.proposed_candidate" in template and "item.candidates[0]" not in template
    assert "Похожих существующих товаров не найдено." in template


def test_supplier_detail_uses_confirmation_dialog_and_queue_wording():
    template = (ROOT / "universal_supplier/control_plane/templates/supplier_detail.html").read_text(encoding="utf-8")
    assert "<dialog" in template and "Кнопка только поставит задание в очередь" in template


def test_basic_scheduler_page_has_no_visible_cron_requirement():
    client = TestClient(create_app(store=SnapshotControlPlaneStore(ROOT/"reports"), admin_store=admin_store(), session_secret=SECRET, auth_required=False))
    text = client.get("/scheduler").text
    assert "Ежедневно" in text and "По дням недели" in text
    assert "Пользовательское cron-выражение" not in text


def test_stage6d1_did_not_modify_migrations_001_013():
    assert all(list((ROOT/"migrations").glob(f"{number:03d}_*.sql")) for number in range(1,14))


def test_identity_characteristics_store_structured_candidate_evidence():
    card = {"supplier_code":"optimum","external_id":"1","sku":"A","name":"Станок Optimum M1","brand":"Optimum","categories":[],"properties":[]}
    registry = {"10":{"sterbrust_product_id":"10","name":"Станок Optimum M1","brand_raw":"Optimum","model_raw":"M1","category_path":"Станки","properties":[]}}
    row = classify_identity_card(card,{"sterbrust_product_id":"10","match_method":"BRAND_MODEL","status":"REVIEW","auto_accepted":"FALSE","evidence":"{}"},registry)
    evidence = json.loads(row["identity_characteristics"])
    assert evidence["candidate_sterbrust_ids"] == ["10"] and evidence["candidate_evidence"][0]["source"] == "matcher"
