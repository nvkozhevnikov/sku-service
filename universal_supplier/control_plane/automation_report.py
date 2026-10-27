"""Hourly local SELECT + native checkpoint readback. No LLM, ingest or backup."""
from collections import Counter
from datetime import datetime,timezone
from pathlib import Path
import json
from .source_hourly import checkpoint,bucket,save_report
from .source_recovery import normal
from .source_runtime import job_dir


def collect(session,*,previous=None,worker='RUNNING',blocked=None):
    import psycopg
    now=datetime.now(timezone.utc);rows=[];current={};copies={}
    from .runtime_config import guarded_connect
    with guarded_connect(session.config,autocommit=True,
        options='-c default_transaction_read_only=on -c statement_timeout=120000') as c:
        from .source_canary import validate_connection
        validate_connection(c);session.check_mappings(c)
        jobs=c.execute('''SELECT j.id,s.code,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata,j.error_message
            FROM crawl_jobs j JOIN suppliers s ON s.id=j.supplier_id LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
            WHERE j.settings_snapshot->'automation'->>'approval_sha256'=%s ORDER BY j.id''',(session.sha,)).fetchall()
        active=c.execute("SELECT id,status FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id").fetchall()
        for jid,code,state,run,snapshot,metadata,error in jobs:
            phase=snapshot['automation']['phase'];current[(code,phase)]=(jid,state,error)
            rt=(metadata or {}).get('source_runtime') or {}
            owner=rt.get('owner_job_id') or snapshot['crawl_settings'].get('resume_owner_job_id') or jid
            key=(code,phase,owner)
            if key not in copies:copies[key]=checkpoint(job_dir(code,owner)/'checkpoint.json')
        # Copy durable files first. A newer checkpoint must never be compared
        # against an older repeatable SQL snapshot during a concurrent commit.
        c.execute('BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY')
        session.check_mappings(c)
        for code in session.value['suppliers']:
            receipts={};proven=set();quarantine=errors=cached=remaining=0;requests=0
            for (source,phase,owner),cp in copies.items():
                if source!=code or cp is None:continue
                for item in cp['terminal'].values():
                    receipt=item['receipt'];state=item['state']
                    quarantine+=state=='QUARANTINED';errors+=state in ('FAILED','UNCERTAIN')
                    cached+=state=='HTTP_CAPTURED_NOT_COMMITTED'
                    if state=='COMMITTED':
                        receipts[receipt['capture_fingerprint']]=receipt
                        if receipt.get('matching_receipt',{}).get('status')=='succeeded':proven.add(receipt['external_id'])
                remaining+=len(cp['frontier'])+len(cp['inflight'])
            for jid,source,state,run,snap,meta,error in jobs:
                if source==code:requests+=((meta or {}).get('source_runtime') or {}).get('request_metrics',{}).get('requests_total',0)
            saved=c.execute('''SELECT cap.capture_fingerprint,cap.source_product_id,sp.external_id FROM supplier_http_captures cap
                JOIN source_products sp ON sp.id=cap.source_product_id JOIN suppliers s ON s.id=cap.supplier_id
                JOIN offer_commercial_observations obs ON obs.capture_id=cap.id AND obs.source_product_id=cap.source_product_id
                    AND obs.observed_at=cap.observed_at WHERE s.code=%s AND cap.capture_fingerprint=ANY(%s)''',(code,list(receipts))).fetchall()
            if len(saved)!=len(receipts) or any(receipts[fp]['external_id']!=ext for fp,pid,ext in saved):
                raise ValueError('AUTOMATION_SQL_RECEIPT_MISMATCH')
            ids={pid for fp,pid,ext in saved};known={pid for fp,pid,ext in saved if ext in proven}
            decisions=c.execute('SELECT source_product_id,status,auto_accepted FROM product_matches WHERE is_current AND source_product_id=ANY(%s)',(sorted(ids),)).fetchall()
            buckets=Counter(bucket(status,auto) for pid,status,auto in decisions if pid in known)
            if len(known)!=sum(buckets.values()):raise ValueError('AUTOMATION_MATCHING_PROOF_MISMATCH')
            before=next((r for r in (previous or {}).get('sources',[]) if r['source']==code),None)
            if session.value.get('commercial_only'):
                # Include approved URLs whose jobs have not started yet. SQL
                # commits and durable quarantine are the only accounted outcomes.
                remaining=max(0,len(session.value['suppliers'][code]['targets'])-len(receipts)-quarantine)
            elapsed=(now-datetime.fromisoformat(previous['at'])).total_seconds() if previous else 0
            speed=max(0,len(receipts)-(before or {}).get('committed',len(receipts)))*3600/elapsed if elapsed>0 else None
            rows.append({'source':code,'committed':len(receipts),'unique_products':len(ids),
                **{k:buckets.get(k,0) for k in ('AUTO','REVIEW','CONFLICT','NEW')},
                'matching_pending':len(ids-known),'quarantine':quarantine,'errors':errors,
                'cached_pending':cached,'remaining_urls':remaining,'HTTP':requests,'urls_per_hour':speed,
                'eta_hours_estimate':remaining/speed if speed else None,
                'phases':{p:current.get((code,p)) for p in ('REFRESH','DISCOVERY')}})
        c.execute('ROLLBACK')
    return {'at':now.isoformat(),'classification':'LIVE-VERIFIED SELECT + durable CP, no business writes',
        'approval_sha256':session.sha,'execution_window':{'work_until':session.end.isoformat(),'hard_until':session.hard.isoformat()},
        'postgres':'IDENTITY_VERIFIED','worker':worker,'active_jobs':normal(active),'original1282_exact':True,
        'sources':rows,'blocked_sources':blocked or {},'committed':sum(r['committed'] for r in rows),
        'unique_products':sum(r['unique_products'] for r in rows),'external_catalog_completeness':'NOT_PROVEN',
        'eta_basis':'INFERENCE: observed rate/known queue; unfinished discovery excluded'}


def write(session,index,previous=None,worker='RUNNING',blocked=None):
    result=collect(session,previous=previous,worker=worker,blocked=blocked)
    save_report(session.folder/'hourly',result,index)
    return result
