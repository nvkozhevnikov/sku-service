#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    import psycopg
    config=PostgresConfig.from_env(); checks={}
    with psycopg.connect(**config.kwargs(),autocommit=True) as c,c.cursor() as q:
        q.execute("SELECT count(*),max(filename) FROM schema_migrations"); checks["migration_013"]=q.fetchone()==(13,"013_operator_admin_review.sql")
        q.execute("SELECT array_agg(code ORDER BY code) FROM suppliers WHERE code IN ('optimum','partner_st')"); checks["real_suppliers"]=q.fetchone()[0]==["optimum","partner_st"]
        q.execute("SELECT count(*) FROM suppliers WHERE code LIKE '%qa%'"); checks["no_qa_suppliers_in_ui"]=q.fetchone()[0]==0
        q.execute("SELECT bool_and(password_hash LIKE 'scrypt$%') AND bool_and(password_hash NOT LIKE '%Stage6D-QA-Password-2026%') FROM app_users"); checks["password_hashing"]=bool(q.fetchone()[0])
        q.execute("SELECT count(*) FROM review_cases rc LEFT JOIN source_products sp ON sp.id=rc.source_product_id WHERE sp.id IS NULL"); checks["review_referential_integrity"]=q.fetchone()[0]==0
        q.execute("SELECT count(*) FROM review_cases WHERE lifecycle_status='REOPENED_SOURCE_CHANGED'"); checks["source_change_reopen"]=q.fetchone()[0]==1
        q.execute("SELECT count(*) FROM candidate_rejections"); checks["candidate_rejection_persisted"]=q.fetchone()[0]==1
        q.execute("SELECT count(*) FROM crawl_jobs WHERE status='skipped_already_queued' AND deduplicated_job_id IS NOT NULL"); checks["manual_schedule_collision_guard"]=q.fetchone()[0]>=1
        q.execute("SELECT count(*) FROM crawl_jobs WHERE trigger_type='manual' AND requested_by_user_id IS NOT NULL"); checks["manual_user_provenance"]=q.fetchone()[0]==1
        q.execute("SELECT id FROM audit_events ORDER BY id LIMIT 1"); audit_id=q.fetchone()[0]
        try:
            q.execute("UPDATE audit_events SET comment='forbidden' WHERE id=%s",(audit_id,))
            checks["audit_append_only"]=False
        except psycopg.Error:
            checks["audit_append_only"]=True
    print(json.dumps(checks,indent=2,sort_keys=True))
    if not all(checks.values()): raise SystemExit(1)


if __name__=="__main__":main()
