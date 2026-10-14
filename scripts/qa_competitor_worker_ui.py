"""Actual isolated Uvicorn process, actual existing UI POST route, no real DB."""
import argparse,os
from pathlib import Path
os.environ['CONTROL_PLANE_SKIP_DEFAULT_APP']='YES'
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates
from universal_supplier.control_plane.competitor_ui import register_monitoring
from competitor_intel.execution import CollectionService

def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',type=Path,required=True);p.add_argument('--port',type=int,required=True);args=p.parse_args()
    service=CollectionService(None,args.runtime/'schedule.json',args.runtime,fixture_spec=args.runtime/'fixture.json')
    app=FastAPI();app.state.local_operator_collection=True
    @app.get('/qa-identity')
    def identity():
        members=None
        if os.name=='nt':
            import ctypes
            class JobPids(ctypes.Structure):
                _fields_=[('assigned',ctypes.c_uint32),('count',ctypes.c_uint32),('pids',ctypes.c_size_t*1024)]
            data=JobPids();k=ctypes.WinDLL('kernel32',use_last_error=True)
            if k.QueryInformationJobObject(None,3,ctypes.byref(data),ctypes.sizeof(data),None):members=list(data.pids)[:data.count]
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
