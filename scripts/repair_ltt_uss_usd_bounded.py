"""Authorized UF100 repair from saved bytes, within operator 30-minute session."""
from pathlib import Path
from dataclasses import asdict,replace
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
import copy
import sys
import xml.etree.ElementTree as ET
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r
from universal_supplier.http_capture import HttpResponse
from universal_supplier.ltt_uss_capture import capture_source_html
from universal_supplier.commercial_persistence import product_card_from_snapshot,observation_from_snapshot

PHASE=r.OUT/'USD_30MIN_2026-10-14'
ORIGINAL=r.OUT/'ROWS/uss_stanko/000178_9d7d063a1dbb2797.json'


def prepare():
    session=r.load(PHASE/'SESSION.json');original=r.load(ORIGINAL)
    assert original['phase']=='TERMINAL' and original['outcome']=='PARSE_ERROR'
    assert r.digest(ORIGINAL.read_bytes())==session['original_journal_sha256']
    tests=ET.parse(PHASE/'TESTS.xml').getroot()[0]
    assert all(int(tests.get(key,0))==0 for key in ('failures','errors','skipped'))
    r.save(PHASE/'TEST_RECEIPT.json',dict(pass_count=int(tests.get('tests')),fail=0,error=0,skip=0,supplier_http_gets=0))
    record=r.load(ORIGINAL.with_name(ORIGINAL.stem+'.response1.json'));raw=(r.OUT/record['raw_path']).read_bytes()
    assert r.digest(raw)==record['response_sha256']==original['result']['response_sha256']
    class SavedClient:
        def get_validated(self,url,*,timeout_seconds,validator):
            assert url==original['url'];value=dict(record['response']);value['redirects']=tuple(value['redirects'])
            validator(value['final_url'])
            for item in value['redirects']:validator(item)
            return HttpResponse(**value,body=raw)
    result=capture_source_html(original['url'],supplier='uss_stanko',evidence_dir=PHASE/'OFFLINE_EVIDENCE',client=SavedClient())
    assert result.status.value=='SUCCESS' and result.response_sha256==original['result']['response_sha256']
    assert result.evidence_sha256==original['result']['evidence_sha256']
    observed=datetime.fromisoformat(original['result']['observed_at'])
    result=replace(result,observed_at=observed,capture=replace(result.capture,observed_at=observed))
    product=r.parse('uss_stanko',result.evidence_body,result.final_url)
    assert product.site_internal_id=='uss:mach/frez/uf100.htm'
    assert product.price.current_price==Decimal('21312') and product.price.currency=='USD' and product.price.raw_text=='21 312 USD'
    card=product_card_from_snapshot(product,result.capture);obs=observation_from_snapshot(product,result.capture)
    assert card.price==obs.price==Decimal('21312') and card.currency==obs.currency=='USD'
    metadata=asdict(result);metadata.pop('evidence_body');metadata['response_record']=str(ORIGINAL.with_name(ORIGINAL.stem+'.response1.json').relative_to(r.OUT))
    revision=dict(supplier='uss_stanko',index=178,url=original['url'],phase='CAPTURED',result=metadata,
        attempts=copy.deepcopy(original['attempts']),reviewed_extraction=dict(old_outcome='PARSE_ERROR',old_reason='USD_UNSUPPORTED',
            new_semantics='VALID_EXPLICIT_FOREIGN_CURRENCY_PRICE',original_journal=str(ORIGINAL.relative_to(r.OUT)),
            original_journal_sha256=session['original_journal_sha256'],repair_http_gets=0,fx_conversion='NONE'))
    run=r.Run.__new__(r.Run);run.c=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[],fetchone=lambda:None))
    run.prepare(revision,PHASE/'PREPARED.json');assert revision['outcome']=='PERSISTED'
    state=r.load(r.OUT/'CHECKPOINT.json');run.state=state;run.manifests,run.scopes=r.frozen_inputs();verified=0
    for supplier in r.SOURCES:
        for row in run.account(supplier):
            if row['outcome']=='PERSISTED':run.product(row);verified+=1
    assert verified==2342
    r.save(PHASE/'OFFLINE_PREPARATION.json',dict(status='PREPARED',prior_payloads_unchanged=verified,repair_http_gets=0,
        original_journal_sha256=session['original_journal_sha256'],after_code_hashes=r.code_hashes(),checked_at=r.now()))
    print('UF10021312USD prepared; HTTP0; all2342 prior payload hashes unchanged',flush=True)


