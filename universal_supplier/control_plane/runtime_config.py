"""Explicit shared test-runtime credential; no inheritance from legacy serving DB."""
import json,os
from pathlib import Path
from universal_supplier.postgres import PostgresConfig


def runtime_config(path=None):
    root=Path(__file__).resolve().parents[2]
    path=Path(path or os.environ.get('STERBRUST_RUNTIME_CREDENTIALS') or
        root.parent/'sku-service-unified-rc-20261019/reports/integration/private-live11-runtime.json')
    value=json.loads(path.read_text(encoding='utf-8'))
    if (value.get('host'),value.get('port'),value.get('database'),value.get('role'))!=('127.0.0.1',55461,'rc_verify_vi_20261019','rc_runtime'):
        raise PermissionError('AUTOMATION_RUNTIME_CREDENTIAL_TARGET_MISMATCH')
    return PostgresConfig(value['host'],value['port'],value['database'],value['role'],value['password'],'disable')


def guarded_connect(config,**kwargs):
    """Reuse the exact identity guard on EACH UI/queue SQL session, before DML."""
    from .source_canary import validate_config,validate_connection
    validate_config(config)
    if config.user!='rc_runtime':raise PermissionError('AUTOMATION_RUNTIME_ROLE_REQUIRED')
    import psycopg
    from psycopg.rows import tuple_row
    kwargs.setdefault('connect_timeout',5)
    kwargs.setdefault('options','-c statement_timeout=60000 -c lock_timeout=15000')
    connection=psycopg.connect(**config.kwargs(),**kwargs)
    try:
        class Tuples:
            def cursor(self):return connection.cursor(row_factory=tuple_row)
        validate_connection(Tuples())
        with connection.cursor(row_factory=tuple_row) as q:
            q.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
            if q.fetchone()!=('rc_runtime',False,False):raise PermissionError('AUTOMATION_RUNTIME_ROLE_REQUIRED')
        return connection
    except BaseException:
        connection.close();raise
