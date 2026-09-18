#!/usr/bin/env python3
"""Isolated UI seed for Stage 6E.1 browser QA. No supplier HTTP, no Sterbrust writes."""
from __future__ import annotations
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from universal_supplier.control_plane.security import hash_password
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    import psycopg
    password_hash = hash_password("Stage6e1-QA-password", salt=b"0123456789abcdef")
    with psycopg.connect(**PostgresConfig.from_env().kwargs(), autocommit=True) as connection, connection.cursor() as q:
        q.execute(
            """INSERT INTO app_users(username,display_name,password_hash,role)
               VALUES('qa_admin','QA Admin',%s,'ADMIN'),('stage6e_admin','Stage 6E Admin',%s,'ADMIN')
               ON CONFLICT(username) DO UPDATE SET password_hash=EXCLUDED.password_hash,is_active=true
               RETURNING id""",
            (password_hash, password_hash),
        )
        q.execute("SELECT id FROM app_users WHERE username='qa_admin'")
        user_id = q.fetchone()[0]
        q.execute(
            """INSERT INTO suppliers(code,name,base_url,adapter_name,enabled,crawl_settings,rate_settings)
               VALUES('optimum','Optimum','https://www.optimum.su/','optimum',true,'{"crawl_type":"full"}','{}')
               ON CONFLICT(code) DO NOTHING"""
        )
        q.execute("SELECT id FROM suppliers WHERE code='partner_st'")
        supplier_id = q.fetchone()[0]
        q.execute(
            """INSERT INTO canonical_properties(code,display_name_ru,data_type,canonical_unit,description,
                 show_in_characteristics,usable_for_identity,identity_critical,usable_as_filter,created_by)
               VALUES('MAX_DRILLING_DIAMETER','Максимальный диаметр сверления','number','mm','QA',true,true,true,true,%s)
               ON CONFLICT(code) DO NOTHING""",
            (user_id,),
        )
        q.execute("SELECT id FROM canonical_properties WHERE code='POWER'")
        power = q.fetchone()
        q.execute(
            """INSERT INTO supplier_property_mappings(supplier_id,category_context,source_name,normalized_source_name,
                 sample_value,source_unit,inferred_type,canonical_property_id,status,confidence,evidence)
               VALUES(%s,'','Диаметр','диаметр','30 мм','mm','number',NULL,'UNMAPPED',0.41,'{"ambiguous":true}')
               ON CONFLICT(supplier_id,category_context,normalized_source_name) DO NOTHING""",
            (supplier_id,),
        )
        if power:
            q.execute(
                """INSERT INTO supplier_property_mappings(supplier_id,category_context,source_name,normalized_source_name,
                     sample_value,source_unit,inferred_type,canonical_property_id,status,confidence,evidence)
                   VALUES(%s,'qa','Мощность QA','мощность qa','0.75','kW','number',%s,'AUTO_PROPOSED',0.94,'{"unit_compatible":true}')
                   ON CONFLICT(supplier_id,category_context,normalized_source_name) DO UPDATE SET
                     canonical_property_id=EXCLUDED.canonical_property_id,status='AUTO_PROPOSED'""",
                (supplier_id, power[0]),
            )
        q.execute(
            """INSERT INTO category_filter_profiles(category_key,display_name_ru,created_by)
               VALUES('drill_press','Сверлильные станки',%s)
               ON CONFLICT(category_key) DO UPDATE SET display_name_ru=EXCLUDED.display_name_ru
               RETURNING id""",
            (user_id,),
        )
        profile_id = q.fetchone()[0]
        if power:
            q.execute(
                """INSERT INTO category_filter_profile_properties(profile_id,canonical_property_id,use_as_characteristic,use_as_filter,position)
                   VALUES(%s,%s,true,true,0)
                   ON CONFLICT(profile_id,canonical_property_id) DO NOTHING""",
                (profile_id, power[0]),
            )
        q.execute(
            """INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by)
               SELECT %s,'product','QA-1','IGNORE_PRODUCT','QA ignore',%s
               WHERE NOT EXISTS (SELECT 1 FROM business_rules WHERE entity_key='QA-1' AND rule_type='IGNORE_PRODUCT')""",
            (supplier_id, user_id),
        )
        q.execute(
            """INSERT INTO business_rules(supplier_id,scope,entity_key,rule_type,reason,created_by)
               SELECT %s,'product','QA-2','FORCE_REVIEW','QA force review',%s
               WHERE NOT EXISTS (SELECT 1 FROM business_rules WHERE entity_key='QA-2' AND rule_type='FORCE_REVIEW')""",
            (supplier_id, user_id),
        )
    print(json.dumps({"STAGE6E1_UI_SEED": "PASS", "STERBRUST_WRITES": 0, "EXTERNAL_HTTP_READS": 0}))


if __name__ == "__main__":
    main()
