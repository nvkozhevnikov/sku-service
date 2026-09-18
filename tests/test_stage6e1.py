from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from universal_supplier.models import ProductCard
from universal_supplier.stage6e import (
    BusinessRuleResolver, InMemoryStage6EService, build_change_preview, csv_chunks,
    validate_fetch_targets,
)

ROOT=Path(__file__).resolve().parents[1]


def old_card_payload():
    return {"supplier_code":"partner_st","external_id":"1","sku":"A","name":"Item",
            "requested_url":"https://partner-st.ru/a","final_url":"https://partner-st.ru/a",
            "canonical_url":"https://partner-st.ru/a","http_status":200,"redirect_urls":[],
            "price":"10","old_price":None,"currency":"RUB","availability_raw":"yes",
            "availability_normalized":"in_stock","quantity":None,"description_text":"d",
            "description_html":"<p>d</p>","raw_data":{}}


def test_old_packaged_card_deserializes_with_safe_price_defaults():
    card=ProductCard.from_jsonable(old_card_payload())
    assert (card.price_type,card.price_raw,card.price_source)==("unknown","","")


def test_business_rule_resolver_excludes_and_forces_review_centrally():
    rules=[{"id":1,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"DO_NOT_IMPORT","active":True},
           {"id":2,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"FORCE_REVIEW","active":True}]
    result=BusinessRuleResolver(rules).resolve(supplier_id=7,source_product_id=9,external_id="A")
    assert result.excluded and result.force_review and result.matched_rule_ids==(1,2)


def test_business_rule_resolver_preserves_raw_and_returns_effective_overrides():
    rules=[{"id":1,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"BRAND_OVERRIDE","config":{"value":"Approved"}},
           {"id":2,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"CATEGORY_OVERRIDE","config":{"value":"Machines"}}]
    result=BusinessRuleResolver(rules).resolve(supplier_id=7,source_product_id=9,external_id="A")
    assert (result.brand_override,result.category_override)==("Approved","Machines")


def test_do_not_use_supplier_field_wins_over_supplier_allowed_policy():
    rows=build_change_preview({"price":1},{"price":2},{"price":"SUPPLIER_ALLOWED"},forbidden_fields={"price"})
    assert rows[0]["decision"]=="EXCLUDED_BY_RULE"


def test_identity_block_is_distinct_from_anomaly_and_rule_block():
    rows=build_change_preview({"price":1},{"price":2},{"price":"SUPPLIER_ALLOWED"},identity_blocked=True)
    assert rows[0]["decision"]=="IDENTITY_BLOCKED"


def test_csv_chunks_streams_bom_once_and_rows_incrementally():
    chunks=list(csv_chunks([{"supplier_name":"A","external_id":"1"},{"supplier_name":"B","external_id":"2"}]))
    assert len(chunks)==3 and chunks[0].startswith(b"\xef\xbb\xbf")
    assert not chunks[1].startswith(b"\xef\xbb\xbf")


def test_redirect_guard_rejects_forbidden_final_host_without_fetching_again():
    fetch=SimpleNamespace(redirects=("https://partner-st.ru/a",),final_url="http://127.0.0.1/private")
    with pytest.raises(ValueError): validate_fetch_targets(fetch,["partner-st.ru"])


def test_property_dropdown_preselects_proposed_candidate_and_has_placeholder():
    template=(ROOT/"universal_supplier/control_plane/templates/properties.html").read_text(encoding="utf-8")
    assert "p.id==m.canonical_property_id" in template and "Выберите свойство" in template


def test_product_detail_links_to_true_reparse_not_only_inspector():
    template=(ROOT/"universal_supplier/control_plane/templates/product_detail.html").read_text(encoding="utf-8")
    assert "/reparse" in template and "Проверить товар заново" in template


def test_reparse_ui_states_zero_discovery_and_full_crawl_calls():
    template=(ROOT/"universal_supplier/control_plane/templates/reparse.html").read_text(encoding="utf-8")
    assert "Discovery calls = 0" in template and "Full crawl calls = 0" in template


def test_runtime_adapter_jobs_pass_automatic_schema_signals_into_ingest():
    source=(ROOT/"universal_supplier/control_plane/adapter_registry.py").read_text(encoding="utf-8")
    assert source.count("schema_signals=_schema_signals") == 2


