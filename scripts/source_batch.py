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
    args = parser.parse_args()
    if args.action == 'matrix':
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
