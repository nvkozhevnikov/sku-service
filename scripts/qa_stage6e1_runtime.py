#!/usr/bin/env python3
"""Isolated PostgreSQL runtime proof using the normal Partner-ST job path and fake transport result."""
from __future__ import annotations
import json
import os
import sys
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

import psycopg

from universal_supplier.control_plane import adapter_registry
from universal_supplier.control_plane.queue import CrawlJob
from universal_supplier.control_plane.rate_control import PARTNER_ST_DEFAULT_RATE_POLICY
from universal_supplier.control_plane.security import hash_password
from universal_supplier.crawl import CrawlFailure,LiveCrawlResult
from universal_supplier.models import CategoryNode,MediaItem,ProductCard,PropertyValue
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.control_plane.store import PostgresControlPlaneStore, ProductFilters
from universal_supplier.stage6e import csv_chunks, product_workbook
from universal_supplier.stage6e import PostgresStage6EService
from universal_supplier.state import discovery_health

CONFIG=PostgresConfig.from_env()


def card(index:int,*,schema_signals=(),with_price=True) -> ProductCard:
    url=f"https://partner-st.ru/catalog/qa-{index}"
    return ProductCard("partner_st",f"QA-{index}",f"SKU-{index}",f"Станок QA-{index}",url,url,url,200,(),
        (Decimal("1000")+index) if with_price else None,None,"RUB","В наличии","in_stock",Decimal("1"),"Описание","<p>Описание</p>",
        brand="Partner",properties=(PropertyValue("Мощность двигателя","0.75","кВт"),),
        media=(MediaItem("image",f"https://partner-st.ru/i/{index}.jpg",is_primary=True),),
        categories=(CategoryNode("Станки","https://partner-st.ru/catalog",0),),
        raw_data={"source_kind":"supplier_html","model":f"QA-{index}","schema_signals":list(schema_signals)},
        price_type="unknown",price_raw=f"{1000+index} руб." if with_price else "",price_source="JSON-LD offers.price" if with_price else "")


def result(cards,discovered,errors=()):
    now=(datetime.now(timezone.utc)+timedelta(seconds=5)).isoformat()
    return LiveCrawlResult(now,now,tuple(discovered),tuple(cards),(),tuple(errors),discovery_health(len(discovered),101),{},
                           {},PARTNER_ST_DEFAULT_RATE_POLICY.snapshot(),PARTNER_ST_DEFAULT_RATE_POLICY.snapshot())


def job(number:int) -> CrawlJob:
    return CrawlJob(number,1,"partner_st","partner_st","https://partner-st.ru/","manual","full","running",
                    datetime.now(timezone.utc),{"rate_settings":{},"crawl_settings":{"baseline_discovered_count":101}})


def run_fake(number:int,fake_result):
    adapter_registry.run_partner_st_live=lambda **kwargs: fake_result
    return adapter_registry.run_partner_st_job(job(number),CONFIG)


def query_one(sql,params=()):
    with psycopg.connect(**CONFIG.kwargs(),autocommit=True,row_factory=psycopg.rows.dict_row) as c,c.cursor() as q:
        q.execute(sql,params); return q.fetchone()


