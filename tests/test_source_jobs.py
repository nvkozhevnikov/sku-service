"""Real parsers through the real registry/orchestrator; no SQL or network."""
from datetime import datetime,timezone
from dataclasses import replace
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
import hashlib,json,socket
from unittest.mock import patch
import pytest

from universal_supplier.control_plane.adapter_registry import default_adapter_registry
from universal_supplier.control_plane.source_jobs import SOURCES,make_source_runner,parse_existing
from universal_supplier.control_plane.queue import CrawlJob,InMemoryJobQueue
from universal_supplier.control_plane.orchestrator import Orchestrator

FIX=Path(__file__).parent/'fixtures'

def saved_case(code):
    if code in ('ltt_msk','ltt_sib','ltt_yug','uss_stanko'):
        provenance=json.loads((FIX/'ltt_uss/PROVENANCE.json').read_text(encoding='utf-8'))
        names={'ltt_msk':'ltt_msk__machine_numeric_price.html','ltt_sib':'ltt_sib__machine_numeric_mb203c.html','ltt_yug':'ltt_yug__machine_numeric_mf362.html','uss_stanko':'uss_stanko__dmtg_detail.html'}
        item=next(r for r in provenance if r['filename']==names[code])
        return (FIX/'ltt_uss'/names[code]).read_bytes(),item['source_url'],item.get('charset','utf-8'),'PACKAGED-EVIDENCE'
    if code=='kami':
        p=FIX/'kami/ironmac_single_sale.html'
        from bs4 import BeautifulSoup
        body=p.read_bytes();node=BeautifulSoup(body.decode(),'html.parser').select_one('link[rel="canonical"]')
        return body,node['href'],'utf-8','PACKAGED-EVIDENCE'
    if code=='beka_mak_tr':
        # Existing synthetic manufacturer regression card, explicitly labelled.
        import runpy
        value=runpy.run_path(str(Path(__file__).parent/'test_manufacturer_collection.py'))
        return value['CARD'].encode(),value['URL'],'utf-8','SYNTHETIC_TEST_DOUBLE'
    if code=='vekprom':
        body=(FIX/'commercial/real/vekprom_bmsy_440dgh_sanitized.html').read_bytes()
        from universal_supplier.adapters.vekprom import parse_vekprom_detail
        return body,parse_vekprom_detail(body.decode()).source_url,'utf-8','PACKAGED-EVIDENCE_HTTP_STATUS_UNKNOWN'
    provenance=json.loads((FIX/'commercial/real/manifest.json').read_text(encoding='utf-8'))
    name={'beka_mak':'bekamak_bmsy_440dgh_sanitized.html','intervesp':'intervesp_bmsy_440dgh_wp2_sanitized.html','vekprom':'vekprom_bmsy_440dgh_sanitized.html'}[code]
    item=next(r for r in provenance['files'] if r['filename']==name)
    return (FIX/'commercial/real'/name).read_bytes(),item['url'],'utf-8','PACKAGED-EVIDENCE_HTTP_STATUS_UNKNOWN'

def job_for(tmp_path,code,**settings):
    from universal_supplier.ltt_uss_capture import sanitise_for_charset
    from universal_supplier.http_capture import sanitise_html
    body,url,charset,evidence_class=saved_case(code)
    clean=sanitise_for_charset(body,charset) if code.startswith('ltt_') or code=='uss_stanko' else sanitise_html(body)
    product=parse_existing(code,clean.decode(),url)
    external=product.external_id if code=='kami' else product.site_internal_id
    folder=tmp_path/code;folder.mkdir(parents=True,exist_ok=True);(folder/'body.html').write_bytes(body)
    entry={'url':url,'file':'body.html','sha256':hashlib.sha256(body).hexdigest(),'http_status':200,'http_status_basis':'INJECTED_TEST_DOUBLE_NOT_HISTORICAL_HTTP_PROOF','content_type':'text/html; charset='+charset,'charset':charset,'observed_at':'2026-10-08T08:00:00+00:00','source_external_id':external,'evidence_class':evidence_class}
    manifest=folder/'manifest.json';manifest.write_text(json.dumps({'format':'source-job-manifest/v1','supplier_code':code,'entries':[entry]},ensure_ascii=False),encoding='utf-8')
    crawl={'source_manifest_path':str(manifest),'source_manifest_sha256':hashlib.sha256(manifest.read_bytes()).hexdigest(),**settings}
    job=CrawlJob(1,1,code,code,url,'MANUAL','full','running',datetime(2026,10,19,tzinfo=timezone.utc),{'crawl_settings':crawl})
    return job,manifest

