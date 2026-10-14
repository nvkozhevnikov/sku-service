"""Explicit isolated zero-network fixture adapters for OS lifetime acceptance."""
from pathlib import Path
import json,time,socket
from copy import deepcopy
from competitor_intel.execution import atomic_json

class FixtureRepository:
    def __init__(self,spec):
        self.path=Path(spec);self.spec=json.loads(self.path.read_text(encoding='utf-8'))
        if self.spec.get('mode')!='ISOLATED_FIXTURE_ONLY':raise ValueError('Fixture mode required')
        self.file=self.path.parent/'fixture-runs.json'
    def records(self):return json.loads(self.file.read_text(encoding='utf-8')) if self.file.exists() else []
    def start(self,since,result):
        rows=self.records();run=len(rows)+1;rows.append({'id':run,**deepcopy(result)});atomic_json(self.file,rows);return run
    def finish(self,run,result):
        rows=self.records();rows[run-1]={'id':run,**deepcopy(result)};atomic_json(self.file,rows)
    def enabled_sources(self):return {'metalmaster'}
    def running_runs(self):return [r for r in self.records() if r['status']=='RUNNING']
    def close(self):pass

def fixture_collector(repo,source,run,since,output,cap,deadline):
    # This process is never allowed external networking, even by accident.
    def denied(*a,**kw):raise AssertionError('FIXTURE_NETWORK_FORBIDDEN')
    socket.create_connection=denied;socket.socket.connect=denied
    atomic_json(repo.path.parent/'fixture-entered.json',{'run_id':run,'source':source})
    if repo.spec.get('fail'):raise RuntimeError('CONTROLLED_FIXTURE_FAILURE')
    while not (repo.path.parent/'release-fixture').exists():
        if time.monotonic()>=deadline:raise TimeoutError('FIXTURE_DEADLINE')
        time.sleep(.05)
    return {'status':'COMPLETED','stored':0,'HTTP_GETs':0,'fixture_only':True}

def fixture_rebuild(repo,path,cfg):
    if repo.spec.get('report_fail'):raise RuntimeError('CONTROLLED_REPORT_FAILURE')
    path.mkdir(parents=True,exist_ok=True);(path/'index.html').write_text('Fixture report; no business data',encoding='utf-8')
    return {'HTTP_GETs':0,'fixture_only':True}
