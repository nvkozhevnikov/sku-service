"""Sequential minimum-detail enrichment of the pinned full listing catalog in RC."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import hashlib
import os
from pathlib import Path
import sys
import time
from urllib.robotparser import RobotFileParser

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ingest_intervesp_listing_archive import saved_pairs, digest, write_report
from scripts.run_full_supplier_qa import _preflight_rc
from scripts.probe_intervesp_detail_rate import DetailClient
from universal_supplier.http_capture import capture_public_html, CaptureStatus
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.intervesp_enrichment import enrich_saved_detail
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from universal_supplier.intervesp_final_proposals import remaining_detail_buckets
from universal_supplier.intervesp_selective_enrichment import post_matching_detail_plan

BASE=ROOT/'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01'


def verified_robots_text(body, expected_sha):
    # Earlier text-mode Windows archive added CR to line endings. Never accept
    # arbitrary edits: reconstruction must match the original response SHA.
    for candidate in (body, body.replace(b'\r\r\n', b'\r\n'), body.replace(b'\r\n', b'\n')):
        if hashlib.sha256(candidate).hexdigest() == expected_sha:
            return candidate.decode('utf-8', 'strict')
    raise RuntimeError('Robots evidence changed')


def restore_capture(value):
    fields=dict(value)
    fields['observed_at']=datetime.fromisoformat(fields['observed_at'])
    for key in ('redirects','diagnostics'): fields[key]=tuple(fields.get(key,()))
    return HttpCapture(**fields)


def reviewed_timeout_continuation(report):
    """Explicit, once-only continuation of independent URLs, never a retry.

    Any access status, CAPTCHA, unsafe redirect, DB error, earlier transport
    failure or second timeout remains STOP. The failed URL stays quarantined.
    """
    rows=report.get('rows', [])
    if (report.get('stage')!='STOPPED_HTTP_ACCESS_OR_ERROR' or report.get('timeout_continuation')
            or len(rows)<2 or rows[-1].get('status')!='NETWORK_ERROR'
            or rows[-1].get('http_status') is not None
            or rows[-1].get('diagnostics')!=['network_error:TimeoutError']
            or any(r.get('http_status')!=200 or r.get('status') not in ('PERSISTED_ENRICHMENT','REVIEW')
                   for r in rows[:-1])):
        raise RuntimeError('Only one isolated timeout after successful independent cards may be reviewed; STOP')
    result=json.loads(json.dumps(report))
    result['rows'][-1].update(status='REVIEW_HTTP_TIMEOUT',
        reason='No HTTP response; identity/detail completeness unverified; no retry or missing/deactivation inference')
    result.update(stage='RUNNING',timeout_continuation={
        'reviewed_at':datetime.now(timezone.utc).isoformat(),
        'method':'explicit_assessment_continue_independent_pinned_urls_no_retry',
        'failed_url_not_retried':rows[-1]['url']})
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply-write',action='store_true')
    parser.add_argument('--limit',type=int)
    parser.add_argument('--resume-after-isolated-timeout',action='store_true')
    parser.add_argument('--identity-critical-only', action='store_true')
    parser.add_argument('--resume-paused', action='store_true')
    parser.add_argument('--select-post-matching', type=Path)
    args=parser.parse_args()
    rows={row['product_url']:row for row,_,_ in saved_pairs()}
    assessment=json.loads((BASE/'OFFLINE_ASSESSMENT.json').read_text(encoding='utf-8'))
    plan=assessment['detail_get_plan']
    sample=json.loads((BASE/'RATE_SAMPLE/report.json').read_text(encoding='utf-8'))
    if sample['stage']!='BOUNDED_RATE_PASS': raise RuntimeError('Bounded rate test is not PASS')
    robots_path=BASE/'RATE_SAMPLE/robots.txt'
    robots=RobotFileParser(); robots.parse(verified_robots_text(robots_path.read_bytes(),sample['robots_sha256']).splitlines())
    pause=max(20,float(robots.crawl_delay('UniversalSupplierReadOnly/1.0') or 0))
    # The plan is fixed; no discovery and no implicit retry of terminal rows.
    report_path=BASE/'DETAIL_ENRICHMENT.json'
    report=json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {
        'started_at':datetime.now(timezone.utc).isoformat(),'stage':'RUNNING','rows':[],
        'plan_count':len(plan),'detail_gets':0,'reused_rate_captures':0,'pause_seconds':pause,
        'manifest_sha256':digest(ROOT.parent/'sku-service-full-discovery-rc1/full_discovery/FULL_MANIFEST_INTERVESP.txt'),
        'dry_run':not args.apply_write}
    stopped_copy=None
    selected_urls=None
    selective_proof=None
    if args.select_post_matching:
        if args.identity_critical_only or args.resume_paused or args.resume_after_isolated_timeout or args.limit:
            raise RuntimeError('Selective matching plan cannot be combined with other resume/limit modes')
        proposal=args.select_post_matching.resolve()
        if BASE.resolve() not in proposal.parents:
            raise RuntimeError('Selective plan must belong to this pinned local evidence directory')
        selective_proof=json.loads(proposal.read_text(encoding='utf-8'))
        if (report['stage']!='IDENTITY_ENRICHMENT_COMPLETE'
                or selective_proof.get('checkpoint_sha256')!=digest(report_path)
                or selective_proof.get('manifest_sha256')!=report['manifest_sha256']
                or selective_proof.get('matching_sha256')!=digest(proposal.parent/'MATCHING_FINAL.json')
                or selective_proof.get('diagnostic_only') is not True):
            raise RuntimeError('Selective matching/checkpoint SHA guard failed')
        selected_urls={r['url'] for r in selective_proof['selected_urls']}
        matching=json.loads((proposal.parent/'MATCHING_FINAL.json').read_text(encoding='utf-8'))
        recomputed=post_matching_detail_plan(matching['rows'],plan,report)
        fetched={r['url'] for r in report['rows']}
        if (not selected_urls or selected_urls.intersection(fetched)
                or len(selected_urls)!=selective_proof['selected_count']
                or not selected_urls.issubset({r['url'] for r in plan})
                or selected_urls!={r['url'] for r in recomputed['selected_urls']}):
            raise RuntimeError('Selected plan is empty, duplicate, fetched or differs from useful matching subset')
        report['stage']='RUNNING'
        report['run_scope']='post-matching-useful-subset'
        report.setdefault('post_matching_runs',[]).append({'plan':str(proposal),
            'sha256':digest(proposal),'selected_count':len(selected_urls),'http_retry':False})
    if report['stage'] == 'PAUSED_BY_OPERATOR_REPLAN':
        if not args.resume_paused or not args.identity_critical_only:
            raise RuntimeError('Operator-paused checkpoint requires explicit identity-only resume')
        report['stage'] = 'RUNNING'
        report['run_scope'] = 'identity-critical-only'
    elif args.resume_paused:
        raise RuntimeError('No approved paused checkpoint to resume')
    if report['stage'].startswith('STOPPED'):
        if not args.resume_after_isolated_timeout: raise RuntimeError('Stopped checkpoint requires review, no automatic retry')
        stopped_copy=report_path.with_name('DETAIL_STOPPED_ISOLATED_TIMEOUT.json')
        if stopped_copy.exists(): raise RuntimeError('Timeout evidence already archived; STOP')
        report=reviewed_timeout_continuation(report)
    if report['dry_run'] != (not args.apply_write): raise RuntimeError('Resume write mode changed')
    if report['manifest_sha256']!=digest(ROOT.parent/'sku-service-full-discovery-rc1/full_discovery/FULL_MANIFEST_INTERVESP.txt'):
        raise RuntimeError('Manifest changed')
    done={r['url'] for r in report['rows']}
    cached={r['url']:r for r in sample['rows'] if r['status']=='SUCCESS'}
    repository=None
    if args.apply_write:
        os.environ['PGPASSFILE']=str(Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/rc-local/pgpass.conf')
        repository=PostgresRepository(PostgresConfig('127.0.0.1',55449,'universal_supplier_server','rc_runtime',None,'disable'))
        _preflight_rc(repository)
        with repository.connection.cursor() as cursor:
            cursor.execute("SELECT pg_try_advisory_lock(hashtextextended('intervesp-listing-archive-job',0))")
            if not cursor.fetchone()[0]: raise RuntimeError('Another Intervesp archive/detail job is running')
    if stopped_copy:
        # Only after manifest, robots and fresh target preflight have passed.
        original=report_path.read_bytes()
        stopped_copy.write_bytes(original)
        report['timeout_continuation']['stopped_checkpoint_sha256']=hashlib.sha256(original).hexdigest()
        report['timeout_continuation']['stopped_checkpoint_path']=str(stopped_copy)
        write_report(report_path,report)
    try:
        pending=[item for item in plan if item['url'] not in done]
        if selected_urls is not None:
            pending=[item for item in pending if item['url'] in selected_urls]
        if args.identity_critical_only:
            pending=[item for item in pending if item['reason']=='identity']
        if args.limit: pending=pending[:args.limit]
        for item in pending:
            if (BASE/'PAUSE_REQUESTED').exists():
                report['stage']='PAUSED_BY_OPERATOR_REPLAN'
                break  # cooperative checkpoint: never interrupt GET/COMMIT
            url=item['url']
            if not robots.can_fetch('UniversalSupplierReadOnly/1.0',url): raise RuntimeError('Robots disallows pinned URL')
            if repository: _preflight_rc(repository)  # before source GET/write
            record={'url':url,'detail_reason':item['reason']}
            if url in cached:
                old=cached[url]; capture=restore_capture(old['capture'])
                evidence=BASE/'RATE_SAMPLE/evidence'/old['evidence_ref'].removeprefix('capture://')
                report['reused_rate_captures']+=1
                record.update(http_status=200,status='SUCCESS',reused_capture=True)
            else:
                time.sleep(pause)
                outcome=capture_public_html(url,evidence_dir=BASE/'DETAIL_EVIDENCE',client=DetailClient(),
                    timeout_seconds=20,max_attempts=1)
                report['detail_gets']+=1
                record.update(http_status=outcome.http_status,status=outcome.status.value,diagnostics=outcome.diagnostics)
                if outcome.status != CaptureStatus.SUCCESS:
                    report['rows'].append(record)
                    report['stage']='STOPPED_HTTP_ACCESS_OR_ERROR'
                    write_report(report_path,report)
                    break  # no retries, bypass or continuation after access/transport error
                capture=outcome.capture
                evidence=BASE/'DETAIL_EVIDENCE'/outcome.evidence_ref.removeprefix('capture://')
            if digest(evidence)!=capture.evidence_sha256: raise RuntimeError('Detail evidence SHA mismatch')
            record.update(evidence_ref=str(evidence),capture=asdict(capture))
            try:
                product=enrich_saved_detail(evidence.read_text(encoding='utf-8'),rows[url],source_url=url)
                record.update(status='ENRICHED',snapshot=asdict(product))
            except ValueError as error:
                record.update(status='REVIEW',reason=str(error))
            else:
                if repository:
                    result=repository.persist_commercial_observation(product,capture)
                    record.update(status='PERSISTED_ENRICHMENT',persistence={k:v for k,v in result.items() if k!='ingest_counts'})
            report['rows'].append(record)
            write_report(report_path,report)
            print(json.dumps({'processed':len(report['rows']),'plan':len(plan),'status':record['status'],'detail_gets':report['detail_gets']},ensure_ascii=True),flush=True)
        if len(report['rows'])==len(plan) and report['stage']=='RUNNING': report['stage']='DETAIL_ENRICHMENT_COMPLETE'
        elif args.identity_critical_only and report['stage']=='RUNNING':
            if not remaining_detail_buckets(plan,report)['identity-critical']:
                report['stage']='IDENTITY_ENRICHMENT_COMPLETE'
        elif selected_urls is not None and report['stage']=='RUNNING':
            report['stage']='IDENTITY_ENRICHMENT_COMPLETE'
        report['updated_at']=datetime.now(timezone.utc).isoformat()
        write_report(report_path,report)
    except Exception as error:
        report.update(stage='STOPPED_ERROR',error=str(error)); write_report(report_path,report); raise
    finally:
        if repository: repository.close()


if __name__=='__main__': main()