def test_preview_has_no_silent_limit_and_scans_in_chunks():
    source=(ROOT/"universal_supplier/stage6e.py").read_text(encoding="utf-8")
    preview=source[source.index("def preview(self, page: int = 1",source.index("class PostgresStage6EService")):]
    assert "LIMIT 1000" not in preview and "fetchmany(500)" in preview


def test_normal_ingest_writes_quality_provenance_and_property_reconciliation():
    source=(ROOT/"universal_supplier/postgres.py").read_text(encoding="utf-8")
    for call in ("_record_field_observations", "_record_quality_snapshot", "_reconcile_supplier_properties"):
        assert f"self.{call}" in source


def test_migrations_001_014_remain_the_only_migrations():
    assert [p.name for p in sorted((ROOT/"migrations").glob("[0-9][0-9][0-9]_*.sql"))][-1].startswith("014_")


def test_price_provenance_never_guesses_price_type():
    card=ProductCard.from_jsonable(old_card_payload())
    from universal_supplier.postgres import PostgresRepository
    assert PostgresRepository._price_provenance(card)==("unknown","10","adapter ProductCard.price")


def test_reparse_is_one_fetch_without_discovery_full_crawl_or_persist():
    service=InMemoryStage6EService()
    payload=old_card_payload()
    fetched=SimpleNamespace(status_code=200,redirects=(),final_url=payload["final_url"])
    calls=[]
    result=service.reparse_preview(payload,url=payload["requested_url"],allowed_hosts=("partner-st.ru",),
        fetcher=lambda url:(calls.append(url) or fetched),parser=lambda response:ProductCard.from_jsonable(payload))
    assert len(calls)==1 and service.reparse_runs==1 and service.inspector_runs==1
    assert service.full_crawl_runs==0 and result["persisted"] is False


import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_stage6e import csrf, make_client
from universal_supplier.stage6e import InMemoryStage6EService, fill_rates_text


def test_ignore_product_and_do_not_import_both_exclude():
    rules=[{"id":1,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"IGNORE_PRODUCT","active":True},
           {"id":2,"supplier_id":7,"scope":"product","entity_key":"B","rule_type":"DO_NOT_IMPORT","active":True}]
    resolver=BusinessRuleResolver(rules)
    assert resolver.resolve(supplier_id=7,source_product_id=1,external_id="A").excluded
    assert resolver.resolve(supplier_id=7,source_product_id=2,external_id="B").excluded


def test_property_mapping_override_is_resolved_centrally():
    rules=[{"id":9,"supplier_id":7,"scope":"property","entity_key":"мощность двигателя","rule_type":"PROPERTY_MAPPING_OVERRIDE",
            "config":{"canonical_property_id":44},"active":True}]
    result=BusinessRuleResolver(rules).resolve(supplier_id=7,source_product_id=1,external_id="A")
    assert result.property_overrides=={"мощность двигателя":44} and result.matched_rule_ids==(9,)


def test_force_review_is_distinct_from_exclusion():
    result=BusinessRuleResolver([{"id":3,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"FORCE_REVIEW","active":True}]).resolve(
        supplier_id=7,source_product_id=1,external_id="A")
    assert result.force_review and not result.excluded


def test_inactive_rules_are_ignored_by_resolver():
    result=BusinessRuleResolver([{"id":1,"supplier_id":7,"scope":"product","entity_key":"A","rule_type":"DO_NOT_IMPORT","active":False}]).resolve(
        supplier_id=7,source_product_id=1,external_id="A")
    assert not result.excluded and result.matched_rule_ids==()


def test_preview_summary_has_every_required_class():
    keys=("price_changes","availability_changes","quantity_changes","no_changes","blocked_by_conflict","needs_review",
          "new_products","accessories","excluded_by_rule","missing_supplier_data","anomaly_guard_blocked")
    summary=InMemoryStage6EService().preview()["summary"]
    assert tuple(summary)==keys


def test_preview_pagination_does_not_truncate_total():
    service=InMemoryStage6EService(preview_rows=[{"external_id":str(i)} for i in range(250)])
    page=service.preview(page=3,page_size=100)
    assert page["total"]==250 and page["pages"]==3 and len(page["rows"])==50 and page["rows"][0]["external_id"]=="200"


