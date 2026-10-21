"""Freeze supported panel actions into the existing crawl job snapshot."""
from copy import deepcopy
from .source_jobs import SOURCES
from .source_discovery import root_urls
from .host_fetch import compile_policy
from .source_runtime import runtime_enabled,read_input,job_dir,checked_input,evidence_root,engine_fingerprint
from .source_resume import SourceCheckpoint,classify,pending_manifest

def prepare_snapshot(snapshot,mode,*,known_rows=(),resume=None,config=None):
    if not runtime_enabled():raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
    if mode not in ('REFRESH','DISCOVERY','RESUME'):raise ValueError('SOURCE_MODE')
    code=snapshot['supplier_code']
    if code not in SOURCES or snapshot['adapter_name']!=code:raise ValueError('SOURCE_ACTION_NOT_SUPPORTED')
    out=deepcopy(snapshot);settings=out['crawl_settings']
    if mode=='RESUME':
        if not resume or resume['supplier_code']!=code or resume['status'] not in ('partial','failed','cancelled','stale_failed'):raise ValueError('RESUME_PARENT_NOT_TERMINAL')
        result=resume['result']
        if not result.get('input_sha256'):raise ValueError('RESUME_NOT_PROVEN_SAFE')
        owner=result['owner_job_id'];sha=result['input_sha256'];original=read_input(code,owner,sha)
        # The parent immutable policy is retained; UI cannot accelerate resume.
        out['rate_settings']=original['rate_policy']
        settings['host_policy_approval']=(resume['settings'].get('crawl_settings') or {}).get('host_policy_approval')
        compile_policy(code,'https://'+out['domain'],out['rate_settings'],approval=settings.get('host_policy_approval'))
        verify_resume(code,owner,sha,original,config)
        settings.update(resume_owner_job_id=owner,resume_input_sha256=sha,persist_source_data=True)
    else:
        policy=compile_policy(code,'https://'+out['domain'],out['rate_settings'],approval=settings.get('host_policy_approval'))
        out['rate_settings']=policy.snapshot()
        out['source_input']=checked_input({'format':'source-runtime-input/v1','supplier_code':code,'mode':mode,
            'entries':[{'url':url,'source_external_id':str(ext)} for ext,url in known_rows] if mode=='REFRESH' else [],
            'seeds':root_urls(code) if mode=='DISCOVERY' else [],'rate_policy':policy.snapshot(),'engine_sha256':engine_fingerprint()},code)
    settings.update(collection_mode=mode,execution_mode='LIVE',persist_source_data=True)
    return out

def verify_resume(code,owner,sha,original,config):
    """Read-only SQL reconciliation, rechecked by worker under its host lease."""
    folder=job_dir(code,owner)
    if not (folder/'checkpoint.json').is_file():raise ValueError('RESUME_CHECKPOINT_MISSING')
    import json
    state=json.loads((folder/'checkpoint.json').read_text(encoding='utf-8'))
    from .source_resume import digest
    checksum=state.pop('checksum')
    if digest(state)!=checksum or state['manifest_sha256']!=sha or state['source']!=code or state['job_id']!=owner:raise ValueError('RESUME_CHECKPOINT_CHANGED')
    discovery_path=evidence_root()/'discovery'/code/('job-'+str(owner))/'checkpoint.json'
    if original['mode']=='DISCOVERY':
        discovery=json.loads(discovery_path.read_text(encoding='utf-8'));check=discovery.pop('checksum')
        if (digest(discovery)!=check or discovery.get('source')!=code
                or discovery.get('job_id')!=owner or discovery.get('manifest_sha256')!=sha
                or discovery['inflight'] or discovery['category_failures']):raise ValueError('UNCERTAIN_DISCOVERY')
        from .source_discovery import validate_node
        for url in discovery['frontier']+discovery['visited']:
            validate_node(code,url)
        if discovery['frontier']:return
        entries=[p for rec in discovery['discovery_receipts'].values() for p in rec.get('products',[])]
        entries=list({e['url']:e for e in entries}.values())
    else:entries=original['entries']
    receipts=[]
    for entry in entries:
        url=entry['url']
        if url in state['terminal']:receipts.append(state['terminal'][url]['receipt'])
        elif url in state['frontier']:receipts.append({'url':url,'state':'NOT_REQUESTED','origin':{'source':code,'job_id':owner,'manifest_sha256':sha}})
    from .source_backend import SourceBackend
    backend=SourceBackend(config,code)
    try:
        def probe(entry,r):return 'NOT_FOUND' if r and r['state']=='NOT_REQUESTED' else backend.probe(entry,r)
        rows=classify(entries,receipts,probe,source=code,parent_job_id=owner,manifest_sha256=sha)
        plan=pending_manifest({'supplier_code':code,'entries':entries},rows,parent_job_id=owner,parent_manifest_sha256=sha)
        if not plan['automatic_resume_allowed']:raise ValueError('UNCERTAIN_OR_FAILED_RESUME')
    finally:backend.close()
