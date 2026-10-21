"""Bounded adapters for the existing queue; saved manifests, no identity apply.

The worker refreshes only explicitly recorded product URLs. Discovery remains
in the existing source-specific collectors; a partial manifest never deactivates
products. Network and persistence are separate explicit runtime permissions.
"""
from __future__ import annotations
from datetime import datetime, timezone
import hashlib,json,os,time
from pathlib import Path
from urllib.parse import urlsplit

from universal_supplier.models import FetchRecord
from universal_supplier.http_capture import MAX_RESPONSE_BYTES,has_blocking_interstitial,sanitise_html
from universal_supplier.commercial_persistence import HttpCapture,product_card_from_snapshot
from universal_supplier.full_supplier_collection import _safe_source_url
from universal_supplier.ltt_uss_capture import safe_source_url,_card_gate,sanitise_for_charset
from universal_supplier.http_capture import CaptureStatus

SOURCES=('beka_mak','beka_mak_tr','intervesp','vekprom','kami','ltt_msk','ltt_sib','ltt_yug','uss_stanko')
NATIVE_SOURCES=('partner_st','optimum')
RUNTIME_SOURCES=SOURCES+NATIVE_SOURCES

def validate_url(code,url):
    parsed=urlsplit(url)
    if code in NATIVE_SOURCES:
        from universal_supplier.url_safety import validate_public_http_url
        host={'partner_st':'partner-st.ru','optimum':'optimum.su'}[code]
        validate_public_http_url(url,(host,'www.'+host))
        if parsed.scheme!='https' or parsed.query or parsed.fragment:raise ValueError('UNSAFE_NATIVE_DETAIL')
        if code=='optimum' and not parsed.path.startswith('/catalog/'):raise ValueError('UNSAFE_OPTIMUM_DETAIL')
        if parsed.path=='/' or parsed.path.endswith('.xml'):raise ValueError('NOT_NATIVE_DETAIL')
        return url
    if parsed.port not in (None,443):raise ValueError('SOURCE_URL_NONSTANDARD_PORT')
    if code=='intervesp':
        # Source-wide candidate traversal; identity still comes from the own
        # el_Main detail ID. The legacy Beka-Mak-only collector is unchanged.
        if parsed.scheme!='https' or parsed.hostname not in ('intervesp.ru','www.intervesp.ru') or parsed.username or parsed.password or parsed.query or parsed.fragment or not parsed.path.startswith('/catalog/') or len(parsed.path.strip('/').split('/'))<2:raise ValueError('UNSAFE_INTERVESP_DETAIL')
        return url
    if code=='kami':
        from universal_supplier.adapters.kami import canonical_source_url
        canonical_source_url(url)
    elif code.startswith('ltt_') or code=='uss_stanko':safe_source_url(code,url)
    else:_safe_source_url(code,url)
    return url

def parse_existing(code,text,url,status=200):
    if code=='partner_st':
        from universal_supplier.adapters.partner_st import PartnerStAdapter
        return PartnerStAdapter().parse_product(FetchRecord(url,url,status,(),1,0,text))
    if code=='optimum':
        from universal_supplier.adapters.optimum_source.parser import OptimumProductParser
        from universal_supplier.adapters.optimum import to_product_card
        parser=OptimumProductParser()
        if not parser.is_product(text):raise ValueError('OPTIMUM_NOT_PRODUCT')
        return to_product_card(parser.parse_product(text,url,url),final_url=url)
    if code=='kami':
        from universal_supplier.adapters.kami import KamiAdapter
        return KamiAdapter().parse_product(FetchRecord(url,url,status,(),1,0,text))
    if code.startswith('ltt_') or code=='uss_stanko':
        gate,diagnostics=_card_gate(code,text,url)
        if gate is not CaptureStatus.SUCCESS:raise ValueError(gate.value+':'+','.join(diagnostics))
        if code in ('ltt_msk','ltt_sib'):
            from universal_supplier.adapters.ltt import parse_ltt_modx_detail
            return parse_ltt_modx_detail(text,supplier=code,source_url=url)
        if code=='ltt_yug':
            from universal_supplier.adapters.ltt_yug import parse_ltt_yug_detail
            return parse_ltt_yug_detail(text,source_url=url)
        from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail
        return parse_uss_stanko_detail(text,source_url=url)
    from universal_supplier.full_supplier_collection import _PARSERS
    return _PARSERS[code](text,source_url=url)