def deny(*a,**kw):raise AssertionError('NETWORK_OR_SQL_FORBIDDEN')

@pytest.mark.parametrize('code',SOURCES)
def test_saved_parser_runs_through_shared_registry_without_network_or_db(tmp_path,code):
    job,_=job_for(tmp_path,code)
    with patch.object(socket,'create_connection',deny),patch.object(socket.socket,'connect',deny),patch('psycopg.connect',deny):
        registry=default_adapter_registry()
        result=registry.resolve(code)(job,None)
    assert registry.supports(code,'full')
    assert result['status']=='succeeded',result
    assert len(result['rows'])==1 and result['rows'][0]['status']=='OFFLINE_PARSED'
    assert result['rows'][0]['observed_at']=='2026-10-08T08:00:00+00:00'
    assert result['matching_applied'] is False and result['source_writes']==0
    assert result['deactivation_allowed'] is False and result['discovery_complete'] is False

def test_registry_preserves_existing_runners():
    registry=default_adapter_registry()
    assert set(registry.names)==set(SOURCES)|{'partner_st','optimum','ivstankomaster'}

def test_rc_existing_runners_use_shared_safe_pipeline(monkeypatch):
    from universal_supplier.control_plane.adapter_registry import _post_ingest_identity
    calls=[]
    monkeypatch.setattr('universal_supplier.control_plane.adapter_registry.run_post_ingest',
        lambda cfg,code: calls.append(code) or {'automatic_matching':True,'matching_applied':False})
    assert _post_ingest_identity(None,'partner_st')['automatic_matching']
    assert _post_ingest_identity(None,'optimum')['automatic_matching']
    assert calls==['partner_st','optimum']


def test_pinned_rc_calculates_before_initial_apply_approval(monkeypatch):
    from universal_supplier.automatic_matching import runtime_matching_policy
    monkeypatch.delenv('STERBRUST_RC_MATCHING_APPLY_APPROVED',raising=False)
    config=SimpleNamespace(host='127.0.0.1',port=55461,dbname='sterbrust_unified_rc_20261019')
    policy=runtime_matching_policy(config)
    assert policy['automatic_matching'] and not policy['apply_allowed']
    assert policy['mode']=='DRY_RUN_PENDING_INITIAL_APPROVAL'

@pytest.mark.parametrize('code',SOURCES)
def test_live_wrapper_uses_existing_client_contract_with_saved_response_only(tmp_path,monkeypatch,code):
    from test_source_runtime import setup
    run,job,backend,calls,url=setup(code)
    job.settings_snapshot['crawl_settings']['persist_source_data']=False
    monkeypatch.setenv('STERBRUST_SOURCE_RUNTIME_ENABLED','YES')
    monkeypatch.setenv('STERBRUST_ALLOW_SUPPLIER_HTTP','YES')
    monkeypatch.setenv('SOURCE_RUNNER_EVIDENCE_ROOT',str(tmp_path/'output'))
    before=datetime.now(timezone.utc)
    with patch.object(socket.socket,'connect',deny),patch('psycopg.connect',deny):result=run()
    after=datetime.now(timezone.utc)
    observed=datetime.fromisoformat(result['rows'][0]['observed_at'])
    assert before<=observed<=after
    assert observed!=datetime(2026,10,8,8,tzinfo=timezone.utc)
    # A parser-only request has no durable SQL receipt. The current resume
    # contract must not present it as a successfully committed source job.
    assert result['status']=='partial',result
    assert result['termination_reason']=='UNCERTAIN_OR_FAILED_RESUME_REQUIRES_OPERATOR'
    from universal_supplier.control_plane.source_discovery import HOSTS
    assert [x[0] for x in calls]==['https://'+HOSTS[code]+'/robots.txt',url]
    assert result['sql_committed']==0 and result['matching_applied'] is False and not backend.writes


