"""Local CLI for the existing scheduler/UI/XML. Default commands do not run LIVE."""
from pathlib import Path
from datetime import datetime,timezone
import argparse,hashlib,json,sys,os
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from universal_supplier.control_plane.runtime_config import runtime_config,guarded_connect
from universal_supplier.control_plane import automation_execution as execution


def main(argv=None):
    p=argparse.ArgumentParser(description='Supplier system: prepare/inspect are read-only; run needs exact approval')
    p.add_argument('action',choices=('prepare','inspect','run','export-preview','stop'))
    p.add_argument('--approval',type=Path);p.add_argument('--sha256');p.add_argument('--confirm')
    p.add_argument('--limits',type=Path);p.add_argument('--not-before');p.add_argument('--execution-until')
    p.add_argument('--confirmed-commercial-targets',type=Path)
    p.add_argument('--continue-approval',type=Path);p.add_argument('--previous-sha256')
    p.add_argument('--reuse-receipts',action='store_true',help='SELECT-only successor for a partially executed commercial grant; never replay COMMITTED/quarantine')
    p.add_argument('--output',type=Path);p.add_argument('--session',type=Path)
    p.add_argument('--resume-safe',action='store_true',help='explicit native safe resume within the SAME approved window; never UNKNOWN')
    args=p.parse_args(argv)
    cfg=runtime_config() if args.action!='stop' else None
    if args.action=='prepare':
        if args.reuse_receipts:
            if not all((args.continue_approval,args.previous_sha256,args.output)):p.error('receipt reuse requires previous approval/SHA and new output')
            if any((args.limits,args.confirmed_commercial_targets)):p.error('receipt reuse retains original scope; no replacement targets/limits')
            if bool(args.not_before)!=bool(args.execution_until):p.error('explicit new window requires both not-before and execution-until')
        elif not all((args.limits or args.confirmed_commercial_targets,args.not_before,args.execution_until,args.output)):p.error('prepare requires limits or commercial targets, window/output')
        if args.output.exists():raise FileExistsError('APPROVAL_OUTPUT_ALREADY_EXISTS')
        if args.reuse_receipts:
            from universal_supplier.control_plane.commercial_continuation import prepare_receipts
            value=prepare_receipts(cfg,args.continue_approval,args.previous_sha256,
                not_before=args.not_before,execution_until=args.execution_until)
        elif args.confirmed_commercial_targets:
            from universal_supplier.control_plane.commercial_refresh import prepare
            value=prepare(cfg,args.confirmed_commercial_targets,args.not_before,args.execution_until)
            if args.continue_approval:
                if not args.previous_sha256:p.error('continuation requires exact previous-sha256')
                from universal_supplier.control_plane.commercial_continuation import attach
                value=attach(cfg,value,args.continue_approval,args.previous_sha256)
        else:value=execution.prepare(cfg,json.loads(args.limits.read_text(encoding='utf-8')),args.not_before,args.execution_until)
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'approval':str(args.output),'sha256':hashlib.sha256(args.output.read_bytes()).hexdigest(),
                          'execution':'NOT_APPROVED','HTTP':0,'enqueue':0}))
    elif args.action in ('inspect','run'):
        if not args.approval or not args.sha256:p.error('approval/sha256 required')
        value=execution.load(args.approval,args.sha256,args.confirm,for_execution=args.action=='run')
        if args.action=='inspect':
            import psycopg
            from universal_supplier.control_plane.source_canary import validate_config,validate_connection
            validate_config(cfg)
            with psycopg.connect(**cfg.kwargs(),autocommit=True,options='-c default_transaction_read_only=on') as c:
                validate_connection(c)
                active=c.execute("SELECT id,status FROM crawl_jobs WHERE status IN ('queued','running') ORDER BY id").fetchall()
                phases=c.execute('''SELECT DISTINCT ON (supplier_id,settings_snapshot->'source_batch'->>'phase') status
                    FROM crawl_jobs WHERE settings_snapshot->'source_batch'->>'id'=%s
                    ORDER BY supplier_id,settings_snapshot->'source_batch'->>'phase',id DESC''',(value['protected_live11_batch_id'],)).fetchall()
            from universal_supplier.control_plane.commercial_refresh import is_refresh,scope_rows
            from universal_supplier.control_plane.commercial_continuation import validate_live,quarantine_ids
            validate_live(cfg,value)
            if is_refresh(value):scope_rows(cfg,[e for r in value['suppliers'].values() for e in r['targets']],accounted_quarantine=quarantine_ids(value))
            print(json.dumps({'head':value['head'],'sources':len(value['suppliers']),'active_jobs':active,
                'window_end':value['execution_until'],'live_blocked':None if len(phases)==22 and all(r[0]=='succeeded' for r in phases)
                    or is_refresh(value) else 'AUTOMATION_LIVE11_UNFINISHED_REQUIRES_SEPARATE_RECOVERY','HTTP':0,'enqueue':0,'matching':0,
                    'operator_confirmation_required':True,'purpose':value.get('purpose')}))
        else:
            from universal_supplier.control_plane.scheduler_main import run_approved
            from contextlib import redirect_stdout,redirect_stderr
            from uuid import uuid4
            folder=ROOT/'reports/automation-runtime'/str(uuid4());folder.mkdir(parents=True)
            print(json.dumps({'session':str(folder),'log':str(folder/'runner.log'),'stop_uses_this_session':True}),flush=True)
            with (folder/'runner.log').open('a',encoding='utf-8',buffering=1) as log,redirect_stdout(log),redirect_stderr(log):
                result=run_approved(cfg,args.approval,args.sha256,args.confirm,folder=folder,resume_safe=args.resume_safe)
                print(json.dumps(result,default=str))
            return 0 if result['reason'] in ('OPERATOR_STOP','AUTOMATION_EXECUTION_WINDOW_EXPIRED','COMMERCIAL_REFRESH_FINISHED') else 1
    elif args.action=='export-preview':
        from universal_supplier.control_plane.store import PostgresControlPlaneStore
        from universal_supplier.xml_export import XmlExportService
        if not args.output:p.error('export-preview requires a local output directory')
        report=XmlExportService(PostgresControlPlaneStore(cfg,connection_factory=guarded_connect)).publish_previews(args.output)
        print(json.dumps({'version':report['version'],'counts':report['counts'],'checksums':report['checksums'],'import_allowed':False}))
    elif args.action=='stop':
        if not args.session:
            if not args.sha256 or len(args.sha256)!=64:p.error('stop requires --session or exact --sha256')
            found=[]
            for receipt in (ROOT/'reports/automation-runtime').glob('*/SESSION.json'):
                value=json.loads(receipt.read_text(encoding='utf-8'))
                if value.get('sha256')==args.sha256 and not (receipt.parent/'FINAL.json').exists() and not (receipt.parent/'FINAL_UNVERIFIED.json').exists():
                    (receipt.parent/'STOP_REQUESTED').write_text('OPERATOR_STOP',encoding='utf-8');found.append(str(receipt.parent))
            print(json.dumps({'STOP_REQUESTED':found,'note':'wait for FINAL and SETTINGS_RESTORED; no forced termination'}))
            return 0
        target=args.session.resolve();allowed=(ROOT/'reports/automation-runtime').resolve()
        if not target.is_relative_to(allowed):raise PermissionError('AUTOMATION_SESSION_PATH_NOT_ALLOWED')
        if not (target/'SESSION.json').is_file():raise ValueError('AUTOMATION_SESSION_NOT_FOUND')
        (target/'STOP_REQUESTED').write_text('OPERATOR_STOP',encoding='utf-8')
        print('STOP_REQUESTED; wait for FINAL and verified zero active jobs')
    return 0


if __name__=='__main__':
    try:raise SystemExit(main())
    except Exception as error:
        message=str(error)
        print(message if message.startswith(('AUTOMATION_','PREVIEW_','APPROVAL_')) else type(error).__name__,file=sys.stderr)
        raise SystemExit(1)
