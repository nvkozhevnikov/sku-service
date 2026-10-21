"""Leased nine-source queue runtime: shared host gate, manifests and SQL receipts.

REFRESH uses frozen known URLs; DISCOVERY uses source-specific public traversal;
RESUME reconciles original captures against SQL before any further product GET.
This module never reconciles missing products or changes canonical identities.
"""
from contextvars import ContextVar
from dataclasses import asdict,replace
from datetime import datetime, timezone
from pathlib import Path
import hashlib, json, math, os, time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

from .host_fetch import BoundedHostFetcher, compile_policy, host_key
from .source_resume import SourceCheckpoint, classify, pending_manifest, digest
from .source_discovery import HOSTS, root_urls, validate_node, decode_node
from .source_jobs import RUNTIME_SOURCES, NATIVE_SOURCES, validate_url, parse_existing
from .rate_control import SystemClock
from universal_supplier.commercial_persistence import HttpCapture, product_card_from_snapshot
from universal_supplier.http_capture import UrllibPublicHttpClient, MAX_RESPONSE_BYTES, sanitise_html
from universal_supplier.ltt_uss_capture import sanitise_for_charset, charset_from_response
from universal_supplier.kami_checkpoint import write_checkpoint

LEASED_HOST = ContextVar('source_collector_leased_host', default=None)
LEASE_ALIVE = ContextVar('source_collector_lease_alive', default=None)
MODES = ('REFRESH', 'DISCOVERY', 'RESUME')
AGENT = 'UniversalSupplierReadOnly/1.0'

