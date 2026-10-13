"""One synchronous use-case shared by UI and CLI. No timer/daemon installation."""
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import json, logging, os, time, uuid
from logging.handlers import RotatingFileHandler

SOURCES={'kuvalda_nnov','metalmaster','jet_online','stanki_ru','titaniumgroup','youmet'}
ORIGINS={'OPERATOR_UI','CLI_MANUAL','SCHEDULED'}
EXIT_CODES={'COMPLETED':0,'PARTIAL':1,'FAILED':2,'SKIP_ALREADY_RUNNING':3,'CONFIG_ERROR':4}
ROOT=Path(__file__).resolve().parents[1]

def settings(path=None):
    cfg=json.loads(Path(path or Path(__file__).with_name('schedule.json')).read_text(encoding='utf-8'))
    if type(cfg.get('enabled')) is not bool or cfg.get('skip_if_running') is not True:raise ValueError('Unsafe scheduler settings')
    ZoneInfo(cfg['timezone'])
    datetime.strptime(cfg['time_of_day'],'%H:%M')
    codes=cfg.get('selected_sources')
    if not isinstance(codes,list) or not codes or len(codes)!=len(set(codes)) or not set(codes)<=SOURCES:raise ValueError('Invalid source selection')
    for key,low,high in [('max_runtime',10,7200),('max_pages_per_source',1,100),('log_max_bytes',4096,10485760),('log_backups',1,10)]:
        if type(cfg.get(key)) is not int or not low<=cfg[key]<=high:raise ValueError('Invalid bound: '+key)
    if cfg.get('failure_policy')!='CONTINUE_OTHER_SOURCES_NO_RUN_RETRY':raise ValueError('Unsupported failure policy')
    date.fromisoformat(cfg['since'])
    return cfg

class AlreadyRunning(Exception):pass

@contextmanager
def execution_lock(directory):
    """Kernel-owned lock, released on close or crash; never delete a stale lock."""
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    f=(directory/'collection.lock').open('a+b')
    try:
        if f.seek(0,2)==0:f.write(b'0');f.flush()
        f.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:raise AlreadyRunning() from None
        try:yield
        finally:
            f.seek(0)
            if os.name=='nt':msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
            else:fcntl.flock(f,fcntl.LOCK_UN)
    finally:f.close()

def atomic_json(path,value):
    path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),encoding='utf-8');os.replace(tmp,path)

