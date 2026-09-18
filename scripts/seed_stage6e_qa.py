#!/usr/bin/env python3
"""Deterministic local UI seed. Never contacts suppliers or Sterbrust."""
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]; sys.path.insert(0,str(ROOT))
from universal_supplier.postgres import PostgresConfig

def main():
    import psycopg
    with psycopg.connect(**PostgresConfig.from_env().kwargs(),autocommit=True) as c,c.transaction(),c.cursor() as q:
        q.execute("SELECT id FROM app_users WHERE username='stage6e_admin'"); user_id=q.fetchone()[0]
        q.execute("SELECT id FROM suppliers WHERE code='optimum'"); supplier_id=q.fetchone()[0]
        q.execute("""INSERT INTO crawl_runs(supplier_id,crawl_type,started_at,finished_at,status,discovered_count,fetched_count,unchanged_count,metadata)
          VALUES(%s,'full',now()-interval '1 hour',now()-interval '55 minutes','succeeded',2,2,2,'{}') RETURNING id""",(supplier_id,)); run_id=q.fetchone()[0]
        for external,sku,name,description,price,availability in (("QA-100","QA-100","Сверлильный станок QA 100","Проверочное описание",125000,"in_stock"),("QA-200",None,"Токарный станок QA 200",None,None,"unknown")):
            q.execute("""INSERT INTO source_products(supplier_id,external_id,external_id_is_stable,sku,name,source_url,canonical_url,brand_raw,description_text,raw_data,first_seen_at,last_seen_at,last_success_at,active)
              VALUES(%s,%s,true,%s,%s,%s,%s,'Optimum',%s,'{}',now()-interval '10 days',now(),now(),true) RETURNING id""",(supplier_id,external,sku,name,f"https://www.optimum.su/catalog/{external.lower()}",f"https://www.optimum.su/catalog/{external.lower()}",description)); product_id=q.fetchone()[0]
            q.execute("""INSERT INTO offers(supplier_id,source_product_id,offer_kind,sku,price,currency,price_type,price_raw,price_source,availability_raw,availability_normalized,first_seen_at,last_seen_at,last_success_at,active)
              VALUES(%s,%s,'default',%s,%s,'RUB','retail',%s,'supplier_html',%s,%s,now()-interval '10 days',now(),now(),true)""",(supplier_id,product_id,sku,price,str(price) if price else None,"В наличии" if availability=='in_stock' else "Неизвестно",availability))
            if external=="QA-100":
                q.execute("INSERT INTO product_properties(source_product_id,raw_name,raw_value,raw_unit,position,raw_metadata) VALUES(%s,'Мощность','0.75','кВт',0,'{}')",(product_id,))
                q.execute("INSERT INTO product_media(source_product_id,media_type,source_url,position,is_primary,first_seen_at,last_seen_at,raw_metadata) VALUES(%s,'image','https://www.optimum.su/images/qa100.jpg',0,true,now(),now(),'{}')",(product_id,))
            q.execute("""INSERT INTO product_identity_decisions(source_product_id,decision,decision_reason,own_model,product_kind,best_sterbrust_id,conflicts,identity_characteristics,rule_version,audited_at)
              VALUES(%s,%s,'QA evidence',%s,%s,NULL,'{}','{}','stage6e-qa',now())""",(product_id,"REVIEW_EXISTING" if external=="QA-100" else "SAFE_NEW_PRODUCT_CANDIDATE",external,"drill_press" if external=="QA-100" else "lathe"))
        q.execute("""INSERT INTO canonical_properties(code,display_name_ru,data_type,canonical_unit,description,show_in_characteristics,usable_for_identity,identity_critical,usable_as_filter,created_by)
          VALUES('POWER','Мощность','number','W','Номинальная мощность',true,false,false,true,%s),('MAX_DRILLING_DIAMETER','Максимальный диаметр сверления','number','mm','Категорийное свойство',true,true,true,true,%s),('PACKAGING_DIMENSIONS','Размеры упаковки','string',NULL,'Не фильтр',true,false,false,false,%s) RETURNING id""",(user_id,user_id,user_id)); property_ids=[r[0] for r in q.fetchall()]
        q.execute("""INSERT INTO supplier_property_mappings(supplier_id,category_context,source_name,normalized_source_name,sample_value,source_unit,inferred_type,canonical_property_id,status,confidence,evidence)
          VALUES(%s,'drill_press','Мощность двигателя','мощность двигателя','0.75 кВт','kW','number',%s,'AUTO_PROPOSED',0.94,'{"unit_compatible":true,"category_context":true}'),
                (%s,'','Диаметр','диаметр','30 мм','mm','number',NULL,'UNMAPPED',0.41,'{"ambiguous":true}')""",(supplier_id,property_ids[0],supplier_id))
        q.execute("INSERT INTO category_filter_profiles(category_key,display_name_ru,created_by) VALUES('drill_press','Сверлильные станки',%s) RETURNING id",(user_id,)); profile_id=q.fetchone()[0]
        q.execute("INSERT INTO category_filter_profile_properties(profile_id,canonical_property_id,use_as_characteristic,use_as_filter,position) VALUES(%s,%s,true,true,0),(%s,%s,true,true,1)",(profile_id,property_ids[1],profile_id,property_ids[0]))
        q.execute("""INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by) VALUES(%s,'product','QA-200','FORCE_REVIEW','Проверочный пример аудируемого правила',%s)""",(supplier_id,user_id))
        q.execute("""INSERT INTO data_quality_snapshots(supplier_id,crawl_run_id,products_seen,products_parsed,metrics) VALUES(%s,%s,2,2,'{"price_fill_rate":0.5,"sku_fill_rate":0.5,"brand_fill_rate":1.0,"description_fill_rate":0.5,"property_fill_rate":0.5,"image_fill_rate":0.5,"category_fill_rate":0.0}')""",(supplier_id,run_id))
    print("STAGE6E_QA_SEED = PASS")
    print("EXTERNAL_HTTP_READS = 0")
    print("STERBRUST_WRITES = 0")

if __name__=="__main__": main()
