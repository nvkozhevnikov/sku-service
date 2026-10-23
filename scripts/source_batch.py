"""Operator entry point. Nothing is enqueued by preview, matrix or status."""
from pathlib import Path
import argparse
import json
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from universal_supplier.control_plane import source_batch
from universal_supplier.control_plane.queue import PostgresJobQueue
from universal_supplier.postgres import PostgresConfig


def main():
    parser = argparse.ArgumentParser(description='LIVE11: один пакет, существующая очередь')
    sub = parser.add_subparsers(dest='action', required=True)
    sub.add_parser('matrix', help='offline: показать сохранённую LIVE11 матрицу')
    sub.add_parser('preview', help='READ ONLY: exact target и 22 immutable scopes')
    enqueue = sub.add_parser('enqueue', help='WRITE: только после отдельного разрешения LIVE11')
    enqueue.add_argument('--approval', required=True, type=Path,
        help='JSON: confirmation_sha256,batch_id,operator,approved_at,started_at (original T0),head')
    for command in ('status','stop','resume'):
        child = sub.add_parser(command)
        child.add_argument('--batch-id', required=True)
    gate = sub.add_parser('confirm-beka', help='WRITE: terminal isolation receipt внутри согласованного пакета')
    gate.add_argument('--batch-id', required=True)
    gate.add_argument('--receipt', required=True, type=Path)
    gate.add_argument('--sha256', required=True)
    recovery=sub.add_parser('prepare-recovery',help='READ ONLY: frozen same-batch compatibility approval')
    recovery.add_argument('--batch-id',required=True)
    recovery.add_argument('--evidence',required=True,type=Path)
    recovery.add_argument('--output',required=True,type=Path)
    continuation=sub.add_parser('prepare-continuation',help='READ ONLY: bounded current-batch continuation and quarantine approval')
    continuation.add_argument('--previous',required=True,type=Path)
    continuation.add_argument('--previous-sha256',required=True)
    continuation.add_argument('--quarantine-url',action='append',default=[])
    continuation.add_argument('--scope-quarantine-decision',type=Path,
        help='Explicit operator decision JSON; SELECT proof only, no checkpoint mutation')
    continuation.add_argument('--output',required=True,type=Path)
    recovery=sub.add_parser('recover',help='WRITE only after explicit same-batch recovery approval')
    recovery.add_argument('--approval',required=True,type=Path)
    recovery.add_argument('--sha256',required=True)
    recovery.add_argument('--confirm',required=True)
    recovery.add_argument('--run',action='store_true',help='Explicit operator session: existing worker until terminal/isolation gate')
    recovery.add_argument('--activate-approved-settings',action='store_true',help='Temporarily activate exact approval matrix; restore after worker shutdown')
    args = parser.parse_args()
    if args.action in ('prepare-recovery','prepare-continuation','recover'):
        from cabinet_batch_read import target_config,command
        from universal_supplier.control_plane.source_recovery import prepare,file_sha,ENV,require
        import os
        if args.action in ('prepare-recovery','prepare-continuation'):
            if args.action=='prepare-continuation':
                from universal_supplier.control_plane.source_recovery import prepare_continuation
                decisions=[json.loads(args.scope_quarantine_decision.read_text(encoding='utf-8'))] if args.scope_quarantine_decision else []
                result=prepare_continuation(PostgresJobQueue(target_config()),args.previous,args.previous_sha256,args.quarantine_url,decisions)
            else:result=prepare(PostgresJobQueue(target_config()),args.evidence,args.batch_id)
            require(not args.output.exists(),'RECOVERY_OUTPUT_ALREADY_EXISTS')
            args.output.parent.mkdir(parents=True,exist_ok=True)
            args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
            result={'approval_file':str(args.output.resolve()),'sha256':file_sha(args.output),'activated':False}
        else:
            require(args.confirm==args.sha256 and file_sha(args.approval)==args.sha256,'RECOVERY_OPERATOR_CONFIRMATION_REQUIRED')
            os.environ[ENV+'_FILE']=str(args.approval.resolve())
            os.environ[ENV+'_SHA256']=args.sha256
            os.environ[ENV+'_CONFIRM']=args.confirm
            if args.run:
                require(args.activate_approved_settings,'RECOVERY_EXPLICIT_TEMPORARY_ACTIVATION_REQUIRED')
                from live11_recovery_session import run
                result=run(PostgresJobQueue(target_config()))
            else:
                require(not args.activate_approved_settings,'RECOVERY_ACTIVATION_REQUIRES_RUN')
                result=command('recover',{'approval_sha256':args.sha256})
    elif args.action == 'matrix':
        result = source_batch.matrix_rows()
    else:
        queue = PostgresJobQueue(PostgresConfig.from_env())
        if args.action == 'preview':
            result = queue.preview_source_batch()
        elif args.action == 'enqueue':
            result = queue.enqueue_source_batch(**json.loads(args.approval.read_text(encoding='utf-8')))
        elif args.action == 'status':
            result = queue.source_batch_status(args.batch_id)
        elif args.action == 'stop':
            queue.stop_source_batch(args.batch_id)
            result = queue.source_batch_status(args.batch_id)
        elif args.action == 'confirm-beka':
            result = source_batch.confirm_beka_gate(queue,args.batch_id,args.receipt,args.sha256)
        else:
            result = source_batch.resume_batch(queue, args.batch_id)
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


if __name__ == '__main__':
    main()
