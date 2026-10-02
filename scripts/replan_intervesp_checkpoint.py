"""Record the proven stopped checkpoint and minimal identity-first plan.

No GET/DB mutation/process stop. Refuses a still-running collector or changed
checkpoint. Original bytes and their SHA are preserved before state annotation.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ingest_intervesp_listing_archive import digest, write_report
from scripts.intervesp_full_preflight import read_source_state
from universal_supplier.intervesp_final_proposals import remaining_detail_buckets

BASE=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01'
EXPECTED='6f2073df3cc146e69980916f3cbf77fa0d0e3741a199127706c6211411e53a45'


def main():
    inventory=subprocess.run(['powershell.exe','-NoProfile','-Command',
        "$ErrorActionPreference='Stop'; @(Get-CimInstance Win32_Process -Filter \"name = 'python.exe'\" | "
        "Where-Object {$_.CommandLine -like '*scripts/enrich_intervesp_details.py*'}).Count"],
        check=True,capture_output=True,text=True)
    if inventory.stdout.strip()!='0': raise RuntimeError('Collector is still running or inventory unverified')
    state=read_source_state()
    path=BASE/'DETAIL_ENRICHMENT.json'
    if digest(path)!=EXPECTED: raise RuntimeError('Stopped checkpoint SHA changed; inspect first')
    checkpoint=json.loads(path.read_text(encoding='utf-8'))
    if checkpoint['stage']!='RUNNING' or len(checkpoint['rows'])!=284:
        raise RuntimeError('Not the proven stopped checkpoint')
    assessment=json.loads((BASE/'OFFLINE_ASSESSMENT.json').read_text(encoding='utf-8'))
    buckets=remaining_detail_buckets(assessment['detail_get_plan'],checkpoint)
    original=BASE/'DETAIL_PAUSED_284.json'
    if original.exists(): raise RuntimeError('Paused evidence already exists; preserve it')
    original.write_bytes(path.read_bytes())
    if digest(original)!=EXPECTED: raise RuntimeError('Preserved checkpoint SHA mismatch')
    pause={'at':datetime.now(timezone.utc).isoformat(),'operator_requested':True,
        'collector_pid':3064,'parent_pid':13332,'checkpoint_rows':284,
        'method':'targeted collector stop 0.75s after atomic checkpoint; rc_runtime idle, backend_xid NULL',
        'postgres_start_stop':False,'checkpoint_sha256':EXPECTED,'evidence_ref':str(original)}
    checkpoint.update(stage='PAUSED_BY_OPERATOR_REPLAN',pause_proof=pause)
    write_report(path,checkpoint)
    result={'stage':'IDENTITY_FIRST_REPLAN','pause_proof':pause,'sql_readonly':state['table_counts'],
        'manifest_sha256':checkpoint['manifest_sha256'],
        'bucket_counts':{key:len(value) for key,value in buckets.items()},'remaining_urls':buckets,
        'minimum_gets_before_matching':len(buckets['identity-critical']),
        'minimum_pause_seconds':len(buckets['identity-critical'])*20,
        'nonidentity_gets_deferred':sum(len(value) for key,value in buckets.items() if key!='identity-critical'),
        'after_matching':'Existing price/availability gaps; ready NEW full content; REVIEW only resolvable ambiguity'}
    write_report(BASE/'IDENTITY_FIRST_REPLAN.json',result)
    print(json.dumps({key:value for key,value in result.items() if key!='remaining_urls'}))


if __name__=='__main__': main()
