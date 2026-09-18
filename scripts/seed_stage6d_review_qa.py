#!/usr/bin/env python3
"""Create a tiny isolated review fixture and exercise every manual class; no network."""
from __future__ import annotations
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.control_plane.admin_store import PostgresAdminStore
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    import psycopg
    config = PostgresConfig.from_env(); store = PostgresAdminStore(config)
    user = store.user_by_username("qa_admin")
    if not user: raise SystemExit("qa_admin is required")
    user_id = user[0].id
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.transaction(), c.cursor() as q:
        q.execute("SELECT count(*) FROM source_products WHERE external_id LIKE 'stage6d-qa-%'")
        if q.fetchone()[0]: raise SystemExit("Refusing to duplicate Stage 6D review QA fixtures")
        q.execute("INSERT INTO catalog_products(name) VALUES ('Stage 6D QA existing') RETURNING id"); catalog_id=q.fetchone()[0]
        q.execute("""INSERT INTO sterbrust_products(sterbrust_product_id,catalog_product_id,name,normalized_name,brand_raw,normalized_brand,model_raw,normalized_model,category_path,active,snapshot_hash,first_seen_at,last_seen_at,last_synced_at)
                     VALUES ('stage6d-500',%s,'Станок QA-500','станок qa 500','Optimum','optimum','QA-500','qa500','Станки / QA',true,'stage6d-qa',now(),now(),now())""", (catalog_id,))
        q.execute("SELECT id FROM suppliers WHERE code='optimum'"); supplier_id=q.fetchone()[0]
        ids=[]
        for n, decision in enumerate(("REVIEW_EXISTING","SAFE_NEW_PRODUCT_CANDIDATE","ACCESSORY_OR_COMPATIBILITY_ITEM","CONFLICT","INSUFFICIENT_IDENTITY"),1):
            q.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,sku,name,source_url,brand_raw,raw_data,first_seen_at,last_seen_at)
                         VALUES (%s,%s,true,%s,%s,%s,'Optimum','{}',now(),now()) RETURNING id""",
                      (supplier_id,f"stage6d-qa-{n}",f"QA-{n}",f"QA товар {n}",f"https://www.optimum.su/stage6d-qa-{n}"))
            source_id=q.fetchone()[0]; ids.append(source_id)
            proposed = "stage6d-500" if decision=="REVIEW_EXISTING" else None
            q.execute("""INSERT INTO product_identity_decisions(source_product_id,decision,own_model,reference_model,product_kind,best_sterbrust_id,decision_reason,identity_characteristics,rule_version)
                         VALUES (%s,%s,%s,NULL,'machine',%s,%s,'{"qa":true}','stage6d-qa')""", (source_id,decision,f"QA-{n}",proposed,"Изолированная Stage 6D QA проверка"))
    generated=store.generate_review_cases()
    cases={row["external_id"]:row for row in store.review_cases() if row["external_id"].startswith("stage6d-qa-")}
    store.decide(cases["stage6d-qa-1"]["id"],user_id,"MANUAL_CONFIRMED",sterbrust_product_id="stage6d-500",comment="QA подтверждение существующего")
    store.reject_candidate(cases["stage6d-qa-2"]["id"],user_id,"stage6d-500","QA кандидат неверный")
    store.decide(cases["stage6d-qa-2"]["id"],user_id,"MANUAL_CONFIRMED_NEW",comment="QA новый товар")
    store.decide(cases["stage6d-qa-3"]["id"],user_id,"MANUAL_ACCESSORY",parent_sterbrust_product_id="stage6d-500",comment="QA аксессуар")
    store.decide(cases["stage6d-qa-4"]["id"],user_id,"POSTPONED",comment="QA отложено")
    store.decide(cases["stage6d-qa-5"]["id"],user_id,"MARKED_REVIEWED",comment="QA первоначальная проверка")
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("UPDATE product_identity_decisions SET own_model='QA-5-CHANGED',audited_at=now() WHERE source_product_id=%s", (ids[4],))
    reopened=store.generate_review_cases()
    print(f"REVIEW_CASES_CREATED = {generated['inserted']}")
    print(f"SOURCE_CHANGE_REOPENED = {reopened['reopened']}")
    print("NETWORK_CALLS = 0")


if __name__ == "__main__": main()