def apply():
    offline=r.load(PHASE/'OFFLINE_PREPARATION.json');assert offline['after_code_hashes']==r.code_hashes()
    r.check_http_budget(datetime.fromisoformat(r.load(PHASE/'SESSION.json')['http_cutoff_at']),reserve_seconds=180)
    system=r.verify_system();_,cfg=r.config();repo=r.PostgresRepository(r.PostgresConfig(**cfg))
    run=r.Run.__new__(r.Run);run.repo=repo;run.c=repo.connection;run.state=r.load(r.OUT/'CHECKPOINT.json');run.manifests,run.scopes=r.frozen_inputs()
    assert run.state['status']=='RUN1_BLOCKED'
    try:
        current=r.fingerprints(run.c);assert current==r.load(PHASE/'BEFORE/DB_AFTER.json')['fingerprints']
        assert tuple(current[k]['count'] for k in ('source_products','offers','supplier_http_captures','offer_commercial_observations'))==(2344,2344,2422,2422)
        constraints=run.c.execute("SELECT conname,pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid IN ('offers'::regclass,'offer_commercial_observations'::regclass) AND contype='c'").fetchall()
        columns=run.c.execute("SELECT table_name,data_type,character_maximum_length FROM information_schema.columns WHERE table_schema='public' AND table_name IN ('offers','offer_commercial_observations') AND column_name='currency' ORDER BY table_name").fetchall()
        assert len(columns)==2 and all(row[1]=='character varying' and row[2]==3 for row in columns)
        assert not any('RUB' in definition for _,definition in constraints)
        assert any(name=='offer_commercial_observations_currency_ck' and '[A-Z]{3}' in definition for name,definition in constraints)
        r.save(PHASE/'BASELINE_AND_CURRENCY_STORAGE.json',dict(**system,fingerprints=current,columns=columns,constraints=constraints,currency_storage_supported=True,checked_at=r.now()))
        assert r.digest(ORIGINAL.read_bytes())==offline['original_journal_sha256']
        revision=r.load(PHASE/'PREPARED.json');revision['phase']='CAPTURED';path=r.OUT/'REVISED_ROWS/uss_stanko'/ORIGINAL.name
        run.prepare(revision,path);assert revision['outcome']=='PERSISTED'
        product,capture=run.product(revision);revision['db_verification'],revision['recovered_committed_event']=r.guarded_commit(repo,product,capture)
        r.verify_persisted(repo,product,capture)
        stored=run.c.execute('''SELECT o.price,o.currency,b.price,b.currency,b.price_raw,b.observed_at,p.canonical_url
            FROM suppliers s JOIN source_products p ON p.supplier_id=s.id JOIN offers o ON o.source_product_id=p.id
            JOIN supplier_http_captures c ON c.source_product_id=p.id JOIN offer_commercial_observations b ON b.capture_id=c.id
            WHERE s.code='uss_stanko' AND p.external_id=%s AND c.capture_fingerprint=%s''',(product.site_internal_id,capture.fingerprint)).fetchone()
        assert stored[:5]==(Decimal('21312'),'USD',Decimal('21312'),'USD','21 312 USD')
        assert stored[5]==capture.observed_at and stored[6]==revision['url']
        revision.update(phase='TERMINAL',completed_at=r.now());r.save(path,revision)
        run.state.setdefault('reviewed_row_revisions',{}).setdefault('uss_stanko',{})['178']=dict(path=str(path.relative_to(r.OUT)),original_journal_sha256=offline['original_journal_sha256'])
        recon=run.reconcile('uss_stanko');assert recon['processed']==179 and recon['terminal_counts']=={'PERSISTED':117,'AMBIGUOUS':50,'MULTI_VARIANT':12}
        after=r.fingerprints(run.c);assert tuple(after[k]['count'] for k in ('source_products','offers','supplier_http_captures','offer_commercial_observations'))==(2345,2345,2423,2423)
        assert r.digest(ORIGINAL.read_bytes())==offline['original_journal_sha256']
        r.save(PHASE/'REPAIR_RECEIPT.json',dict(status='UF100_REPAIRED_AND_LIVE_VERIFIED',price='21312',currency='USD',raw_price='21 312 USD',
            stable_id=product.site_internal_id,url=product.source_url,observed_at=str(capture.observed_at),response_sha256=capture.response_sha256,
            repair_http_gets=0,fx_conversion='NONE',historical_journal_preserved=True,db_verification=revision['db_verification'],fingerprints=after,checked_at=r.now()))
        transition=dict(status='RUN1_RESUMABLE_AFTER_CURRENCY_FIX',previous_status='RUN1_BLOCKED_USD_PRICE',after_code_hashes=r.code_hashes(),
            before_code_hashes=run.state['code_hashes'],uf100_repaired=True,repair_http_gets=0,cursor_before=179,cursor_after=179,
            test_receipt='USD_30MIN_2026-10-14/TEST_RECEIPT.json',repair_receipt='USD_30MIN_2026-10-14/REPAIR_RECEIPT.json',checked_at=r.now())
        r.save(PHASE/'TRANSITION.json',transition)
        run.state.update(status=transition['status'],code_hashes=r.code_hashes(),reviewed_transition='USD_30MIN_2026-10-14/TRANSITION.json',usd_price_fix='VERIFIED',uf100_repaired=True)
        run.state.pop('blocker',None);own=run.state['sources']['uss_stanko'];own['status']='RUN1_RUNNING'
        for key in ('blocker','blocked_url','blocked_index'):own.pop(key,None)
        r.save(r.OUT/'CODE_SEAL.json',r.code_hashes());r.save(r.OUT/'CHECKPOINT.json',run.state);r.save(PHASE/'AFTER_CHECKPOINT.json',run.state)
        print('USD repair LIVE-VERIFIED; 2345/2345/2423/2423; resumable USS cursor179 retained; HTTP0',flush=True)
    finally:repo.close()


if __name__=='__main__':
    prepare() if sys.argv[1]=='prepare' else apply()
