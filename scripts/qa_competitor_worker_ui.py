"""Actual isolated Uvicorn process, actual existing UI POST route, no real DB."""
import argparse,os
from pathlib import Path
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from universal_supplier.control_plane.competitor_ui import register_monitoring
from competitor_intel.execution import CollectionService

def owned_windows_job():
    """Create the UI-owned job being tested instead of assuming a venv redirector.

    A directly installed Python runtime has no implicit UI Job Object. Keep the
    handle alive until this isolated UI exits; an un-detached worker would die.
    """
    if os.name!='nt':return None
    import ctypes
    from ctypes import wintypes as w
    class Basic(ctypes.Structure):
        _fields_=[('process_time',ctypes.c_int64),('job_time',ctypes.c_int64),('flags',w.DWORD),
                  ('min_ws',ctypes.c_size_t),('max_ws',ctypes.c_size_t),('active',w.DWORD),
                  ('affinity',ctypes.c_size_t),('priority',w.DWORD),('scheduling',w.DWORD)]
    class Io(ctypes.Structure):
        _fields_=[(name,ctypes.c_uint64) for name in ('read_ops','write_ops','other_ops','read_bytes','write_bytes','other_bytes')]
    class Extended(ctypes.Structure):
        _fields_=[('basic',Basic),('io',Io),('process_memory',ctypes.c_size_t),('job_memory',ctypes.c_size_t),
                  ('peak_process',ctypes.c_size_t),('peak_job',ctypes.c_size_t)]
    k=ctypes.WinDLL('kernel32',use_last_error=True)
    k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR];k.CreateJobObjectW.restype=w.HANDLE
    k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
    k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
    k.GetCurrentProcess.restype=w.HANDLE
    handle=k.CreateJobObjectW(None,None);limits=Extended()
    limits.basic.flags=0x2000|0x0800 # KILL_ON_JOB_CLOSE | BREAKAWAY_OK
    if not handle or not k.SetInformationJobObject(handle,9,ctypes.byref(limits),ctypes.sizeof(limits)) or not k.AssignProcessToJobObject(handle,k.GetCurrentProcess()):
        raise ctypes.WinError(ctypes.get_last_error())
    return handle

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--port',type=int,required=True);args=p.parse_args()
    service=CollectionService(None,args.runtime/'schedule.json',args.runtime,fixture_spec=args.runtime/'fixture.json')
    app=FastAPI();app.state.local_operator_collection=True
    app.state.qa_ui_job=owned_windows_job()
    @app.get('/qa-identity')
    def identity():
        members=None
        if os.name=='nt':
            import ctypes
            class JobPids(ctypes.Structure):
                _fields_=[('assigned',ctypes.c_uint32),('count',ctypes.c_uint32),('pids',ctypes.c_size_t*1024)]
            data=JobPids();k=ctypes.WinDLL('kernel32',use_last_error=True)
            k.QueryInformationJobObject.argtypes=[ctypes.c_void_p,ctypes.c_int,ctypes.c_void_p,ctypes.c_uint32,ctypes.c_void_p]
            if k.QueryInformationJobObject(app.state.qa_ui_job,3,ctypes.byref(data),ctypes.sizeof(data),None):members=list(data.pids)[:data.count]
        return {'ui_pid':os.getpid(),'ui_job_members':members,'fixture_only':True}
    @app.get('/qa')
    def fixture_page():
        from fastapi.responses import HTMLResponse
        return HTMLResponse('<button id="start">Start fixture collection</button><pre id="result"></pre><script>document.querySelector("#start").onclick=async()=>{let r=await fetch("/competitor-monitoring/runs",{method:"POST",headers:{"Content-Type":"application/x-www-form-urlencoded","Accept":"application/json"},body:"source=metalmaster&max_pages=1"});document.querySelector("#result").textContent=JSON.stringify(await r.json())}</script>')
    class ReadProjection:
        def view(self,*a):return {'rows':[],'total':0,'page':1,'pages':1}
    register_monitoring(app,Jinja2Templates(directory=str(Path(__file__).resolve().parents[1]/'universal_supplier/control_plane/templates')),lambda request,**kw:{'request':request,**kw},lambda *a,**kw:None,ReadProjection(),service)
    import uvicorn
    uvicorn.run(app,host='127.0.0.1',port=args.port)

if __name__=='__main__':main()
