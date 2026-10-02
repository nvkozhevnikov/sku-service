"""Replay saved complete evidence: no public GET, no live RUN2.

Offline ledger first; explicit --apply-replay verifies exact PostgreSQL no-ops
without replacing newer projections or altering canonical/activation state.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ingest_intervesp_listing_archive import saved_pairs, digest, write_report
from scripts.enrich_intervesp_details import restore_capture
from scripts.intervesp_full_preflight import read_source_state
from scripts.generate_proposed_matching import read_qa_rows
from scripts.run_full_supplier_qa import _preflight_rc
from universal_supplier.intervesp_final_proposals import assert_matching_identity_complete, remaining_detail_buckets
from universal_supplier.intervesp_enrichment import enrich_saved_detail
from universal_supplier.commercial_persistence import CommercialObservationLedger
from universal_supplier.postgres import PostgresRepository, PostgresConfig

BASE=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01'


def projection_sha(rows):
    return hashlib.sha256(json.dumps(rows,sort_keys=True,ensure_ascii=False,default=str).encode('utf-8')).hexdigest()


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-replay',action='store_true')
    parser.add_argument('--report',type=Path,required=True)
    args=parser.parse_args()
    if args.report.exists(): raise RuntimeError('Preserve previous verification report')
    pairs=saved_pairs()
    listings={row['product_url']:row for row,_,_ in pairs}
    checkpoint=BASE/'DETAIL_ENRICHMENT.json'
    checkpoint_sha=digest(checkpoint)
    report=json.loads(checkpoint.read_text(encoding='utf-8'))
    plan=[{'url':url,'reason':row['detail_get_reason']} for url,row in listings.items() if row['detail_get_reason']!='none']
    complete=assert_matching_identity_complete(report,plan)
    details=[]
    for url,row in complete.items():
        if row['status']!='PERSISTED_ENRICHMENT': continue
        path=Path(row['evidence_ref'])
        capture=restore_capture(row['capture'])
        if digest(path)!=capture.evidence_sha256: raise RuntimeError('Saved detail evidence changed')
        product=enrich_saved_detail(path.read_text(encoding='utf-8'),listings[url],source_url=url)
        if json.dumps(asdict(product),sort_keys=True,default=str)!=json.dumps(row['snapshot'],sort_keys=True,default=str):
            # JSON turns immutable tuples into arrays; compare the JSON data,
            # not Python container representations.
            if json.loads(json.dumps(asdict(product),default=str))!=row['snapshot']:
                raise RuntimeError('Parser replay differs from persisted enrichment')
        details.append((url,product,capture))
    ledger=CommercialObservationLedger()
    evidence=[(r['product_url'],p,c) for r,p,c in pairs]+details
    for _,product,capture in evidence: ledger.ingest(product,capture)
    if len(ledger.products)!=1642 or len(ledger.offers)!=1642:
        raise RuntimeError('Offline replay duplicated or lost a supplier identity')
    if any(ledger.ingest(product,capture).observation_created for _,product,capture in evidence):
        raise RuntimeError('Offline full saved replay is not idempotent')
    body={'generated_at':datetime.now(timezone.utc).isoformat(),'http_gets':0,'full_live_run2':'DEFERRED',
          'checkpoint_sha256':checkpoint_sha,'listing_records':len(pairs),'detail_records':len(details),
          'offline_products':len(ledger.products),'offline_offers':len(ledger.offers),
          'offline_observations':len(ledger.observations),'offline_second_pass_new_observations':0,
          'deferred_detail_buckets':{key:len(value) for key,value in remaining_detail_buckets(plan,report).items()},
          'stage':'OFFLINE_REPLAY_PASS','verified':False,'database_replay_rows':[]}
    if not args.apply_replay:
        write_report(args.report,body); print(json.dumps(body)); return
    before=read_source_state()
    os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
    readonly=PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_readonly',None,'disable')
    _,before_projection=read_qa_rows(readonly,expected_system_identifier=7691270601420084116)
    repository=PostgresRepository(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_runtime',None,'disable'))
    try:
        _preflight_rc(repository)
        with repository.connection.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(hashtextextended('intervesp-listing-archive-job',0))")
            if not cur.fetchone()[0]: raise RuntimeError('Another collection/replay is running')
        backup=args.report.with_suffix('.before.dump')
        if backup.exists(): raise RuntimeError('Backup path exists')
        args.report.parent.mkdir(parents=True,exist_ok=True)
        pgdump=Path(os.environ['LOCALAPPDATA'])/'Temp/universal_supplier_stage4_pg17_runtime/bin/pg_dump.exe'
        subprocess.run([str(pgdump),'-h','127.0.0.1','-p','55449','-U','rc_admin','-d',readonly.dbname,
                        '-Fc','--no-owner','-f',str(backup)],check=True)
        body.update(before=before['table_counts'],backup={'path':str(backup),'sha256':digest(backup)})
        for number,(url,product,capture) in enumerate(evidence,1):
            if number%25==1: _preflight_rc(repository)
            outcome=repository.persist_commercial_observation(product,capture)
            if not outcome['exact_noop']: raise RuntimeError('Saved replay caused a non-noop mutation')
            body['database_replay_rows'].append({'url':url,'exact_noop':True})
        after=read_source_state()
        _,after_projection=read_qa_rows(readonly,expected_system_identifier=7691270601420084116)
        if before['table_counts']!=after['table_counts'] or projection_sha(before_projection)!=projection_sha(after_projection):
            raise RuntimeError('Replay changed counts or current commercial projection')
        if digest(checkpoint)!=checkpoint_sha: raise RuntimeError('Checkpoint changed during replay')
        body.update(after=after['table_counts'],current_projection_sha256=projection_sha(after_projection),
                    stage='FULL_SAVED_REPLAY_VERIFIED',verified=True)
        write_report(args.report,body)
        print(json.dumps({k:v for k,v in body.items() if k!='database_replay_rows'},default=str))
    except Exception as error:
        body.update(stage='STOPPED_REPLAY_ERROR',error=str(error)); write_report(args.report,body); raise
    finally: repository.close()


if __name__=='__main__': main()