def load_manifest(settings,code,*,deadline,clock):
    path=Path(settings['source_manifest_path']).resolve()
    if path.stat().st_size>MAX_RESPONSE_BYTES:raise ValueError('SOURCE_MANIFEST_TOO_LARGE')
    body=path.read_bytes()
    if hashlib.sha256(body).hexdigest()!=settings['source_manifest_sha256']:raise ValueError('SOURCE_MANIFEST_SHA256_MISMATCH')
    data=json.loads(body)
    if data.get('format')!='source-job-manifest/v1' or data.get('supplier_code')!=code:raise ValueError('SOURCE_MANIFEST_NAMESPACE_MISMATCH')
    if not isinstance(data.get('entries'),list) or not data['entries']:raise ValueError('SOURCE_MANIFEST_EMPTY')
    urls=set();external_ids=set()
    for entry in data['entries']:
        if clock()>=deadline:raise TimeoutError('SOURCE_MANIFEST_VALIDATION_TIME_BUDGET')
        url=validate_url(code,entry['url'])
        if url in urls:raise ValueError('DUPLICATE_SOURCE_MANIFEST_URL')
        urls.add(url)
        external=entry.get('source_external_id')
        if not isinstance(external,str) or not external:raise ValueError('VERIFIED_SOURCE_EXTERNAL_ID_REQUIRED')
        if external in external_ids:raise ValueError('DUPLICATE_SOURCE_EXTERNAL_ID')
        external_ids.add(external)
        observed=datetime.fromisoformat(entry['observed_at'])
        if observed.tzinfo is None:raise ValueError('SOURCE_TIMESTAMP_REQUIRES_TIMEZONE')
        if entry.get('http_status')!=200:raise ValueError('SAVED_HTTP_200_EVIDENCE_REQUIRED')
        file=(path.parent/entry['file']).resolve()
        if not file.is_relative_to(path.parent):raise ValueError('SOURCE_EVIDENCE_PATH_ESCAPES_MANIFEST')
        if not file.is_file() or file.stat().st_size>MAX_RESPONSE_BYTES:raise ValueError('SOURCE_EVIDENCE_MISSING_OR_TOO_LARGE')
        if hashlib.sha256(file.read_bytes()).hexdigest()!=entry['sha256']:raise ValueError('SOURCE_EVIDENCE_SHA256_MISMATCH')
    return path,data