def test_fill_rates_are_human_readable():
    assert fill_rates_text({"fill_rates":{"price":1,"sku":0.5}})=="Цена 100%, SKU 50%"


def test_mapping_confirm_reject_ignore_and_canonical_create():
    service=InMemoryStage6EService(
        properties=[{"id":7,"display_name_ru":"Мощность","code":"POWER","data_type":"number"}],
        mappings=[{"id":4,"supplier_name":"Optimum","source_name":"Мощность","status":"AUTO_PROPOSED","canonical_property_id":7},
                  {"id":5,"supplier_name":"Optimum","source_name":"Диаметр","status":"UNMAPPED","canonical_property_id":None}])
    client,_=make_client(service=service); token=csrf(client)
    assert client.post("/properties/mappings/4/confirm",data={"canonical_property_id":"7","csrf_token":token},follow_redirects=False).status_code==303
    assert client.post("/properties/mappings/5/decision",data={"status":"MANUAL_REJECTED","csrf_token":token},follow_redirects=False).status_code==303
    assert client.post("/properties/canonical",data={"code":"SPINDLE_BORE","display_name_ru":"Отверстие шпинделя","data_type":"number","csrf_token":token},follow_redirects=False).status_code==303
    ignored=next(row for row in service.mappings if row["id"]==5)
    client.post("/properties/mappings/4/decision",data={"status":"IGNORED","csrf_token":token},follow_redirects=False)
    assert service.mappings[0]["status"]=="IGNORED"
    assert ignored["status"]=="MANUAL_REJECTED"
    assert any(row.get("code")=="SPINDLE_BORE" for row in service.properties)


def test_properties_page_has_russian_statuses_placeholder_and_profile_table():
    service=InMemoryStage6EService(
        properties=[{"id":7,"display_name_ru":"Мощность","code":"POWER","data_type":"number","show_in_characteristics":True,"identity_critical":False,"usable_as_filter":True}],
        mappings=[{"id":4,"supplier_name":"Optimum","source_name":"Мощность","status":"AUTO_PROPOSED","canonical_property_id":7,"category_context":"lathe"}],
        profiles=[{"id":1,"display_name_ru":"Токарные","properties":[{"canonical_property_id":7,"name":"Мощность","characteristic":True,"filter":True,"position":0}]}])
    html=make_client(service=service)[0].get("/properties").text
    assert "Выберите свойство" in html and 'selected' in html and "Подтвердить предложенное" in html
    assert "Предложено автоматически" in html and "Да" in html and "Нет" in html
    assert "True" not in html and "False" not in html
    assert "<th>Свойство</th>" in html and "<th>Характеристика</th>" in html and "<th>Фильтр</th>" in html and "<th>Позиция</th>" in html
    assert "toggle_characteristic" in html and "toggle_filter" in html and "set_position" in html


def test_unmapped_dropdown_does_not_preselect_first_canonical():
    template=(ROOT/"universal_supplier/control_plane/templates/properties.html").read_text(encoding="utf-8")
    assert 'option value="" disabled{% if not m.canonical_property_id %} selected{% endif %}>Выберите свойство' in template
    assert "{% if p.id==m.canonical_property_id %} selected{% endif %}" in template


def test_rules_form_has_config_value_and_lifecycle_does_not_erase_reason():
    service=InMemoryStage6EService()
    client,_=make_client(service=service); token=csrf(client)
    assert "config_value" in client.get("/rules").text
    client.post("/rules",data={"scope":"product","entity_key":"QA-1","rule_type":"BRAND_OVERRIDE","config_value":"Approved","reason":"Исходная причина","csrf_token":token},follow_redirects=False)
    created=service.rules[0]
    service.set_rule_active(created["id"],False,1,"Деактивация")
    assert created["reason"]=="Исходная причина" and created["active"] is False
    assert created["config"]["lifecycle"][0]["reason"]=="Деактивация"


