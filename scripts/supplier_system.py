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
    p.add_argument('--output',type=Path);p.add_argument('--session',type=Path)
    args=p.parse_args(argv)
    cfg=runtime_config() if args.action!='stop' else None
    if args.action=='prepare':
        if not all((args.limits,args.not_before,args.execution_until,args.output)):p.error('prepare requires limits/window/output')
        if args.output.exists():raise FileExistsError('APPROVAL_OUTPUT_ALREADY_EXISTS')
        value=execution.prepare(cfg,json.loads(args.limits.read_text(encoding='utf-8')),args.not_before,args.execution_until)
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
            print(json.dumps({'head':value['head'],'sources':len(value['suppliers']),'active_jobs':active,
                'window_end':value['execution_until'],'live_blocked':None if len(phases)==22 and all(r[0]=='succeeded' for r in phases)
                    else 'AUTOMATION_LIVE11_UNFINISHED_REQUIRES_SEPARATE_RECOVERY','HTTP':0,'enqueue':0,'matching':0}))
        else:
            from universal_supplier.control_plane.scheduler_main import run_approved
            from contextlib import redirect_stdout,redirect_stderr
            from uuid import uuid4
            folder=ROOT/'reports/automation-runtime'/str(uuid4());folder.mkdir(parents=True)
            with (folder/'runner.log').open('a',encoding='utf-8',buffering=1) as log,redirect_stdout(log),redirect_stderr(log):
                result=run_approved(cfg,args.approval,args.sha256,args.confirm,folder=folder)
                print(json.dumps(result,default=str))
            return 0 if result['reason'] in ('OPERATOR_STOP','AUTOMATION_EXECUTION_WINDOW_EXPIRED') else 1
    elif args.action=='export-preview':
        from universal_supplier.control_plane.store import PostgresControlPlaneStore
        from universal_supplier.xml_export import XmlExportService
        if not args.output:p.error('export-preview requires a local output directory')
        report=XmlExportService(PostgresControlPlaneStore(cfg,connection_factory=guarded_connect)).publish_previews(args.output)
        print(json.dumps({'version':report['version'],'counts':report['counts'],'checksums':report['checksums'],'import_allowed':False}))
    elif args.action=='stop':
        if not args.session:p.error('stop requires --session directory')
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