def engine_fingerprint():
    root=Path(__file__).resolve().parents[2]
    files=sorted([*root.joinpath('universal_supplier').rglob('*.py'),*root.joinpath('sterbrust_matching').rglob('*.py')])
    return digest({p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

def evidence_root():
    return Path(os.environ.get('SOURCE_RUNNER_EVIDENCE_ROOT', Path(__file__).resolve().parents[2]/'reports/source-runner-evidence')).resolve()

def job_dir(code,owner):return evidence_root()/code/('job-'+str(owner))

def frozen_json(path,value):
    """No overwrite of manifests, archived responses or original input."""
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,default=str).encode()
    try:
        with path.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
    except FileExistsError:
        if path.read_bytes()!=raw:raise ValueError('IMMUTABLE_SOURCE_EVIDENCE_CHANGED')
    return hashlib.sha256(raw).hexdigest()

def checked_input(value,code):
    if value.get('format')!='source-runtime-input/v1' or value.get('supplier_code')!=code:raise ValueError('SOURCE_INPUT_NAMESPACE')
    if value.get('mode') not in ('REFRESH','DISCOVERY'):raise ValueError('SOURCE_INPUT_MODE')
    if value.get('engine_sha256')!=engine_fingerprint():raise ValueError('SOURCE_ENGINE_CHANGED_REQUIRES_REVIEW')
    entries=value.get('entries',[]);urls=set();ids=set()
    if len(entries)>100000:raise ValueError('SOURCE_INPUT_TOO_LARGE')
    for e in entries:
        validate_url(code,e['url'])
        if e['url'] in urls:raise ValueError('DUPLICATE_SOURCE_URL')
        urls.add(e['url'])
        ext=e.get('source_external_id')
        if ext:
            if not isinstance(ext,str) or ext in ids:raise ValueError('DUPLICATE_SOURCE_ID')
            ids.add(ext)
    if value['mode']=='REFRESH' and not entries:raise ValueError('REFRESH_HAS_NO_KNOWN_URLS')
    for u in value.get('seeds',[]):validate_node(code,u)
    if value['mode']=='DISCOVERY' and not value.get('seeds'):raise ValueError('DISCOVERY_SEEDS_REQUIRED')
    if 'canary' in value:
        from .source_canary import validate_input
        validate_input(value)
    return value

def read_input(code,owner,sha):
    data=json.loads((job_dir(code,owner)/'input.json').read_text(encoding='utf-8'))
    checked_input(data,code)
    if digest(data)!=sha:raise ValueError('RESUME_INPUT_CHANGED')
    return data

def runtime_enabled():return os.environ.get('STERBRUST_SOURCE_RUNTIME_ENABLED')=='YES'

def run_source(job,config,*,backend_factory=None,client_factory=None,clock=None):
    code=job.supplier_code
    if code not in RUNTIME_SOURCES or job.adapter_name!=code:raise ValueError('SOURCE_JOB_NAMESPACE')
    if os.environ.get('STERBRUST_ALLOW_SUPPLIER_HTTP')!='YES':raise PermissionError('LIVE_SUPPLIER_HTTP_NOT_AUTHORIZED')
    if not runtime_enabled():raise PermissionError('SOURCE_RUNTIME_NOT_ENABLED')
    if LEASED_HOST.get()!=job.domain or job.domain!=host_key('https://'+HOSTS[code]):raise PermissionError('CURRENT_HOST_LEASE_REQUIRED')
    settings=job.settings_snapshot.get('crawl_settings') or {}
    mode=settings.get('collection_mode','REFRESH')
    if mode not in MODES:raise ValueError('SOURCE_MODE')
    persist=settings.get('persist_source_data',False)
    if type(persist) is not bool:raise ValueError('PERSIST_SOURCE_DATA_MUST_BE_BOOLEAN')
    if persist and os.environ.get('STERBRUST_ALLOW_SOURCE_WRITES')!='YES':raise PermissionError('SOURCE_WRITES_NOT_AUTHORIZED')
    if mode=='RESUME' and not persist:raise PermissionError('RESUME_REQUIRES_SQL_READBACK')
    limit=settings.get('max_products');pages=settings.get('max_discovery_pages',100);seconds=settings.get('max_run_duration_seconds',300)
    if type(limit) is not int or not 1<=limit<=100000 or type(pages) is not int or not 1<=pages<=100000:raise ValueError('SOURCE_URL_BUDGET')
    if type(seconds) not in (int,float) or not math.isfinite(seconds) or not 0<seconds<=86400:raise ValueError('SOURCE_TIME_BUDGET')
    clock=clock or SystemClock();start=clock.monotonic();deadline=start+seconds
    rate=compile_policy(code,'https://'+HOSTS[code],job.settings_snapshot.get('rate_settings'),approval=settings.get('host_policy_approval'))
    if seconds>rate.max_run_duration_seconds:raise ValueError('TIME_BUDGET_EXCEEDS_HOST_PROFILE')
    # A resume must use the original input AND original policy; no hidden speedup.
    owner=settings.get('resume_owner_job_id',job.id) if mode=='RESUME' else job.id
    if type(owner) is not int or owner<1:raise ValueError('RESUME_OWNER')
    if mode=='RESUME':
        original=read_input(code,owner,settings['resume_input_sha256'])
        if original['rate_policy']!=rate.snapshot():raise ValueError('RESUME_POLICY_CHANGED')
    else:
        original=job.settings_snapshot.get('source_input')
        if original is None:
            # Legacy saved manifest can seed REFRESH but never supply fake HTTP
            # observation metadata for this fresh collection.
            from .source_jobs import load_manifest
            _,data=load_manifest(settings,code,deadline=deadline,clock=clock.monotonic)
            original={'format':'source-runtime-input/v1','supplier_code':code,'mode':mode,
                'entries':[{'url':e['url'],'source_external_id':e['source_external_id']} for e in data['entries']],
                'seeds':root_urls(code) if mode=='DISCOVERY' else [],'rate_policy':rate.snapshot(),'engine_sha256':engine_fingerprint()}
        original=checked_input(original,code)
        if original['rate_policy']!=rate.snapshot():raise ValueError('SOURCE_POLICY_SNAPSHOT_CHANGED')
    canary=original.get('canary')
    if original['mode']=='REFRESH' and len(original['entries'])>limit:raise ValueError('SNAPSHOT_EXCEEDS_APPROVED_TOTAL_CAP')
    gate_size=(canary or {}).get('safety_gate_size',settings.get('safety_gate_size',5))
    if type(gate_size) is not int or not 1<=gate_size<=20:raise ValueError('SAFETY_GATE_SIZE')
    gate_passed=False
    if settings.get('source_canary') or canary:
        if not canary:raise ValueError('CANARY_SNAPSHOT_MISSING')
        if job.trigger_type!='manual' or job.crawl_type!='full':raise ValueError('CANARY_MANUAL_JOB_REQUIRED')
        from .source_canary import validate_input,validate_config
        validate_config(config);validate_input(original,settings,config)
        if not canary['supplier_enabled'] or canary['supplier_id']!=job.supplier_id:raise PermissionError('CANARY_SUPPLIER_DISABLED_OR_CHANGED')
    sha=digest(original);folder=job_dir(code,owner)
    frozen_json(folder/'input.json',original)
    if mode=='RESUME' and not (folder/'checkpoint.json').is_file():raise ValueError('RESUME_CHECKPOINT_MISSING')
    validate=lambda u:validate_url(code,u)
    cp=SourceCheckpoint(evidence_root(),code,owner,sha,validator=validate,lease_acquired=True)
    discovery=SourceCheckpoint(evidence_root()/'discovery',code,owner,sha,validator=lambda u:validate_node(code,u),lease_acquired=True)
    if mode!='RESUME' and (cp.state['revision'] or discovery.state['revision']):raise ValueError('EXISTING_JOB_REQUIRES_EXPLICIT_RESUME')
    if mode!='RESUME':cp.add_urls([])
    run_folder=job_dir(code,job.id);run_folder.mkdir(parents=True,exist_ok=True)
    stop_file=run_folder/'STOP_REQUESTED'
    lease_alive=LEASE_ALIVE.get()
    def stopped():
        if lease_alive is not None and not lease_alive():return 'HOST_LEASE_LOST'
        try:return stop_file.read_text(encoding='utf-8').strip()[:1000] or 'OPERATOR_STOP'
        except FileNotFoundError:return False
    entries=list(original['entries'])
    backend=None;run_id=None;fetcher=None;dispatch_budget=None;rows=[];committed=[];matching_pending=[];errors=[];termination=None
    count_names=('new_products','changed_products','unchanged_products','new_offers','changed_offers','unchanged_offers')
    sql_counts={k:0 for k in count_names};counts_known=True
    http_started=clock.monotonic();http_seconds=0.;sql_seconds=0.;parse_seconds=0.;matching_seconds=None
    identity={'status':'NOT_RUN','matching_applied':False,'total':0};records={};robot=None
    try:
        if mode=='RESUME' and cp.state['inflight']:raise ValueError('UNCERTAIN_PRODUCT_RECEIPT_REQUIRES_OPERATOR')
        if persist:
            if backend_factory is None:
                from .source_backend import SourceBackend
                backend=SourceBackend(config,code)
            else:backend=backend_factory(config,code)
            run_id=backend.start(job,mode)
        def allowed(url):
            if url==robots_url and robot is None:return True
            return robot is not None and robot.can_fetch(AGENT,url)
        robots_url='https://'+HOSTS[code]+'/robots.txt'
        dispatch_budget=None
        if canary and original['mode']=='REFRESH':
            from .source_canary import DispatchBudget
            dispatch_budget=DispatchBudget(original,robots_url)
        def request_url(url):
            if canary and original['mode']=='REFRESH' and url!=robots_url and url not in {e['url'] for e in original['entries']}:
                if fetcher is not None:fetcher.stop('CANARY_URL_OUTSIDE_FROZEN_BATCH')
                raise ValueError('CANARY_URL_OUTSIDE_FROZEN_BATCH')
            p=urlsplit(url)
            if url==robots_url:return url
            try:return validate_url(code,url)
            except ValueError:return validate_node(code,url)
        fetcher=BoundedHostFetcher(HOSTS[code],rate,client_factory or UrllibPublicHttpClient,
            validator=request_url,robots_allowed=allowed,robots_delay_seconds=0,
            lease_acquired=True,clock=clock,stop_requested=stopped,source=code,approval=settings.get('host_policy_approval'),defer_first_start=True)
        fetcher.stop_sink=lambda reason:frozen_json(run_folder/'FIRST_STOP.json',{'reason':reason,'job_id':job.id,'observed_at':datetime.now(timezone.utc).isoformat()})
        if dispatch_budget:fetcher.request_start_guard=dispatch_budget
        def archive(outcome):
            if outcome.response is None:return
            response=outcome.response
            if len(response.body)>MAX_RESPONSE_BYTES:raise ValueError('SOURCE_RESPONSE_TOO_LARGE')
            charset=charset_from_response(response)
            raw=response.body
            xml='xml' in (response.content_type or '').lower()
            body=raw if xml else sanitise_for_charset(raw,charset) if code.startswith('ltt_') or code=='uss_stanko' else sanitise_html(raw,response.content_type)
            h=hashlib.sha256(body).hexdigest();dest=folder/'captures'/(h+('.xml' if xml else '.html'));dest.parent.mkdir(parents=True,exist_ok=True)
            try:
                with dest.open('xb') as stream:stream.write(body);stream.flush();os.fsync(stream.fileno())
            except FileExistsError:
                if hashlib.sha256(dest.read_bytes()).hexdigest()!=h:raise ValueError('CAPTURE_FILE_CHANGED')
            record={'url':outcome.url,'final_url':response.final_url,'http_status':response.status_code,
                'content_type':response.content_type,'observed_at':outcome.observed_at.isoformat(),
                'response_sha256':hashlib.sha256(raw).hexdigest(),'evidence_sha256':h,'file':str(dest),'redirects':response.redirects,
                'attempts':outcome.attempts,'error':outcome.error,'retry_after_seconds':outcome.retry_after_seconds,
                'origin_job_id':owner,'input_sha256':sha}
            # Exact raw sitemap paths are XML, contain only discovery evidence;
            # product bodies are sanitised with source charset before archival.
            frozen_json(folder/'responses'/(hashlib.sha256((outcome.url+'|'+record['observed_at']).encode()).hexdigest()+'.json'),record)
            records[outcome.url]=record
            if outcome.url in cp.state['inflight'] and response.status_code==200 and not outcome.error:
                cap=capture_from_record(record)
                cp.record_terminal([receipt_for(outcome.url,'HTTP_CAPTURED_NOT_COMMITTED',cap,code,owner,sha)])
        fetcher.receipt_sink=archive
        budget=limit+pages+4
        def fetch(urls):
            nonlocal http_seconds
            remaining=deadline-clock.monotonic()
            if remaining<=0:fetcher.stop('MAX_RUN_DURATION_EXCEEDED');return []
            began=clock.monotonic()
            try:return list(fetcher.fetch(urls,limit=budget,seconds=min(remaining,3600)))
            finally:http_seconds+=clock.monotonic()-began
        def robots():
            nonlocal robot
            outcomes=fetch([robots_url])
            if len(outcomes)!=1 or outcomes[0].error or outcomes[0].response.status_code!=200:raise ValueError(fetcher.stop_reason or 'ROBOTS_NOT_PROVEN')
            response=outcomes[0].response
            if '<html' in response.body.decode('utf-8',errors='ignore').lower():raise ValueError('ROBOTS_NOT_TEXT')
            robot=RobotFileParser();robot.parse(response.body.decode('utf-8').splitlines())
            delay=robot.crawl_delay(AGENT);rr=robot.request_rate(AGENT)
            minimum=max(delay or 0,rr.seconds/rr.requests if rr else 0)
            if minimum>rate.min_request_interval_seconds:raise PermissionError('ROBOTS_REQUIRES_SLOWER_PROFILE')
        entries=list(original['entries'])
        # DISCOVERY has its own durable node frontier. A node with lost response
        # receipt is uncertain; it never silently re-enters the HTTP queue.
        if original['mode']=='DISCOVERY':
            if mode!='RESUME':discovery.add_urls(original['seeds'])
            if discovery.state['inflight']:raise ValueError('UNCERTAIN_DISCOVERY_REQUIRES_RECONCILIATION')
            discovered=lambda:{p['url']:p for r in discovery.state['discovery_receipts'].values() for p in r.get('products',[])}
            if discovery.state['frontier'] and len(discovered())<limit:
                robots();nodes=0
                while discovery.state['frontier'] and nodes<pages and len(discovered())<limit and not fetcher.aborted():
                    if clock.monotonic()>=deadline:fetcher.stop('MAX_RUN_DURATION_EXCEEDED');break
                    node=discovery.claim_batch(1)[0];outcomes=fetch([node]);nodes+=1
                    if not outcomes or outcomes[0].error or outcomes[0].response.status_code!=200:raise ValueError(fetcher.stop_reason or 'DISCOVERY_HTTP_FAILED')
                    rec=records[node];body=read_body(rec)
                    children,products=decode_node(code,node,body)
                    # Source parser validates every detail candidate before SQL.
                    discovery.complete_discovery_node(node,children,evidence_sha256=rec['evidence_sha256'],products=products)
                    found=len({p['url'] for r in discovery.state['discovery_receipts'].values() for p in r.get('products',[])})
                    if backend:backend.progress({'collection_mode':mode,'phase':'DISCOVERY','found':found,'processed':0,'remaining':found,'discovery_visited':len(discovery.state['visited']),'discovery_remaining':len(discovery.state['frontier']),'request_metrics':fetcher.metrics.as_metadata()})
            if discovery.state['frontier'] and len(discovered())<limit:
                termination=fetcher.stop_reason or 'DISCOVERY_PAGE_BUDGET'
            else:
                if not discovery.state['frontier']:discovery.finish_discovery()
                candidates={p['url']:p for r in discovery.state['discovery_receipts'].values() for p in r.get('products',[])}
                entries=[candidates[u] for u in sorted(candidates)[:limit]]
                frozen_json(folder/'manifest.json',{'format':'source-discovered-manifest/v1','supplier_code':code,'input_sha256':sha,'entries':entries,'traversal_finished':not bool(discovery.state['frontier']),'external_catalog_completeness':'NOT_PROVEN'})
        elif mode!='RESUME':
            frozen_json(folder/'manifest.json',{'format':'source-refresh-manifest/v1','supplier_code':code,'input_sha256':sha,'entries':entries})
        # Persist the entire frontier once. Large manifests are consumed in
        # bounded batches; unselected URLs remain provably NOT_REQUESTED.
        if entries and not termination:
            cp.add_urls([e['url'] for e in entries]);byurl={e['url']:e for e in entries}
            def reconcile():
                receipts=[]
                for e in entries:
                    url=e['url']
                    if url in cp.state['terminal']:receipts.append(cp.state['terminal'][url]['receipt'])
                    elif url in cp.state['frontier']:receipts.append({'url':url,'state':'NOT_REQUESTED','origin':{'source':code,'job_id':owner,'manifest_sha256':sha}})
                def probe(e,r):
                    if r and r['state']=='NOT_REQUESTED':return 'NOT_FOUND'
                    return backend.probe(e,r) if backend else 'UNKNOWN'
                classified=classify(entries,receipts,probe,source=code,parent_job_id=owner,manifest_sha256=sha)
                return classified,pending_manifest({'supplier_code':code,'entries':entries},classified,parent_job_id=owner,parent_manifest_sha256=sha)
            if mode=='RESUME':
                classified,plan=reconcile()
                if not plan['automatic_resume_allowed']:raise ValueError('UNCERTAIN_OR_FAILED_RESUME_REQUIRES_OPERATOR')
                for r in classified:
                    if r['state']=='COMMITTED':
                        cp.record_terminal([{**r['receipt'],'state':'COMMITTED','sql_verified':True}])
                        if not r['receipt'].get('matching_receipt'):matching_pending.append(r['receipt']['external_id'])
                offline=[r for r in classified if r['state']=='HTTP_CAPTURED_NOT_COMMITTED']
            else:offline=[]
            seen={}
            def process(entry,record=None,receipt=None):
                nonlocal sql_seconds,parse_seconds,counts_known
                url=entry['url'];cap=capture_from_record(record) if record else restore_capture(receipt['original_capture_metadata'])
                began=time.perf_counter()
                body=Path(cap.evidence_ref).read_bytes()
                if hashlib.sha256(body).hexdigest()!=cap.evidence_sha256:raise ValueError('ARCHIVE_HASH_CHANGED')
                product=parse_existing(code,body.decode('utf-8'),cap.final_url)
                if code in NATIVE_SOURCES:product=replace(product,requested_url=cap.requested_url,final_url=cap.final_url,redirect_urls=cap.redirects)
                external=product.external_id if code in ('kami',*NATIVE_SOURCES) else product.site_internal_id
                if not external or not product.name:raise ValueError('SOURCE_IDENTITY_UNRESOLVED')
                if entry.get('source_external_id') and external!=entry['source_external_id']:raise ValueError('SOURCE_EXTERNAL_ID_CHANGED')
                if receipt and receipt.get('external_id') and receipt['external_id']!=external:raise ValueError('RESUME_PARSER_ID_CHANGED')
                if external in seen and seen[external]!=url:raise ValueError('SOURCE_ID_REUSED_BY_ANOTHER_URL')
                seen[external]=url
                card=product if code in ('kami',*NATIVE_SOURCES) else product_card_from_snapshot(product,cap)
                validate_url(code,card.canonical_url)
                if card.supplier_code!=code:raise ValueError('SOURCE_NAMESPACE_CHANGED')
                parse_seconds+=time.perf_counter()-began
                captured=receipt_for(url,'HTTP_CAPTURED_NOT_COMMITTED',cap,code,owner,sha,external)
                cp.record_terminal([captured])
                if backend:
                    reason=stopped()
                    if reason:
                        fetcher.stop(str(reason));raise RuntimeError(str(reason))
                    began=time.perf_counter();known_before=counts_known;counts_known=False
                    written=backend.persist(product,cap);sql_seconds+=time.perf_counter()-began
                    count=(written or {}).get('ingest_counts')
                    if count is not None:
                        count=count if isinstance(count,dict) else vars(count)
                        for k in count_names:sql_counts[k]+=count.get(k,0)
                        counts_known=known_before
                    if backend.probe(entry,captured)!='COMMITTED':
                        counts_known=False;raise ValueError('SQL_COMMIT_NOT_PROVEN')
                    cp.record_terminal([{**captured,'state':'COMMITTED','sql_verified':True}]);committed.append(external)
                    matching_pending.append(external)
                rows.append({'url':url,'external_id':external,'status':'COMMITTED' if backend else 'HTTP_CAPTURED_NOT_COMMITTED','observed_at':cap.observed_at.isoformat()})
            for item in offline:
                if stopped():termination=str(stopped());break
                process(item['entry'],receipt=item['receipt'])
            if cp.state['frontier'] and not termination:
                if robot is None:robots()
                fetched_count=0
                while cp.state['frontier'] and fetched_count<limit and not fetcher.aborted():
                    if clock.monotonic()>=deadline:fetcher.stop('MAX_RUN_DURATION_EXCEEDED');break
                    # Do not schedule card N+1 until the first N exact SQL
                    # receipts have passed. The SAME immutable job then continues.
                    gate_target=min(gate_size,len(entries))
                    verified=sum(x['state']=='COMMITTED' for x in cp.state['terminal'].values())
                    gate_passed=verified>=gate_target
                    if not gate_passed and fetched_count>=gate_target:
                        fetcher.stop('SAFETY_GATE_SQL_NOT_PROVEN');break
                    batch=min(rate.max_concurrency,limit-fetched_count,1000)
                    if not gate_passed:batch=min(batch,max(1,gate_target-verified))
                    urls=cp.claim_batch(batch);outcomes=fetch(urls);fetched_count+=len(urls)
                    for outcome in outcomes:
                        if outcome.error or outcome.response is None or outcome.response.status_code!=200:
                            if outcome.response is None and outcome.transport_started is False:
                                cp.return_not_requested({'url':outcome.url,'state':'NOT_REQUESTED','transport_started':False,'origin':{'source':code,'job_id':owner,'manifest_sha256':sha},'reason':outcome.error})
                                fetcher.stop(fetcher.stop_reason or 'SOURCE_REQUEST_NOT_DISPATCHED');continue
                            receipt={'url':outcome.url,'state':'FAILED' if outcome.response else 'UNCERTAIN','origin':{'source':code,'job_id':owner,'manifest_sha256':sha},'reason':outcome.error or 'HTTP_'+str(outcome.response.status_code)}
                            cp.record_terminal([receipt]);errors.append(receipt);fetcher.stop('SOURCE_CARD_HTTP_FAILED');continue
                        # All successful responses are already durably captured.
                        if fetcher.stop_reason:continue
                        try:process(byurl[outcome.url],records[outcome.url])
                        except ValueError as e:
                            if str(e)=='SQL_COMMIT_NOT_PROVEN':raise
                            cp.record_terminal([{'url':outcome.url,'state':'FAILED','origin':{'source':code,'job_id':owner,'manifest_sha256':sha},'reason':str(e)}]);errors.append({'url':outcome.url,'error':str(e)});fetcher.stop('SOURCE_CARD_PARSE_FAILED')
                    gate_passed=sum(x['state']=='COMMITTED' for x in cp.state['terminal'].values())>=gate_target
                    if backend:backend.progress({'collection_mode':mode,'phase':'PRODUCTS','safety_gate_passed':gate_passed,'approved_total_cap':limit,'found':len(entries),'processed':sum(x['state']!='NOT_REQUESTED' for x in cp.state['terminal'].values()),'parsed_this_job':len(rows),'remaining':len(cp.state['frontier']),'sql_committed_this_job':len(committed),'sql_committed':sum(x['state']=='COMMITTED' for x in cp.state['terminal'].values()),'request_metrics':fetcher.metrics.as_metadata(),'http_seconds':http_seconds,'http_cards_per_minute':None if http_seconds<=0 else 60*sum(u in byurl for u in records)/http_seconds})
                termination=termination or fetcher.stop_reason or ('PRODUCT_BATCH_BUDGET' if cp.state['frontier'] else None)
            classified,plan=reconcile()
            resume_safe=plan['automatic_resume_allowed'] and bool(cp.state['frontier'] or plan['offline_pending'])
        else:
            plan={'uncertain':[],'failed_requires_decision':[]};resume_safe=bool(termination=='DISCOVERY_PAGE_BUDGET' and not discovery.state['inflight'])
        if matching_pending and not (termination and termination.startswith(('SOURCE_BLOCKED','OPERATOR','SQL_','UNCERTAIN','HOST_LEASE'))):
            reason=stopped()
            if reason:
                fetcher.stop(str(reason));raise RuntimeError(str(reason))
            backend.progress({'phase':'MATCHING','sql_committed_this_job':len(committed)})
            began=time.perf_counter();identity=backend.matching(list(dict.fromkeys(matching_pending)));matching_seconds=time.perf_counter()-began
            if identity.get('status')!='succeeded':raise ValueError('MATCHING_NOT_SUCCEEDED')
            matching_receipts=[]
            for url,row in list(cp.state['terminal'].items()):
                r=row['receipt']
                if row['state']=='COMMITTED' and r.get('external_id') in matching_pending:
                    matching_receipts.append({**r,'matching_receipt':{'job_id':job.id,'run_id':run_id,'status':'succeeded','external_id':r['external_id'],'policy_version':identity.get('policy_version')}})
            # One atomic receipt batch for one successful scoped matching run.
            # Avoid N full checkpoint rewrites for a large catalogue.
            if matching_receipts:cp.record_terminal(matching_receipts)
    except Exception as e:
        termination=(fetcher.stop_reason if fetcher else None) or str(e) or type(e).__name__
        errors.append({'type':type(e).__name__,'reason':str(e)})
        resume_safe=False;plan={'uncertain':list(cp.state['inflight']),'failed_requires_decision':[]}
    finally:
        # Result always describes actual commits and checkpoints, even if SQL
        # raised after committing but before a local terminal receipt was saved.
        metrics=fetcher.metrics.as_metadata() if fetcher else {}
        if original['mode']=='DISCOVERY':
            candidates={p['url']:p for r in discovery.state['discovery_receipts'].values() for p in r.get('products',[])}
            entries=[candidates[u] for u in sorted(candidates)[:limit]]
        product_urls={e['url'] for e in locals().get('entries',[])}
        remaining=len(cp.state['frontier'])+len(cp.state['inflight'])
        if original['mode']=='DISCOVERY' and not discovery.state['discovery_finished'] and termination:remaining=len(entries)
        manifest_path=folder/'manifest.json'
        if not manifest_path.is_file():
            manifest_path=folder/('manifest-partial-r'+str(discovery.state['revision'])+'.json')
            frozen_json(manifest_path,{'format':'source-discovered-manifest/v1','supplier_code':code,'input_sha256':sha,'entries':locals().get('entries',[]),'traversal_finished':False,'external_catalog_completeness':'NOT_PROVEN'})
        result={'status':'partial' if termination or errors or remaining else 'succeeded','supplier_code':code,'collection_mode':mode,
            'crawl_run_id':run_id,'owner_job_id':owner,'input_sha256':sha,'checkpoint':str(cp.path),
            'manifest':str(manifest_path),'manifest_sha256':hashlib.sha256(manifest_path.read_bytes()).hexdigest(),'found':len(locals().get('entries',[])),
            'processed':sum(x['state']!='NOT_REQUESTED' for x in cp.state['terminal'].values()),'remaining':remaining,'discovery_visited':len(discovery.state['visited']),
            'discovery_remaining':len(discovery.state['frontier'])+len(discovery.state['inflight']),
            'discovered_urls_total':len(candidates) if original['mode']=='DISCOVERY' else len(entries),'selected_urls':len(entries),
            'discovery_complete':bool(original['mode']=='DISCOVERY' and discovery.state['discovery_finished']),
            'external_catalog_completeness':'NOT_PROVEN','deactivation_allowed':False,
            'sql_committed':sum(x['state']=='COMMITTED' for x in cp.state['terminal'].values()),
            'sql_committed_this_job':len(committed),'rows':rows,'errors':len(errors),'error_details':errors,
            'sql_counts':sql_counts if counts_known else None,
            'parsed_this_job':len(rows),
            'request_metrics':metrics,'http_seconds':http_seconds,'http_cards_per_minute':None if http_seconds<=0 else 60*len(set(records)&product_urls)/http_seconds,
            'stage_timings':{'parsing_seconds_sum':parse_seconds,'sql_seconds':sql_seconds,'matching_seconds':matching_seconds},
            'effective_rate_policy':rate.snapshot(),'approved_total_cap':limit,'safety_gate_size':gate_size,'safety_gate_passed':sum(x['state']=='COMMITTED' for x in cp.state['terminal'].values())>=min(gate_size,len(entries)) if entries else False,'termination_reason':termination,'resume_available':locals().get('resume_safe',False),
            'resume_uncertain':locals().get('plan',{}).get('uncertain',[]),'matching_applied':identity.get('matching_applied',False),
            'identity_post_processing':identity}
        if canary:result['canary']={**canary,'product_requests':list(dispatch_budget.products) if dispatch_budget else [],'robots_requests':dispatch_budget.robots if dispatch_budget else 0}
        write_checkpoint(run_folder/'result.json',result,durable=True)
        if backend:
            try:backend.finish(result)
            finally:backend.close()
    return result

def read_body(record):
    body=Path(record['file']).read_bytes()
    if hashlib.sha256(body).hexdigest()!=record['evidence_sha256']:raise ValueError('CAPTURE_ARCHIVE_HASH')
    return body

def capture_from_record(record):
    return HttpCapture(record['url'],record['final_url'],record['http_status'],record['content_type'],
        datetime.fromisoformat(record['observed_at']),record['response_sha256'],record['evidence_sha256'],record['file'],redirects=tuple(record['redirects']),
        diagnostics=('source-job:'+str(record['origin_job_id']),'source-input-sha256:'+record['input_sha256']) if 'origin_job_id' in record else ())

def restore_capture(value):
    fields=dict(value);fields['observed_at']=datetime.fromisoformat(fields['observed_at']);return HttpCapture(**fields)

def receipt_for(url,state,capture,code,owner,sha,external=None):
    fields=asdict(capture);fields['observed_at']=capture.observed_at.isoformat()
    return {'url':url,'state':state,'capture_fingerprint':capture.fingerprint,'original_capture_metadata':fields,
        'external_id':external,'origin':{'source':code,'job_id':owner,'manifest_sha256':sha}}