def test_multi_variant_uss_is_review_and_never_written(tmp_path):
    job,manifest=job_for(tmp_path,'uss_stanko')
    data=json.loads(manifest.read_text(encoding='utf-8'))
    meta=next(r for r in json.loads((FIX/'ltt_uss/PROVENANCE.json').read_text(encoding='utf-8')) if r['filename']=='uss_stanko__mrcm_detail.html')
    body=(FIX/'ltt_uss'/meta['filename']).read_bytes()
    (manifest.parent/'body.html').write_bytes(body)
    data['entries'][0].update(url=meta['source_url'],sha256=hashlib.sha256(body).hexdigest(),charset=meta.get('charset','utf-8'))
    manifest.write_text(json.dumps(data),encoding='utf-8')
    job.settings_snapshot['crawl_settings']['source_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    result=make_source_runner('uss_stanko',repository_factory=deny)(job,None)
    assert result['status']=='partial' and 'MULTI_VARIANT' in result['rows'][0]['reason']
    assert result['source_writes']==0

@pytest.mark.parametrize('settings,error',[({'execution_mode':'LIVE'},'LIVE_SUPPLIER_HTTP_NOT_AUTHORIZED'),({'persist_source_data':True},'SOURCE_WRITES_NOT_AUTHORIZED')])
def test_permissions_fail_before_factories_or_io(tmp_path,monkeypatch,settings,error):
    monkeypatch.delenv('STERBRUST_ALLOW_SUPPLIER_HTTP',raising=False);monkeypatch.delenv('STERBRUST_ALLOW_SOURCE_WRITES',raising=False)
    job,_=job_for(tmp_path,'vekprom',**settings)
    runner=make_source_runner('vekprom',repository_factory=deny,transport_factory=deny)
    with pytest.raises(PermissionError,match=error):runner(job,None)

def test_tampered_evidence_and_cross_namespace_are_rejected(tmp_path):
    job,manifest=job_for(tmp_path,'vekprom')
    (manifest.parent/'body.html').write_text('tampered')
    with pytest.raises(ValueError,match='EVIDENCE_SHA256'):make_source_runner('vekprom')(job,None)
    with pytest.raises(ValueError,match='NAMESPACE'):make_source_runner('kami')(job,None)

def test_deadline_is_partial_without_parsing_or_writes(tmp_path):
    job,_=job_for(tmp_path,'vekprom');ticks=iter([0,0,999])
    result=make_source_runner('vekprom',clock=lambda:next(ticks))(job,None)
    assert result['status']=='partial' and result['rows']==[]
    assert result['termination_reason']=='MAX_RUN_DURATION_EXCEEDED'

def test_preflight_hash_validation_is_also_time_bounded(tmp_path):
    job,_=job_for(tmp_path,'vekprom');ticks=iter([0,999])
    with pytest.raises(TimeoutError,match='MANIFEST_VALIDATION_TIME_BUDGET'):
        make_source_runner('vekprom',repository_factory=deny,transport_factory=deny,clock=lambda:next(ticks))(job,None)

@pytest.mark.parametrize('code',SOURCES)
@pytest.mark.parametrize('origin',['MANUAL','SCHEDULED'])
def test_existing_queue_and_orchestrator_use_same_adapter_and_deduplicate(tmp_path,origin,code):
    job,_=job_for(tmp_path,code)
    queue=InMemoryJobQueue();settings=job.settings_snapshot
    first=queue.enqueue(1,trigger_type=origin,crawl_type='full',settings_snapshot=settings)
    second=queue.enqueue(1,trigger_type='SCHEDULED',crawl_type='full',settings_snapshot=settings)
    assert first.enqueued and second.status=='skipped_already_queued'
    class Queue:
        lease_seconds=300
        def complete(self,id,status,**kw):self.status=status
        def heartbeat(self,id):return True
    q=Queue()
    @contextmanager
    def lease(domain):yield SimpleNamespace(acquired=True)
    worker=Orchestrator(None,queue=q,registry=default_adapter_registry(),lease_factory=lease)
    try:
        with patch.object(socket.socket,'connect',deny),patch('psycopg.connect',deny):worker._run_job(replace(job,trigger_type=origin))
    finally:worker.pool.shutdown(wait=True)
    assert q.status=='succeeded'

@pytest.mark.parametrize('code',SOURCES)
def test_existing_passive_writer_receives_original_capture_and_replay_is_noop(tmp_path,monkeypatch,code):
    from universal_supplier.http_capture import sanitise_html
    from universal_supplier.ltt_uss_capture import sanitise_for_charset
    job,manifest=job_for(tmp_path,code,persist_source_data=True)
    data=json.loads(manifest.read_text(encoding='utf-8'));entry=data['entries'][0]
    body,url,charset,_=saved_case(code)
    evidence=sanitise_for_charset(body,charset) if code.startswith('ltt_') or code=='uss_stanko' else sanitise_html(body)
    # Synthetic original capture metadata for an in-memory writer contract test.
    entry['capture']={'requested_url':url,'final_url':url,'http_status':200,'content_type':'text/html; charset='+charset,'observed_at':entry['observed_at'],'response_sha256':hashlib.sha256(body).hexdigest(),'evidence_sha256':hashlib.sha256(evidence).hexdigest(),'evidence_ref':'capture://offline-test/example.html'}
    manifest.write_text(json.dumps(data),encoding='utf-8');job.settings_snapshot['crawl_settings']['source_manifest_sha256']=hashlib.sha256(manifest.read_bytes()).hexdigest()
    monkeypatch.setenv('STERBRUST_ALLOW_SOURCE_WRITES','YES')
    events=set();calls=[]
    class Writer:
        def persist_commercial_observation(self,product,capture):
            assert product.supplier.replace('-','_')==code and capture.observed_at.isoformat()==entry['observed_at']
            key=(code,capture.fingerprint);noop=key in events;events.add(key);calls.append(capture)
            return {'exact_noop':noop}
        def persist_kami_observation(self,card,capture):
            assert card.supplier_code=='kami'
            key=(code,capture.fingerprint);noop=key in events;events.add(key);calls.append(capture)
            return {'exact_noop':noop}
        def close(self):pass
    runner=make_source_runner(code,repository_factory=lambda cfg:Writer())
    matching_calls=[]
    monkeypatch.setattr('universal_supplier.automatic_matching.run_post_ingest',
        lambda cfg,source,ids: matching_calls.append((source,ids)) or {'matching_applied':False,'automatic_matching':True})
    with patch.object(socket.socket,'connect',deny),patch('psycopg.connect',deny):
        first=runner(job,None);second=runner(job,None)
    assert first['status']==second['status']=='succeeded'
    assert first['rows'][0]['exact_noop'] is False and second['rows'][0]['exact_noop'] is True
    assert len(events)==1 and len(calls)==2
    assert first['matching_applied'] is False
    assert len(matching_calls)==2 and all(source==code and ids==[first['rows'][0]['external_id']] for source,ids in matching_calls)
    assert first['identity_post_processing']['automatic_matching']