def make_source_runner(code,*,repository_factory=None,transport_factory=None,clock=time.monotonic,runtime_backend_factory=None,runtime_clock=None):
    if code not in SOURCES:raise ValueError('UNKNOWN_SOURCE_RUNNER')
    def run(job,config):
        if job.supplier_code!=code or job.adapter_name!=code:raise ValueError('SOURCE_JOB_NAMESPACE_MISMATCH')
        settings=job.settings_snapshot.get('crawl_settings') or {}
        if settings.get('collection_mode') or settings.get('execution_mode')=='LIVE':
            from .source_runtime import run_source
            return run_source(job,config,backend_factory=runtime_backend_factory,
                              client_factory=transport_factory,clock=runtime_clock)
        mode=settings.get('execution_mode','OFFLINE_SAVED')
        if mode not in ('OFFLINE_SAVED','LIVE'):raise ValueError('UNKNOWN_SOURCE_EXECUTION_MODE')
        # Check before constructing a transport, repository or reading a URL.
        if mode=='LIVE' and os.environ.get('STERBRUST_ALLOW_SUPPLIER_HTTP')!='YES':raise PermissionError('LIVE_SUPPLIER_HTTP_NOT_AUTHORIZED')
        persist=settings.get('persist_source_data',False)
        if persist is not True and persist is not False:raise ValueError('PERSIST_SOURCE_DATA_MUST_BE_BOOLEAN')
        if persist and os.environ.get('STERBRUST_ALLOW_SOURCE_WRITES')!='YES':raise PermissionError('SOURCE_WRITES_NOT_AUTHORIZED')
        limit=settings.get('max_products',100);seconds=settings.get('max_run_duration_seconds',300)
        if isinstance(limit,bool) or not isinstance(limit,int) or not 1<=limit<=1000:raise ValueError('INVALID_SOURCE_PRODUCT_BUDGET')
        if isinstance(seconds,bool) or not isinstance(seconds,(int,float)) or not 0<seconds<=3600:raise ValueError('INVALID_SOURCE_TIME_BUDGET')
        deadline=clock()+seconds
        manifest,data=load_manifest(settings,code,deadline=deadline,clock=clock)
        entries=data['entries'][:limit]
        repository=None;transport=None;rows=[];seen={};terminated=None
        try:
            if persist:
                if repository_factory is None:
                    from universal_supplier.postgres import PostgresRepository
                    repository=PostgresRepository(config)
                else:repository=repository_factory(config)
            for entry in entries:
                if clock()>=deadline:terminated='MAX_RUN_DURATION_EXCEEDED';break
                url=entry['url'];observed=datetime.fromisoformat(entry['observed_at']);response_hash=entry['sha256']
                final=url;redirects=();content_type=entry.get('content_type','text/html; charset=utf-8')
                charset=entry.get('charset')
                if mode=='OFFLINE_SAVED':
                    body=(manifest.parent/entry['file']).read_bytes()
                    if hashlib.sha256(body).hexdigest()!=entry['sha256']:raise ValueError('SOURCE_EVIDENCE_CHANGED_DURING_RUN')
                if len(body)>MAX_RESPONSE_BYTES:raise ValueError('SOURCE_RESPONSE_TOO_LARGE')
                if has_blocking_interstitial(body):terminated='SOURCE_BLOCKED_INTERSTITIAL';break
                evidence=sanitise_for_charset(body,charset) if code.startswith('ltt_') or code=='uss_stanko' else sanitise_html(body,content_type)
                text=evidence.decode('utf-8',errors='strict')
                try:
                    product=parse_existing(code,text,final)
                    external=product.external_id if code=='kami' else product.site_internal_id
                    if not external or not product.name:raise ValueError('SOURCE_IDENTITY_UNRESOLVED')
                    if entry.get('source_external_id') and external!=entry['source_external_id']:raise ValueError('SOURCE_EXTERNAL_ID_CHANGED')
                    if external in seen and seen[external]!=url:raise ValueError('SOURCE_ID_REUSED_BY_ANOTHER_URL')
                    seen[external]=url
                    eh=hashlib.sha256(evidence).hexdigest()
                    root=Path(os.environ.get('SOURCE_RUNNER_EVIDENCE_ROOT',Path(__file__).resolve().parents[2]/'reports/source-runner-evidence')).resolve()
                    if mode=='LIVE':
                        dest=root/code/(eh+'.html');dest.parent.mkdir(parents=True,exist_ok=True)
                        if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest()!=eh:raise ValueError('EVIDENCE_HASH_COLLISION')
                        if not dest.exists():dest.write_bytes(evidence)
                        evidence_ref=str(dest)
                    else:evidence_ref=str((manifest.parent/entry['file']).resolve())
                    capture=HttpCapture(url,final,200,content_type,observed,eh if mode=='OFFLINE_SAVED' else response_hash,eh,evidence_ref,redirects=redirects,response_hash_basis='sanitized_archive' if mode=='OFFLINE_SAVED' else 'raw_response')
                    if mode=='OFFLINE_SAVED' and persist:
                        if not entry.get('capture'):raise ValueError('ORIGINAL_CAPTURE_METADATA_REQUIRED_FOR_SAVED_WRITE')
                        recorded=dict(entry['capture']);recorded['observed_at']=datetime.fromisoformat(recorded['observed_at'])
                        capture=HttpCapture(**recorded)
                        if capture.requested_url!=url or capture.final_url!=final or capture.evidence_sha256!=eh or capture.observed_at!=observed:raise ValueError('SAVED_CAPTURE_METADATA_MISMATCH')
                    card=product if code=='kami' else product_card_from_snapshot(product,capture)
                    validate_url(code,card.canonical_url)
                    if code=='kami':
                        from universal_supplier.kami_persistence import prepare_observation
                        card,_=prepare_observation(card,capture)
                    if card.supplier_code!=code:raise ValueError('PARSER_RETURNED_ANOTHER_NAMESPACE')
                except ValueError as error:
                    rows.append({'url':url,'status':'REVIEW','reason':str(error)});continue
                result=None
                if repository:
                    # Existing narrow writers retain commercial history; no reconcile.
                    result=repository.persist_kami_observation(product,capture) if code=='kami' else repository.persist_commercial_observation(product,capture)
                rows.append({'url':url,'status':'PERSISTED' if repository else 'OFFLINE_PARSED' if mode=='OFFLINE_SAVED' else 'LIVE_PARSED','external_id':external,'observed_at':observed.isoformat(),'price':None if card.price is None else str(card.price),'price_state':(card.raw_data.get('price_extraction') or {}).get('state'),'capture_fingerprint':capture.fingerprint,'evidence_sha256':eh,'response_sha256':response_hash,'exact_noop':None if result is None else result.get('exact_noop')})
        finally:
            if repository:repository.close()
            if transport and getattr(transport,'session',None):transport.session.close()
        identity = {'matching_applied': False, 'skipped': 'NO_PERSISTED_INPUT'}
        persisted_ids = [r['external_id'] for r in rows if r['status']=='PERSISTED']
        if persisted_ids:
            from universal_supplier.automatic_matching import run_post_ingest
            try:
                identity = run_post_ingest(config, code, persisted_ids)
            except Exception as error:
                identity = {'status':'POST_PROCESSING_FAILED','matching_applied':False,
                            'error_type':type(error).__name__}
        partial=bool(terminated or len(entries)<len(data['entries']) or any(r['status']=='REVIEW' for r in rows) or identity.get('status')=='POST_PROCESSING_FAILED')
        return {'status':'partial' if partial else 'succeeded','mode':mode,'supplier_code':code,'rows':rows,'manifest_sha256':settings['source_manifest_sha256'],'termination_reason':terminated,'source_scope':'BOUNDED_VERIFIED_MANIFEST','discovery_complete':False,'deactivation_allowed':False,'matching_applied':identity.get('matching_applied',False),'identity_post_processing':identity,'source_writes':sum(r['status']=='PERSISTED' and r['exact_noop'] is not True for r in rows),'source_write_calls':sum(r['status']=='PERSISTED' for r in rows),'network_allowed':mode=='LIVE'}
    return run

def register_source_jobs(registry):
    for code in SOURCES:registry.register(code,make_source_runner(code),supported_crawl_types={'full'})
