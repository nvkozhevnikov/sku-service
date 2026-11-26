"""One-shot process submission; persistence, not the web process, owns status."""
from pathlib import Path
from datetime import datetime,timezone
import os,sys,json,uuid,subprocess,time

def process_identity(pid):
    if os.name!='nt':
        try:return {'pid':pid,'created':Path(f'/proc/{pid}/stat').read_text().split()[21],'executable':os.readlink(f'/proc/{pid}/exe')}
        except FileNotFoundError:return None
        except OSError:return {'pid':pid,'unknown':True}
    import ctypes
    from ctypes import wintypes
    k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.restype=wintypes.HANDLE
    k.CloseHandle.argtypes=[wintypes.HANDLE]
    k.GetProcessTimes.argtypes=[wintypes.HANDLE]+[ctypes.POINTER(wintypes.FILETIME)]*4
    k.GetExitCodeProcess.argtypes=[wintypes.HANDLE,ctypes.POINTER(wintypes.DWORD)]
    k.QueryFullProcessImageNameW.argtypes=[wintypes.HANDLE,wintypes.DWORD,wintypes.LPWSTR,ctypes.POINTER(wintypes.DWORD)]
    k.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)]
    h=k.OpenProcess(0x1000,False,pid)
    if not h:return None if ctypes.get_last_error()==87 else {'pid':pid,'unknown':True}
    try:
        exit_code=wintypes.DWORD()
        if k.GetExitCodeProcess(h,ctypes.byref(exit_code)) and exit_code.value!=259:return None
        times=[wintypes.FILETIME() for _ in range(4)];buf=ctypes.create_unicode_buffer(32768);size=wintypes.DWORD(len(buf))
        if not k.GetProcessTimes(h,*[ctypes.byref(t) for t in times]) or not k.QueryFullProcessImageNameW(h,0,buf,ctypes.byref(size)):return {'pid':pid,'unknown':True}
        in_job=wintypes.BOOL();job_known=bool(k.IsProcessInJob(h,None,ctypes.byref(in_job)))
        return {'pid':pid,'created':(times[0].dwHighDateTime<<32)+times[0].dwLowDateTime,'executable':buf.value,'in_job':bool(in_job.value) if job_known else None}
    finally:k.CloseHandle(h)

def liveness(identity):
    if not identity or identity.get('unknown'):return 'UNKNOWN'
    actual=process_identity(identity['pid'])
    if actual is None:return 'GONE'
    if actual.get('unknown'):return 'UNKNOWN'
    return 'ALIVE' if actual['created']==identity['created'] and os.path.normcase(actual['executable'])==os.path.normcase(identity['executable']) else 'PID_REUSED'

def load(path):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except FileNotFoundError:return None

def dispatch_state(runtime):return load(Path(runtime)/'worker-dispatch.json')

def pending_blocks(runtime,request_id=None):
    d=dispatch_state(runtime)
    if not d or d.get('request_id')==request_id:return False
    if d.get('status') not in ('STARTING','RUNNING'):return False
    return liveness(d.get('worker_identity') or d.get('launcher_identity')) in ('ALIVE','UNKNOWN')

def update_dispatch(runtime,request_id,result):
    from .execution import atomic_json
    d=dispatch_state(runtime)
    if d and d['request_id']==request_id:
        d.update({k:result[k] for k in ('status','run_id','origin','started_at','finished_at','worker_identity','errors','updated_at') if k in result})
        atomic_json(Path(runtime)/'worker-dispatch.json',d)

def submit(service,origin,selected_sources,max_pages):
    from .execution import settings,execution_lock,AlreadyRunning,atomic_json
    cfg=settings(service.schedule_config)
    if origin not in ('OPERATOR_UI','CLI_MANUAL','SCHEDULED'):raise ValueError('Invalid origin')
    if origin=='SCHEDULED' and not cfg['enabled']:return {'status':'CONFIG_ERROR','reason':'SCHEDULER_DISABLED','exit_code':4}
    selected=cfg['selected_sources'] if selected_sources is None else selected_sources
    cap=cfg['max_pages_per_source'] if max_pages is None else max_pages
    if not selected or len(selected)!=len(set(selected)) or not set(selected)<=set(cfg['selected_sources']) or type(cap) is not int or not 1<=cap<=cfg['max_pages_per_source']:raise ValueError('Invalid selection/bound')
    runtime=service.runtime
    try:
        with execution_lock(runtime):
            if pending_blocks(runtime):return {'status':'SKIP_ALREADY_RUNNING','exit_code':3}
            repo=service.repository()
            try:
                if repo.running_runs():return {'status':'START_BLOCKED_ORPHAN','reason':'PERSISTED_RUN_REQUIRES_REVIEW','exit_code':3}
                if not set(selected)<=repo.enabled_sources():raise ValueError('Disabled source selection')
            finally:repo.close()
            request_id=uuid.uuid4().hex
            request={'request_id':request_id,'origin':origin,'selected_sources':selected,'max_pages':cap,'dev_config':str(Path(service.dev_config).resolve()) if service.dev_config else None,'schedule_config':str(Path(service.schedule_config).resolve()) if service.schedule_config else None,'fixture_spec':str(service.fixture_spec) if service.fixture_spec else None,'runtime':str(runtime.resolve()),'submitted_at':datetime.now(timezone.utc).isoformat(),'status':'STARTING'}
            request_path=runtime/'requests'/f'{request_id}.json';request_path.parent.mkdir(exist_ok=True)
            atomic_json(request_path,request);atomic_json(runtime/'worker-dispatch.json',request)
            try:
                flags=(subprocess.CREATE_NO_WINDOW|subprocess.CREATE_BREAKAWAY_FROM_JOB) if os.name=='nt' else 0
                executable=sys.executable;env=os.environ.copy()
                if os.name=='nt':
                    # The Windows venv redirector creates its own Job Object.
                    # Launch the same base runtime directly, retaining the exact
                    # active environment's packages, with no redirector parent.
                    import site
                    executable=str(Path(sys.base_prefix)/'python.exe')
                    env['PYTHONPATH']=os.pathsep.join(site.getsitepackages()+([env['PYTHONPATH']] if env.get('PYTHONPATH') else []))
                with (runtime/'worker-process.log').open('ab') as output:
                    proc=subprocess.Popen([executable,'-m','competitor_intel.worker','--request',str(request_path.resolve())],cwd=Path(__file__).resolve().parents[1],stdin=subprocess.DEVNULL,stdout=output,stderr=output,env=env,close_fds=True,creationflags=flags,start_new_session=os.name!='nt')
                request['launcher_identity']=process_identity(proc.pid)
                atomic_json(runtime/'worker-dispatch.json',request)
            except Exception as error:
                request.update(status='FAILED',errors=[{'phase':'spawn','reason':type(error).__name__}]);atomic_json(runtime/'worker-dispatch.json',request)
                return {'status':'FAILED','reason':'WORKER_SPAWN_FAILED','request_id':request_id,'exit_code':2}
        # A short acknowledgement wait only; never wait for collection completion.
        end=time.monotonic()+1
        while time.monotonic()<end:
            current=dispatch_state(runtime)
            if current and current.get('request_id')==request_id and (current.get('run_id') or current['status']!='STARTING'):return current
            time.sleep(.025)
        return dispatch_state(runtime)
    except AlreadyRunning:return {'status':'SKIP_ALREADY_RUNNING','exit_code':3}
