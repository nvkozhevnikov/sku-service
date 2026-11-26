"""One Windows process host for the existing bounded runner; no task/restart loop."""
from pathlib import Path
import argparse,ctypes,json,os,sys,time,site
ROOT=Path(__file__).resolve().parents[1]
# The probe via the venv redirector had a JobObject; the original interpreter
# probe did not. Keep the same installed packages without that wrapper.
VENV_SITE=ROOT.parent/'sku-service/.venv-audit/Lib/site-packages'
if not VENV_SITE.is_dir():raise RuntimeError('EXISTING_RUNTIME_PACKAGES_NOT_FOUND')
site.addsitedir(str(VENV_SITE))
sys.path.remove(str(VENV_SITE));sys.path.insert(0,str(VENV_SITE))
sys.path.insert(0,str(ROOT))


def process_in_job():
    if os.name!='nt':raise RuntimeError('WINDOWS_HOST_REQUIRED')
    from ctypes import wintypes
    kernel=ctypes.WinDLL('kernel32',use_last_error=True)
    kernel.GetCurrentProcess.restype=wintypes.HANDLE
    kernel.IsProcessInJob.argtypes=[wintypes.HANDLE,wintypes.HANDLE,ctypes.POINTER(wintypes.BOOL)]
    kernel.IsProcessInJob.restype=wintypes.BOOL
    result=wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(),None,ctypes.byref(result)):
        raise ctypes.WinError(ctypes.get_last_error())
    return bool(result.value)


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',required=True,type=Path);p.add_argument('--probe',action='store_true')
    p.add_argument('--approval',type=Path);p.add_argument('--sha256');p.add_argument('--confirm')
    args=p.parse_args(argv)
    output=args.output.resolve()
    if not output.is_relative_to((ROOT/'reports/automation-launch').resolve()):raise PermissionError('HOST_OUTPUT_OUTSIDE_RUNTIME')
    from universal_supplier.control_plane.source_hourly import atomic_text
    from universal_supplier.control_plane.transport_diagnostics import exception_evidence
    output.mkdir(parents=True,exist_ok=False)
    def save(stage,**kw):
        from datetime import datetime,timezone
        atomic_text(output/'HOST.json',json.dumps({'stage':stage,'at':datetime.now(timezone.utc).isoformat(),
            'pid':os.getpid(),'parent_pid':os.getppid(),'interpreter':sys.executable,'packages':str(VENV_SITE),
            'no_automatic_restart':True,'no_task_or_cron':True,**kw}))
    try:
        in_job=process_in_job()
        save('PROBE_STARTED' if args.probe else 'STARTING',in_job=in_job)
        if in_job:raise PermissionError('INDEPENDENT_PROCESS_HOST_NOT_CONFIRMED')
        if args.probe:
            import psycopg
            time.sleep(5)
            save('PROBE_COMPLETE',in_job=False,HTTP=0,SQL=0,enqueue=0)
            return 0
        if any(os.environ.get(k) for k in ('STERBRUST_LIVE11_RECOVERY_FILE','STERBRUST_LIVE11_AUTONOMOUS_SESSION')):
            raise PermissionError('OLD_LIVE11_CONTEXT_PRESENT')
        from universal_supplier.control_plane.automation_execution import load
        if not args.approval or not args.sha256:raise PermissionError('APPROVAL_AND_SHA_REQUIRED')
        load(args.approval,args.sha256,args.confirm,for_execution=True)
        os.environ['PGCONNECT_TIMEOUT']='5'
        # Same CLI/session/queue/worker/hourly/keep-awake/STOP/restore, no new loop.
        from contextlib import redirect_stdout,redirect_stderr
        from supplier_system import main as run
        with (output/'startup.jsonl').open('x',encoding='utf-8',buffering=1) as log,redirect_stdout(log),redirect_stderr(log):
            status=run(['run','--approval',str(args.approval),'--sha256',args.sha256,'--confirm',args.confirm])
        save('FINISHED',in_job=False,exit_code=status)
        return status
    except BaseException as error:
        save('STOPPED',error=exception_evidence(error),no_retry=True)
        return 1


if __name__=='__main__':raise SystemExit(main())
