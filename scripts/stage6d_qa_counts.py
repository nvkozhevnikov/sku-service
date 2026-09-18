#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.postgres import PostgresConfig


def main() -> None:
    import psycopg
    config=PostgresConfig.from_env(); result={}
    with psycopg.connect(**config.kwargs(), autocommit=True) as c, c.cursor() as q:
        q.execute("SELECT count(*) FROM app_users"); result["users"]=q.fetchone()[0]
        q.execute("SELECT role,count(*) FROM app_users WHERE is_active GROUP BY role ORDER BY role"); result["active_users_by_role"]={r[0]:r[1] for r in q.fetchall()}
        q.execute("SELECT count(*) FILTER (WHERE lifecycle_status<>'RESOLVED'),count(*) FILTER (WHERE lifecycle_status='RESOLVED') FROM review_cases"); result["open_review_cases"],result["resolved_review_cases"]=q.fetchone()
        q.execute("SELECT coalesce(resolved_decision_class,'UNRESOLVED'),count(*) FROM review_cases GROUP BY resolved_decision_class ORDER BY 1"); result["review_cases_by_decision_class"]={r[0]:r[1] for r in q.fetchall()}
        for key,decision in (("manual_confirmed_existing","MANUAL_CONFIRMED"),("manual_confirmed_new","MANUAL_CONFIRMED_NEW"),("manual_accessories","MANUAL_ACCESSORY")):
            q.execute("SELECT count(*) FROM review_decisions WHERE decision_class=%s",(decision,));result[key]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM candidate_rejections");result["candidate_rejections"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM review_cases WHERE lifecycle_status='POSTPONED'");result["postponed"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM audit_events WHERE action='REVIEW_REQUIRED_SOURCE_CHANGED'");result["reopened_due_source_change"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM audit_events");result["audit_events"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM crawl_jobs WHERE trigger_type='manual'");result["manual_crawl_qa_jobs"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM crawl_jobs WHERE trigger_type='schedule'");result["scheduled_crawl_qa_jobs"]=q.fetchone()[0]
        q.execute("SELECT count(*) FROM schema_migrations");result["migrations"]=q.fetchone()[0]
    print(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True))


if __name__=="__main__":main()