def test_preview_page_has_no_apply_and_paginates():
    service=InMemoryStage6EService(preview_rows=[{"external_id":"1","best_sterbrust_id":"SB","supplier_name":"Optimum","decision":"EXACT_EXISTING","field_decisions":[{"field":"price","label":"Цена","current":1,"proposed":2,"policy":"SUPPLIER_ALLOWED","decision":"PROPOSE_UPDATE","reason":"x"}]}]*120)
    client,_=make_client(service=service)
    html=client.get("/sterbrust-preview").text
    assert "Применить" not in html and 'name="apply"' not in html and "APPLY" not in html
    assert "Изменения цены" in html and "Страница 1 из 2" in html
    assert client.get("/sterbrust-preview?page=2").status_code==200


def test_data_quality_history_and_russian_status_labels():
    service=InMemoryStage6EService(quality_rows=[{"supplier_code":"optimum","supplier_name":"Optimum","latest_quality_critical":True,"latest_anomaly_codes":["CRAWL_COUNT_COLLAPSE"],"latest_quality_at":"2026-09-16","total":10,"active":10,"without_price":0,"without_sku":0,"without_brand":0,"without_description":0,"without_properties":0,"without_images":0,"without_category":0,"review":0,"conflicts":0,"safe_new":0,"parser_errors":0,"metrics":{"fill_rates":{"price":1.0,"sku":0.5}}}])
    html=make_client(service=service)[0].get("/data-quality").text
    assert "Критическая аномалия" in html and "Цена 100%" in html and "{'price'" not in html


def test_single_product_reparse_uses_one_fetch_and_zero_discovery(monkeypatch):
    from universal_supplier.models import FetchRecord
    calls={"get":0,"discover":0}
    class FakeTransport:
        def __init__(self,**kwargs): self.kwargs=kwargs
        def get_validated(self,url,validator,**kwargs):
            calls["get"]+=1; validator(url)
            return FetchRecord(url,url,200,(),1,1,"<html></html>")
    class FakeAdapter:
        def parse_product(self,fetch):
            payload=old_card_payload(); payload.update(requested_url=fetch.requested_url,final_url=fetch.final_url,canonical_url=fetch.final_url)
            return ProductCard.from_jsonable(payload)
        def discover(self,*args,**kwargs):
            calls["discover"]+=1; raise AssertionError("discovery must not run")
    monkeypatch.setattr("universal_supplier.http.HttpTransport",FakeTransport)
    monkeypatch.setattr("universal_supplier.adapters.partner_st.PartnerStAdapter",lambda: FakeAdapter())
    monkeypatch.setattr("universal_supplier.stage6e.validate_supplier_url",lambda url,allowed,**kwargs: url)
    monkeypatch.setattr("universal_supplier.control_plane.web.validate_supplier_url",lambda url,allowed,**kwargs: url)
    monkeypatch.setattr("universal_supplier.control_plane.web.validate_fetch_targets",lambda *args,**kwargs: None)
    service=InMemoryStage6EService()
    client,_=make_client(service=service)
    html=client.post("/products/partner_st/1/reparse",data={"csrf_token":csrf(client)}).text
    assert calls=={"get":1,"discover":0} and service.reparse_runs==1 and service.full_crawl_runs==0
    assert "Discovery calls = 0" in html and "Сохранить новые данные в нашу БД" in html
    persisted=client.post("/reparse/1/persist",data={"csrf_token":csrf(client),"confirm":"true"},follow_redirects=False)
    assert persisted.status_code==303


def test_xlsx_cells_strip_timezone():
    from datetime import datetime, timezone
    from universal_supplier.stage6e import _cell
    value=_cell(datetime(2026,9,16,12,0,tzinfo=timezone.utc))
    assert value.tzinfo is None


def test_xlsx_write_only_and_csv_streaming_are_wired():
    source=(ROOT/"universal_supplier/stage6e.py").read_text(encoding="utf-8")
    web=(ROOT/"universal_supplier/control_plane/web.py").read_text(encoding="utf-8")
    store=(ROOT/"universal_supplier/control_plane/store.py").read_text(encoding="utf-8")
    assert "Workbook(write_only=True)" in source and "csv_chunks" in web and "iter_export_rows" in store and "fetchmany(chunk_size)" in store


def test_no_apply_route_and_no_migration_015():
    client,_=make_client()
    assert not any("apply" in getattr(route,"path","").lower() for route in client.app.routes)
    assert not list((ROOT/"migrations").glob("015_*.sql"))
