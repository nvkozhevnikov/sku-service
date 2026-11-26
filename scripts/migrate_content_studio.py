"""Explicit migration plan/apply. Defaults to READ ONLY; never starts Studio/VK/AI."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import psycopg
from content_studio.migration import read_source,import_snapshot,digest


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',type=Path,required=True);p.add_argument('--config',type=Path,required=True)
    p.add_argument('--data',type=Path,required=True);p.add_argument('--owners',type=Path)
    p.add_argument('--legacy-admin-only',action='store_true')
    p.add_argument('--expected-database',required=True);p.add_argument('--expected-system-identifier',required=True)
    p.add_argument('--apply',action='store_true');p.add_argument('--apply-schema',action='store_true')
    p.add_argument('--allow-primary',action='store_true');p.add_argument('--confirm-plan-sha256')
    p.add_argument('--report',type=Path,required=True)
    args=p.parse_args();cfg=json.loads(args.config.read_text(encoding='utf-8'))
    kwargs=dict(host=cfg['host'],port=int(cfg['port']),dbname=cfg.get('database',cfg.get('dbname')),
                user=cfg.get('role',cfg.get('user')),password=cfg.get('password'),sslmode=cfg.get('sslmode','disable'),connect_timeout=5)
    snapshot=read_source(args.source);owners=json.loads(args.owners.read_text(encoding='utf-8')) if args.owners else {}
    # Target and owner policy are part of the exact approval hash, not just a flag.
    plan={'source_sha256':snapshot['source_sha256'],'host':kwargs['host'],'port':kwargs['port'],
          'database':args.expected_database,'system_identifier':args.expected_system_identifier,
          'owners':owners,'legacy_admin_only':args.legacy_admin_only,'data':str(args.data.resolve()),'apply_schema':args.apply_schema}
    plan_hash=digest(plan)
    if kwargs['dbname']!=args.expected_database:raise ValueError('TARGET_DATABASE_CONFIG_MISMATCH')
    primary=kwargs['dbname']=='rc_verify_vi_20261019'
    if not primary and not kwargs['dbname'].startswith('studio_integration_qa_'):raise ValueError('ONLY_EXPLICIT_ISOLATED_QA_OR_PRIMARY_TARGET_ALLOWED')
    if args.apply and (args.confirm_plan_sha256!=plan_hash or primary and not args.allow_primary):raise ValueError('EXACT_PLAN_APPROVAL_REQUIRED')
    with psycopg.connect(**kwargs,autocommit=True,options='-c statement_timeout=180000 -c lock_timeout=10000') as c:
        if args.apply:
            actual=c.execute("SELECT current_database(),(pg_control_system()).system_identifier::text,current_setting('server_version')").fetchone()
        else:
            with c.transaction():
                c.execute('SET TRANSACTION READ ONLY')
                actual=c.execute("SELECT current_database(),(pg_control_system()).system_identifier::text,current_setting('server_version')").fetchone()
        if actual[:2]!=(args.expected_database,args.expected_system_identifier) or not actual[2].startswith('17.11'):raise ValueError('TARGET_DATABASE_IDENTITY_MISMATCH')
        result=import_snapshot(c,snapshot,args.data,owners,legacy_admin_only=args.legacy_admin_only,apply_schema=args.apply_schema) if args.apply else {'status':'PLAN_ONLY_NO_WRITES','tables':snapshot['manifest']['tables'],'media_files':len(snapshot['media'])}
    result.update(plan=plan,approval_sha256=plan_hash,primary_applied=bool(args.apply and primary))
    args.report.parent.mkdir(parents=True,exist_ok=True);args.report.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'status':result['status'],'approval_sha256':plan_hash,'primary_applied':result['primary_applied']}))


if __name__=='__main__':
    try:main()
    except Exception as exc:
        # Driver exceptions may contain DSNs/connection details; never echo them.
        message=str(exc) if type(exc) is ValueError and str(exc).isupper() and len(str(exc))<120 else type(exc).__name__
        print('MIGRATION_STOPPED: '+message,file=sys.stderr);raise SystemExit(1)