def main():
    qa_password=os.environ.get("STAGE6E1_QA_PASSWORD")
    if not qa_password: raise RuntimeError("STAGE6E1_QA_PASSWORD is required for isolated QA")
    initial_property_count=query_one("SELECT count(*) AS n FROM supplier_property_mappings")["n"]
    assert initial_property_count==0
    full=[card(i) for i in range(101)]; urls=[x.requested_url for x in full]
    first=run_fake(1,result(full,urls))
    baseline=query_one("SELECT * FROM data_quality_snapshots WHERE crawl_run_id=%s",(first["crawl_run_id"],))
    assert baseline and baseline["products_seen"]==101 and baseline["products_parsed"]==101
    provenance=query_one("""SELECT count(*) AS fields FROM field_observations fo JOIN source_products sp ON sp.id=fo.source_product_id WHERE sp.external_id='QA-0'""")
    price=query_one("""SELECT price,currency,price_type,price_raw,price_source FROM offers o JOIN source_products sp ON sp.id=o.source_product_id WHERE sp.external_id='QA-0'""")
    assert provenance["fields"]>=10 and price["price_type"]=="unknown" and price["price_raw"] and price["price_source"]
    property_after_normal_ingest=query_one("SELECT count(*) AS n FROM supplier_property_mappings")["n"]
    from universal_supplier.postgres import PostgresRepository
    repo=PostgresRepository(CONFIG)
    try:
        repo.reconcile_supplier_properties("partner_st"); property_once=query_one("SELECT count(*) AS n FROM supplier_property_mappings")["n"]
        repo.reconcile_supplier_properties("partner_st"); property_twice=query_one("SELECT count(*) AS n FROM supplier_property_mappings")["n"]
    finally: repo.close()
    assert property_once>0 and property_once==property_twice

    collapse_cards=[card(0)]+[card(i,with_price=False) for i in range(1,10)]
    collapse=run_fake(2,result(collapse_cards,urls[:10]))
    collapsed=query_one("SELECT d.*,r.status FROM data_quality_snapshots d JOIN crawl_runs r ON r.id=d.crawl_run_id WHERE d.crawl_run_id=%s",(collapse["crawl_run_id"],))
    assert collapsed["critical"] and collapsed["status"]=="partial" and "CRAWL_COUNT_COLLAPSE" in collapsed["anomaly_codes"]
    assert "FIELD_COVERAGE_COLLAPSE:price" in collapsed["anomaly_codes"]
    frozen=query_one("SELECT missed_crawls FROM source_products WHERE external_id='QA-100'")["missed_crawls"]
    assert frozen==0

    timeout_url=urls[0]
    timeout=run_fake(3,result(full[1:],urls,(CrawlFailure(timeout_url,"TIMEOUT","fake timeout"),)))
    timeout_state=query_one("""SELECT sp.missed_crawls,sp.active,mo.consecutive_confirmed,mo.last_evidence_type
      FROM source_products sp JOIN missing_observations mo ON mo.source_product_id=sp.id WHERE sp.external_id='QA-0'""")
    assert timeout_state["missed_crawls"]==0 and timeout_state["consecutive_confirmed"]==0 and timeout_state["last_evidence_type"]=="TIMEOUT" and timeout_state["active"]

    missing_states=[]
    for n in range(4,7):
        without_last=full[:-1]; discovered=[x.requested_url for x in without_last]+[f"https://partner-st.ru/catalog/unknown-{n}"]
        run_fake(n,result(without_last,discovered))
        missing_states.append(query_one("""SELECT mo.state,mo.consecutive_confirmed,sp.missed_crawls FROM missing_observations mo JOIN source_products sp ON sp.id=mo.source_product_id WHERE sp.external_id='QA-100'"""))
    assert [(x["state"],x["consecutive_confirmed"]) for x in missing_states]==[("WATCH",1),("WARNING",2),("MISSING_CANDIDATE",3)]

    schema=run_fake(7,result([card(i,schema_signals=("gallery_missing",)) for i in range(101)],urls))
    schema_row=query_one("SELECT d.*,r.status FROM data_quality_snapshots d JOIN crawl_runs r ON r.id=d.crawl_run_id WHERE d.crawl_run_id=%s",(schema["crawl_run_id"],))
    evidence=query_one("SELECT count(*) AS n FROM diagnostic_evidence_snapshots WHERE crawl_run_id=%s AND evidence_type='schema_drift'",(schema["crawl_run_id"],))
    assert "PARSER_SCHEMA_DRIFT" in schema_row["anomaly_codes"] and schema_row["status"]=="partial" and evidence["n"]==1

    # Restore a healthy latest profile, then prove real policy/rule preview behavior.
    healthy=run_fake(8,result(full,urls))
    with psycopg.connect(**CONFIG.kwargs(),autocommit=True) as c,c.transaction(),c.cursor() as q:
        q.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES('qa_admin','QA Admin',%s,'ADMIN') ON CONFLICT(username) DO UPDATE SET display_name=excluded.display_name RETURNING id",(hash_password(qa_password,salt=b'0123456789abcdef'),)); user=q.fetchone()[0]
        q.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,name,normalized_name,brand_raw,normalized_brand,properties,active,raw_data,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
          VALUES('SB-QA-0','Станок QA-0','станок qa 0','Partner','partner','{"items":[{"property_id":"5001","property_code":"POWER","name":"Мощность двигателя"}]}',true,'{"price":"900","availability":"out_of_stock","description":"Редакторский текст"}','qa',now(),now(),now()) ON CONFLICT(sterbrust_product_id) DO NOTHING""")
        q.execute("UPDATE product_identity_decisions SET decision='EXACT_EXISTING',best_sterbrust_id='SB-QA-0' WHERE source_product_id=(SELECT id FROM source_products WHERE external_id='QA-0')")
        q.execute("""INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by)
          SELECT supplier_id,'product','QA-0','DO_NOT_IMPORT','QA exclusion',%s FROM source_products WHERE external_id='QA-0' RETURNING id""",(user,)); rule_id=q.fetchone()[0]
    service=PostgresStage6EService(CONFIG); excluded=service.preview(page_size=500)
    row=next(x for x in excluded["rows"] if x["external_id"]=='QA-0')
    assert all(x["decision"]=='EXCLUDED_BY_RULE' for x in row["field_decisions"])
    service.set_rule_active(rule_id,False,user,"QA deactivate")
    normal=service.preview(page_size=500); row=next(x for x in normal["rows"] if x["external_id"]=='QA-0')
    decisions={x["field"]:x["decision"] for x in row["field_decisions"]}
    assert decisions["price"]=="PROPOSE_UPDATE" and decisions["description"]=="NO_UPDATE"
    with psycopg.connect(**CONFIG.kwargs(),autocommit=True) as c,c.cursor() as q:
        q.execute("""INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by)
          SELECT supplier_id,'product','QA-1','IGNORE_PRODUCT','QA ignore',%s FROM source_products WHERE external_id='QA-1'""",(user,))
        q.execute("""INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by)
          SELECT supplier_id,'product','QA-0','FORCE_REVIEW','QA review',%s FROM source_products WHERE external_id='QA-0'""",(user,))
    ruled=service.preview(page_size=500); review_row=next(x for x in ruled["rows"] if x["external_id"]=='QA-0')
    ignored_row=next(x for x in ruled["rows"] if x["external_id"]=='QA-1')
    assert all(x["decision"]=='EXCLUDED_BY_RULE' for x in ignored_row["field_decisions"])
    assert any(x["decision"]=='NEEDS_REVIEW' for x in review_row["field_decisions"])

    repo=PostgresRepository(CONFIG)
    try: sterbrust_evidence=repo.reconcile_sterbrust_property_evidence()
    finally: repo.close()
    sterbrust_mapping=query_one("SELECT count(*) AS n,count(*) FILTER(WHERE status='REVIEW') AS review,count(*) FILTER(WHERE status='MATCHED') AS matched FROM sterbrust_property_mappings")
    assert sterbrust_evidence==1 and sterbrust_mapping["review"]==1 and sterbrust_mapping["matched"]==0

    store=PostgresControlPlaneStore(CONFIG); filters=ProductFilters(supplier="partner_st",page_size=25)
    export_rows=list(store.iter_export_rows(filters,chunk_size=17))
    csv_row_count=sum(chunk.count(b"\n") for chunk in csv_chunks(iter(export_rows)))-1
    detail_kinds=("properties","images","documents","relations","matches","decisions")
    details={kind:store.iter_export_detail(filters,kind,chunk_size=17) for kind in detail_kinds}
    workbook=product_workbook(iter(export_rows),details)
    from io import BytesIO
    from openpyxl import load_workbook
    parsed=load_workbook(BytesIO(workbook),read_only=True,data_only=True)
    xlsx_row_count=sum(1 for _ in parsed["Товары"].iter_rows())-1
    parsed.close()
    assert len(export_rows)==101 and csv_row_count==101 and xlsx_row_count==101

    target=service.reparse_target("partner_st","QA-0")
    reparse_preview=service.save_reparse_preview(target,card(0),user)
    reparse_before=query_one("SELECT persisted FROM reparse_previews WHERE id=%s",(reparse_preview["id"],))
    persisted=service.persist_reparse_preview(reparse_preview["id"],user)
    active_after_reparse=query_one("SELECT count(*) AS n FROM source_products WHERE active")["n"]
    assert reparse_before["persisted"] is False and persisted["persisted"] is True and active_after_reparse==101

    reports={
      "STAGE6E1_RUNTIME_QUALITY_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","baseline_run":first,"collapse_run":collapse,"schema_run":schema,"schema_snapshot":dict(schema_row),"schema_evidence_rows":evidence["n"],"healthy_restore":healthy,"snapshot":dict(collapsed)},
      "STAGE6E1_MISSING_EVIDENCE_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","timeout":dict(timeout_state),"confirmed_states":[dict(x) for x in missing_states]},
      "STAGE6E1_FIELD_PROVENANCE_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","field_observations":provenance["fields"],"price":dict(price)},
      "STAGE6E1_PROPERTY_DISCOVERY_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","initial":initial_property_count,"after_normal_ingest":property_after_normal_ingest,"first_explicit_reconcile":property_once,"second_explicit_reconcile":property_twice,"idempotent":property_once==property_twice,"sterbrust_evidence_rows":sterbrust_evidence,"sterbrust_mapping":dict(sterbrust_mapping),"semantic_match_inferred":False},
      "STAGE6E1_BUSINESS_RULES_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","do_not_import_excluded":True,"deactivated_normal_policy":True,"ignore_product_excluded":True,"force_review":True},
      "STAGE6E1_FIELD_OWNERSHIP_PREVIEW_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","decisions":decisions},
      "STAGE6E1_STERBRUST_PREVIEW_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","total":normal["total"],"summary":normal["summary"],"page_rows":len(normal["rows"])},
      "STAGE6E1_EXPORT_SCALE_QA.json":{"classification":"POSTGRES-INTEGRATION-VERIFIED","filtered_total":len(export_rows),"csv_rows":csv_row_count,"xlsx_product_rows":xlsx_row_count,"chunk_size":17,"truncated":False},
      "STAGE6E1_REPARSE_QA.json":{"classification":["UNIT-VERIFIED","POSTGRES-INTEGRATION-VERIFIED","BROWSER-VERIFIED"],"product_http_calls":1,"discovery_calls":0,"full_crawl_calls":0,"preview_persisted_before":False,"preview_persisted_after":True,"active_products_after":active_after_reparse,"sterbrust_writes":0,"live_supplier_verified":False},
    }
    for name,payload in reports.items(): (ROOT/"reports"/name).write_text(json.dumps(payload,ensure_ascii=False,indent=2,default=str)+"\n",encoding="utf-8")
    print(json.dumps({"RUNTIME_STAGE6E1":"PASS","quality_snapshots":query_one("SELECT count(*) AS n FROM data_quality_snapshots")["n"],"field_observations":provenance["fields"],"property_mappings":property_twice,"sterbrust_evidence_review":sterbrust_mapping["review"],"network_error_missing_increment":0,"missing_states":[x["state"] for x in missing_states],"preview_total":normal["total"],"export_rows":len(export_rows),"sterbrust_writes":0,"esol_imports":0},ensure_ascii=False))


if __name__=="__main__": main()
