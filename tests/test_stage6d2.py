from __future__ import annotations

from contextlib import nullcontext
from html.parser import HTMLParser
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from universal_supplier.control_plane.adapter_registry import _finish_metadata
from universal_supplier.control_plane.admin_store import InMemoryAdminStore
from universal_supplier.control_plane.queue import InMemoryJobQueue
from universal_supplier.control_plane.web import (
    PRODUCT_KIND_LABELS, create_app, product_kind_label, relation_type_label,
)

ROOT = Path(__file__).resolve().parents[1]
DECISIONS = (
    "EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING", "REVIEW_EXISTING", "CONFLICT",
    "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY_OR_COMPATIBILITY_ITEM", "INSUFFICIENT_IDENTITY",
)
KINDS = (
    "accessory", "beveling_machine", "chuck", "collet", "controller", "disk",
    "drill_press", "fixture", "grinder", "handle", "jaw", "lathe", "machine",
    "manipulator", "milling_machine", "stand", "table", "tool", "unknown", "vise",
)


class VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(); self.parts = []; self.hidden = 0
    def handle_starttag(self, tag, attrs):
        if tag == "details" and any(name == "data-technical" for name, _ in attrs): self.hidden += 1
    def handle_endtag(self, tag):
        if tag == "details" and self.hidden: self.hidden -= 1
    def handle_data(self, data):
        if not self.hidden: self.parts.append(data)


def visible_text(html: str) -> str:
    parser = VisibleText(); parser.feed(html); return " ".join(parser.parts)


class RenderStore:
    def __init__(self):
        self.rows = [dict(id=i + 1, external_id=str(i + 1), sku=f"SKU-{i+1}", name=f"Товар {i+1}",
            brand_raw="Optimum", supplier_code="optimum", supplier_name="Optimum", decision=decision,
            product_kind=KINDS[i], own_model="M1", reference_model="", best_sterbrust_id="500",
            decision_reason="technical", category="Оборудование", availability_normalized="in_stock",
            price=1, quantity=1, image_count=0) for i, decision in enumerate(DECISIONS)]
        self.supplier = dict(id=1, code="optimum", name="Optimum", enabled=True, base_url="https://example.invalid",
            rate_settings={"min_request_interval_seconds":.2,"max_concurrency":1,"request_timeout_seconds":30},
            products=7, errors_30d=1, last_status="partial", last_crawl="2026-09-15", last_duration_seconds=12,
            last_discovered_count=7, last_new_count=2, last_changed_count=3, last_error_count=1,
            job_status=None, next_runs=[])
    def health(self): return {"application":"ok","postgresql":"demo_snapshot"}
    def dashboard(self): return [dict(self.supplier)]
    def suppliers(self): return [dict(self.supplier)]
    def filter_options(self): return {"suppliers":[{"code":"optimum","name":"Optimum"}],"brands":["Optimum"],"decisions":DECISIONS,"kinds":KINDS}
    def products(self, filters): return {"items":self.rows,"total":len(self.rows),"page":1,"page_size":50,"pages":1}
    def product(self, supplier_code, external_id):
        row=dict(self.rows[2]); row.update(properties=[],media=[],description_text="",raw_data={},relations=[{"relation_type":x,"to_source_product_id":2} for x in ("accessory","related","compatible","replacement","navigation","parent_reference","future")]); return row
    def runs(self): return [{"id":1,"supplier_name":"Optimum","trigger_type":"manual","status":"partial","started_at":"2026-09-15","discovered_count":7,"changed_count":3,"error_count":1}]
    def run(self, run_id): return None


def admin_cases():
    return [dict(id=i+1, source_product_id=i+1, external_id=str(i+1), supplier_name="Optimum", supplier_code="optimum",
        name=f"Проверка {i+1}", brand_raw="Optimum", sku=f"S{i+1}", priority_rank=2, priority_reason="Требуется проверка",
        automatic_decision=decision, lifecycle_status="OPEN", assigned_to_user_id=None, assignee_name=None,
        proposed_sterbrust_id="500", own_model="M1", reference_model="", product_kind=KINDS[i],
        explanation={"summary":"Нужно проверить признаки.","positive":[],"technical_reason":decision,"identity_evidence":{}},
        candidates=[{"sterbrust_product_id":"500","name":"Станок","brand_raw":"Optimum","model_raw":"M1","category_path":"Оборудование","candidate_source":"matcher"}], comparison=[])
        for i, decision in enumerate(DECISIONS)]


def app_client(cases=None):
    admin = InMemoryAdminStore(cases=cases if cases is not None else admin_cases())
    app = create_app(store=RenderStore(), queue=InMemoryJobQueue(), admin_store=admin, auth_required=False)
    return TestClient(app), admin


class Cursor:
    def __init__(self): self.calls=[]
    def __enter__(self): return self
    def __exit__(self,*_): pass
    def execute(self, sql, params): self.calls.append((sql,params))