def run_competitor_collection(repository,collector,rebuild,*,origin,config,runtime,selected_sources=None,max_pages=None,clock=time.monotonic):
    """Dependency-injected only for QA; normal entry points supply the same adapters."""
    if origin not in ORIGINS:raise ValueError('Invalid origin')
    selected=config['selected_sources'] if selected_sources is None else selected_sources
    if not selected or len(selected)!=len(set(selected)) or not set(selected)<=set(config['selected_sources']):raise ValueError('Invalid selected sources')
    cap=config['max_pages_per_source'] if max_pages is None else max_pages
    if type(cap) is not int or not 1<=cap<=config['max_pages_per_source']:raise ValueError('Invalid page bound')
    if origin=='SCHEDULED' and not config['enabled']:return {'status':'CONFIG_ERROR','reason':'SCHEDULER_DISABLED','exit_code':4,'origin':origin}
    runtime=Path(runtime)
    try:
        with execution_lock(runtime):
            deadline=clock()+config['max_runtime']
            logger=logging.Logger('competitor.collection');logger.setLevel(logging.INFO);logger.propagate=False
            handler=RotatingFileHandler(runtime/'collection.log',maxBytes=config['log_max_bytes'],backupCount=config['log_backups'],encoding='utf-8');logger.addHandler(handler)
            result={'status':'RUNNING','origin':origin,'started_at':datetime.now(timezone.utc).isoformat(),'sources':{},'errors':[],'scheduler_enabled':config['enabled'],'pid':os.getpid()}
            run_id=None
            try:
                previous=runtime/'last-run.json'
                if previous.exists():
                    old=json.loads(previous.read_text(encoding='utf-8'))
                    if old.get('status')=='RUNNING' and old.get('run_id'):
                        repository.finish(old['run_id'],{**old,'status':'FAILED','reason':'PROCESS_INTERRUPTED'})
                run_id=repository.start(date.fromisoformat(config['since']),result);result['run_id']=run_id
                atomic_json(runtime/'last-run.json',result);logger.info('run=%s origin=%s start',run_id,origin)
                enabled=repository.enabled_sources()
                for source in selected:
                    if source not in enabled:result['sources'][source]={'status':'SKIPPED_DISABLED'};continue
                    if clock()>=deadline:
                        result['sources'][source]={'status':'FAILED','reason':'MAX_RUNTIME'};result['errors'].append({'source':source,'reason':'MAX_RUNTIME'});continue
                    try:
                        outcome=collector(repository,source,run_id,date.fromisoformat(config['since']),runtime/f'run-{run_id}',cap,deadline)
                        result['sources'][source]=outcome
                        if outcome['status'] not in ('COMPLETED','MANUAL_PENDING'):result['errors'].append({'source':source,'reason':outcome.get('reason','SOURCE_INCOMPLETE')})
                    except Exception as error:
                        # Never print connection strings, request credentials or arbitrary exception messages.
                        result['sources'][source]={'status':'FAILED','reason':type(error).__name__};result['errors'].append({'source':source,'reason':type(error).__name__})
                    logger.info('run=%s source=%s status=%s',run_id,source,result['sources'][source]['status']);atomic_json(runtime/'last-run.json',result)
                if not any(v['status']!='SKIPPED_DISABLED' for v in result['sources'].values()):raise ValueError('NO_ENABLED_SOURCES')
                result['report']=rebuild(repository,runtime/'business-report',config)
                statuses=[v['status'] for v in result['sources'].values()]
                preserved=any(v['status']=='COMPLETED' or v.get('stored',0)>0 for v in result['sources'].values())
                result['status']='PARTIAL' if result['errors'] and preserved else 'FAILED' if result['errors'] else 'COMPLETED'
            except Exception as error:
                result['status']='FAILED';result['errors'].append({'phase':'execution/report','reason':type(error).__name__})
            finally:
                result['finished_at']=datetime.now(timezone.utc).isoformat();result['exit_code']=EXIT_CODES[result['status']]
                try:
                    if run_id is not None:repository.finish(run_id,result)
                except Exception:
                    result['status']='FAILED';result['exit_code']=2;result['errors'].append({'phase':'run_record','reason':'FINISH_UNAVAILABLE'})
                finally:
                    try:
                        atomic_json(runtime/'last-run.json',result)
                        atomic_json(runtime/('scheduled-last.json' if origin=='SCHEDULED' else 'manual-last.json'),result)
                        logger.info('run=%s status=%s exit=%s',run_id,result['status'],result['exit_code'])
                    finally:handler.close();logger.removeHandler(handler)
            return result
    except AlreadyRunning:return {'status':'SKIP_ALREADY_RUNNING','origin':origin,'exit_code':3}

class CollectionService:
    def __init__(self,dev_config=None,schedule_config=None,runtime=None):
        self.dev_config=dev_config;self.schedule_config=schedule_config;self.runtime=Path(runtime or ROOT/'competitor-data/collection')
    def view(self):
        cfg=settings(self.schedule_config);p=self.runtime/'last-run.json';last=json.loads(p.read_text(encoding='utf-8')) if p.exists() else None
        try:
            with execution_lock(self.runtime):running=False
        except AlreadyRunning:running=True
        history={}
        for kind in ('manual','scheduled'):
            path=self.runtime/(kind+'-last.json');history[kind]=json.loads(path.read_text(encoding='utf-8')) if path.exists() else None
        return {'config':cfg,'last':last,'running':running,**history,'next_run_at':None,'installed':False}
    def run(self,origin,selected_sources=None,max_pages=None):
        from .collection_adapter import Repository,collect_source,rebuild_report
        cfg=settings(self.schedule_config)
        if origin=='SCHEDULED' and not cfg['enabled']:return {'status':'CONFIG_ERROR','reason':'SCHEDULER_DISABLED','exit_code':4,'origin':origin}
        repo=Repository(self.dev_config)
        try:result=run_competitor_collection(repo,collect_source,rebuild_report,origin=origin,config=cfg,runtime=self.runtime,selected_sources=selected_sources,max_pages=max_pages)
        finally:repo.close()
        return result
