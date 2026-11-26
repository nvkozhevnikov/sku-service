"""Explicit offline recovery. Never starts a collection, timer or transport."""
from copy import deepcopy
from datetime import datetime,timezone
from pathlib import Path
import ctypes,json,os
from .execution import execution_lock,atomic_json
from .store import json_value
from psycopg.types.json import Jsonb

def process_alive(pid):
    if not isinstance(pid,int) or pid<=0:raise ValueError('INVALID_WORKER_PID')
    if os.name=='nt':
        from ctypes import wintypes
        dll=ctypes.WinDLL('kernel32',use_last_error=True)
        dll.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD];dll.OpenProcess.restype=wintypes.HANDLE
        dll.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)];dll.CloseHandle.argtypes=[wintypes.HANDLE]
        handle=dll.OpenProcess(0x1000,False,pid)
        if not handle:
            if ctypes.get_last_error()==87:return False
            raise RuntimeError('PROCESS_LIVENESS_UNKNOWN')
        try:
            code=wintypes.DWORD()
            if not dll.GetExitCodeProcess(handle,ctypes.byref(code)):raise RuntimeError('PROCESS_LIVENESS_UNKNOWN')
            return code.value==259
        finally:dll.CloseHandle(handle)
    if Path('/proc').exists():return Path('/proc',str(pid)).exists()
    raise RuntimeError('PROCESS_LIVENESS_UNSUPPORTED')

def recovered_result(row,journal,source_counts,finished_at):
    if row['status']!='RUNNING' or row['finished_at'] is not None:raise ValueError('NOT_ORPHAN_RUNNING')
    if journal.get('run_id')!=row['id'] or journal.get('pid')!=(row.get('result') or {}).get('pid'):raise ValueError('JOURNAL_IDENTITY_MISMATCH')
    result=deepcopy(journal);result.update(status='PARTIAL',exit_code=1,finished_at=finished_at.isoformat())
    result['sources']=deepcopy(journal.get('sources',{}))
    for code,count in source_counts.items():
        if code not in result['sources']:
            result['sources'][code]={'status':'PARTIAL','execution_state':'INTERRUPTED','reason':'WORKER_PROCESS_GONE',
                'stored':count['observations'],'accepted_pages':count['pages'],'versions':count['new_versions'],
                'HTTP_GETs':None,'scope_complete':False}
    # JSON outcomes have no closed status enum; these are descriptive states,
    # not new competitor_runs database status values or fabricated failures.
    for code in ('titaniumgroup','youmet'):
        if code not in result['sources']:result['sources'][code]={'status':'NOT_REACHED','execution_state':'NOT_EXECUTED','stored':0,'reason':'RUN_INTERRUPTED_BEFORE_SOURCE','scope_complete':False}
    result['errors']=deepcopy(journal.get('errors',[]))+[{'phase':'recovery','reason':'WORKER_PROCESS_GONE','source':'stanki_ru'}]
    latest=max((c['latest_observation'] for c in source_counts.values()),default=None)
    result['recovery']={'kind':'ORPHAN_RECOVERY','worker_pid':journal['pid'],'worker_exit_time':None,
        'last_persisted_activity':latest,'recovered_at':finished_at.isoformat(),'partial_committed_data_retained':True,
        'root_cause':'SUSPECTED_UI_PROCESS_RESTART','ui_process_coupling':'REPOSITORY_VERIFIED','new_collection':False,
        'coverage_complete':False,'selected_sources_exact':'NOT_RECORDED_IN_ORIGINAL_RUN'}
    return json_value(result)

def recover_orphan(store,runtime,run_id,*,snapshot,counts,alive=process_alive):
    """Caller snapshots evidence under the shared lock before metadata mutation.

    Repeating recovery returns the exact persisted result/finished time; no DML.
    Existing recovered DB result repairs missing journal projection only.
    """
    runtime=Path(runtime);lock=runtime/'collection.lock'
    if not lock.is_file() or lock.stat().st_size!=1:raise ValueError('EXISTING_LOCK_REQUIRED')
    with execution_lock(runtime):
        with store.conn.transaction(),store.conn.cursor() as c:
            c.execute('SELECT * FROM competitor_runs WHERE id=%s FOR UPDATE',(run_id,));row=c.fetchone()
            if not row:raise ValueError('RUN_NOT_FOUND')
            journal=json.loads((runtime/'last-run.json').read_text(encoding='utf-8'))
            existing=(row.get('result') or {}).get('recovery')
            if existing and existing.get('kind')=='ORPHAN_RECOVERY':
                result=row['result'];changed=False
            else:
                if alive((row.get('result') or {}).get('pid')):raise ValueError('WORKER_STILL_ALIVE')
                snapshot(row,journal)
                c.execute('SELECT clock_timestamp() recovered_at');when=c.fetchone()['recovered_at']
                result=recovered_result(row,journal,counts,when)
                c.execute('UPDATE competitor_runs SET status=%s,finished_at=%s,result=%s WHERE id=%s AND status=%s AND finished_at IS NULL RETURNING id',
                    ('PARTIAL',when,Jsonb(result),run_id,'RUNNING'))
                if not c.fetchone():raise RuntimeError('RUN_RECOVERY_CONFLICT')
                changed=True
        for path in (runtime/'last-run.json',runtime/'manual-last.json'):
            old=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
            if path.name=='last-run.json' and old and old.get('run_id')!=run_id:raise ValueError('OTHER_RUN_JOURNAL_PRESENT')
            if old!=result:atomic_json(path,result)
        return {'changed':changed,'result':result}