class Connection:
    def __init__(self): self.cursor_obj=Cursor()
    def transaction(self): return nullcontext()
    def cursor(self): return self.cursor_obj


def test_post_processing_failure_forces_partial_and_recounts_errors():
    connection=Connection(); repo=SimpleNamespace(connection=connection)
    _finish_metadata(repo, 9, {"identity_post_processing":{"status":"POST_PROCESSING_FAILED"}})
    sql, params=connection.cursor_obj.calls[0]
    assert "status=CASE WHEN %s THEN 'partial'" in sql and "count(*) FROM crawl_errors" in sql
    assert params[0] is True and params[1] == params[3] == 9


def test_successful_post_processing_does_not_force_partial():
    connection=Connection(); _finish_metadata(SimpleNamespace(connection=connection), 10, {"identity_post_processing":{"status":"succeeded"}})
    assert connection.cursor_obj.calls[0][1][0] is False


def test_complete_product_kind_and_relation_labels():
    assert set(KINDS) <= set(PRODUCT_KIND_LABELS)
    assert all(product_kind_label(value) not in {value,"Другой тип"} for value in KINDS if value != "unknown")
    assert product_kind_label("future_kind") == "Другой тип"
    assert relation_type_label("accessory") == "Аксессуар" and relation_type_label("future") == "Другая связь"


def test_rendered_normal_pages_have_no_raw_primary_codes_and_are_russian():
    client,_=app_client()
    paths=("/","/products","/products/optimum/1","/review","/review/1","/suppliers","/runs","/scheduler","/settings")
    forbidden=set(DECISIONS)|set(KINDS)|{"accessory","related","compatible","replacement","navigation","parent_reference","Universal Supplier","Sterbrust Product ID"}
    for path in paths:
        response=client.get(path); assert response.status_code == 200, path
        text=visible_text(response.text)
        assert not [code for code in forbidden if code in text], (path,text)
        assert "Универсальный поставщик" in text
    assert "<title>Универсальный поставщик</title>" in client.get("/").text


def test_product_filters_keep_codes_as_values_but_show_russian_text():
    html=app_client()[0].get("/products").text
    assert 'value="CONFLICT"' in html and '>Противоречивые признаки<' in html
    assert 'value="jaw"' in html and '>Губки<' in html


def test_product_detail_allows_raw_codes_only_in_marked_technical_block():
    html=app_client()[0].get("/products/optimum/1").text
    assert "Проверить существующий товар" in visible_text(html)
    assert "REVIEW_EXISTING" in html and "product_kind" not in visible_text(html)
    assert "ID товара Sterbrust" in visible_text(html)


def test_direct_proposed_candidate_rejection_requires_comment_and_disappears():
    client,admin=app_client()
    assert client.post("/review/1/reject",data={"csrf_token":"test-csrf","sterbrust_product_id":"500","comment":""}).status_code == 400
    assert client.post("/review/1/reject",data={"csrf_token":"test-csrf","sterbrust_product_id":"500","comment":"Неверный кандидат"},follow_redirects=False).status_code == 303
    assert admin.review_case(1)["proposed_candidate"] is None


def test_next_review_includes_assigned_and_reopened_but_never_resolved():
    cases=admin_cases()[:4]
    cases[1]["lifecycle_status"]="ASSIGNED"; cases[2]["lifecycle_status"]="REOPENED_SOURCE_CHANGED"; cases[3]["lifecycle_status"]="RESOLVED"
    client,_=app_client(cases)
    response=client.post("/review/1/decision",data={"csrf_token":"test-csrf","decision_class":"MANUAL_CONFIRMED_NEW","comment":"Новый"},follow_redirects=False)
    assert response.headers["location"].startswith("/review/2")


def test_supplier_cards_show_truthful_run_fields():
    text=visible_text(app_client()[0].get("/").text)
    for label in ("Последний сбор","Статус","Длительность","Найдено","Новых","Изменено","Ошибок","Следующий запуск","Очередь / выполняется"):
        assert label in text


def test_stage6d1_runtime_script_imports_os_and_requires_empty_safe_password_env():
    source=(ROOT/"scripts/qa_stage6d1_runtime.py").read_text(encoding="utf-8")
    assert "import os" in source and "STAGE6D1_QA_ADMIN_PASSWORD" in source


def test_product_detail_relation_query_uses_actual_schema_column():
    source=(ROOT/"universal_supplier/control_plane/store.py").read_text(encoding="utf-8")
    assert "to_source_product_id,raw_metadata FROM source_product_relations" in source
    assert "to_source_product_id,raw_data FROM source_product_relations" not in source


def test_migrations_001_013_remain_present_after_later_stage():
    assert all(list((ROOT/"migrations").glob(f"{number:03d}_*.sql")) for number in range(1,14))
