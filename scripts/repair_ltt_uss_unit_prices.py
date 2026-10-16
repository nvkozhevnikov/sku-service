"""Operator-reviewed saved-response repair. No supplier HTTP; no schema changes."""
from pathlib import Path
from dataclasses import asdict, replace
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import patch
import argparse
import copy
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import run_ltt_uss_run1 as r
from universal_supplier.ltt_uss_capture import capture_source_html
from universal_supplier.http_capture import HttpResponse
from universal_supplier.commercial_persistence import product_card_from_snapshot, observation_from_snapshot

PHASE=r.OUT/'UNIT_FIX_2026-10-14'


def saved_extractions():
    inputs=r.load(PHASE/'INPUT_VERIFICATION.json')['rows'];assert len(inputs)==8
    rows=[]
    for item in inputs:
        original_path=r.OUT/item['journal'];original=r.load(original_path)
        assert r.digest(original_path.read_bytes())==item['journal_sha256']
        assert original['phase']=='TERMINAL' and original['outcome']=='AMBIGUOUS'
        response_path=original_path.with_name(original_path.stem+'.response1.json')
        record=r.load(response_path);raw=(r.OUT/record['raw_path']).read_bytes()
        assert r.digest(raw)==record['response_sha256']==original['result']['response_sha256']
        class SavedClient:
            def get_validated(self,url,*,timeout_seconds,validator):
                assert url==original['url'];value=dict(record['response'])
                value['redirects']=tuple(value['redirects']);validator(value['final_url'])
                for redirect in value['redirects']:validator(redirect)
                return HttpResponse(**value,body=raw)
        with patch('universal_supplier.http_capture.UrllibPublicHttpClient',side_effect=AssertionError('Repair HTTP forbidden')):
            result=capture_source_html(original['url'],supplier='ltt_sib',evidence_dir=PHASE/'OFFLINE_EVIDENCE',client=SavedClient())
        assert result.status.value=='SUCCESS'
        observed=datetime.fromisoformat(original['result']['observed_at'])
        result=replace(result,observed_at=observed,capture=replace(result.capture,observed_at=observed))
        assert result.response_sha256==original['result']['response_sha256']
        assert result.evidence_sha256==original['result']['evidence_sha256']
        product=r.parse('ltt_sib',result.evidence_body,result.final_url)
        assert product.site_internal_id==item['id'] and product.price.raw_text==item['raw']
        assert product.price.diagnostics==('sales_unit_raw:бухта',)
        assert product.price.currency=='RUB' and product.price.current_price is not None
        card=product_card_from_snapshot(product,result.capture);obs=observation_from_snapshot(product,result.capture)
        assert card.quantity is None and obs.price==product.price.current_price
        assert card.raw_data['price_extraction']['diagnostics']==product.price.diagnostics
        assert obs.extraction_evidence['diagnostics']==product.price.diagnostics
        metadata=asdict(result);metadata.pop('evidence_body');metadata['response_record']=str(response_path.relative_to(r.OUT))
        revision=dict(supplier='ltt_sib',index=original['index'],url=original['url'],phase='CAPTURED',
            result=metadata,attempts=copy.deepcopy(original['attempts']),
            reviewed_extraction=dict(original_journal=item['journal'],original_journal_sha256=item['journal_sha256'],
                old_outcome='AMBIGUOUS',reason='unsupported explicit sales-unit grammar',supplier_http_gets=0,
                raw_price=item['raw'],sales_unit_raw='бухта',original_observed_at=item['original_time']))
        run=r.Run.__new__(r.Run)
        run.c=SimpleNamespace(execute=lambda *a:SimpleNamespace(fetchall=lambda:[],fetchone=lambda:None))
        prepared=PHASE/'PREPARED'/original_path.name;run.prepare(revision,prepared)
        assert revision['outcome']=='PERSISTED'
        rows.append(dict(index=item['index'],external_id=item['id'],price=str(product.price.current_price),
            raw_price=item['raw'],sales_unit_raw='бухта',url=original['url'],response_sha256=result.response_sha256,
            evidence_sha256=result.evidence_sha256,observed_at=str(observed),prepared=str(prepared.relative_to(r.OUT))))
    return rows


def prepare():
    assert r.load(r.OUT/'CHECKPOINT.json')['status']=='RUN1_BLOCKED'
    rows=saved_extractions()
    # Reparse all previously successful rows with the new parser before any SQL.
    verified=0;run=r.Run.__new__(r.Run)
    for supplier in r.SOURCES:
        for path in (r.OUT/'ROWS'/supplier).glob('*.json'):
            if '.response' in path.name:continue
            row=r.load(path)
            if row.get('outcome')=='PERSISTED':run.product(row);verified+=1
    assert verified==1242
    pending=r.OUT/'ROWS/ltt_sib/000881_47e00f61bd54eab2.json'
    row=r.load(pending);assert row['phase']=='REQUESTING' and 'attempts' not in row and 'result' not in row
    assert not list(pending.parent.glob(pending.stem+'.response*.json'))
    events=[r.load(p) for p in (r.OUT/'HTTP').glob('*.json')]
    assert len(events)==1262
    assert not any(e['request_started_at']>=row['intent']['observed_at'] for e in events)
    r.atomic_bytes(PHASE/'BEFORE/PENDING_881.json',pending.read_bytes())
    receipt=dict(status='OFFLINE_REPAIR_PREPARED',saved_rows=rows,repair_http_gets=0,
        previous_persisted_payloads_unchanged=verified,pending_intent_only=True,pending_journal_sha256=r.digest(pending.read_bytes()),
        before_code_hashes=r.load(PHASE/'BEFORE/CODE_SEAL.json'),after_code_hashes=r.code_hashes(),checked_at=r.now())
    r.save(PHASE/'OFFLINE_PREPARATION.json',receipt)
    print(r.encoded({k:receipt[k] for k in ('status','repair_http_gets','previous_persisted_payloads_unchanged')}).decode())


def apply():
    offline=r.load(PHASE/'OFFLINE_PREPARATION.json');assert offline['status']=='OFFLINE_REPAIR_PREPARED'
    assert offline['after_code_hashes']==r.code_hashes()
    tests=r.load(PHASE/'TEST_RECEIPT.json');assert tests['fail']==tests['error']==tests['skip']==0
    assert tests['focused_pass']>0 and tests['regression_pass']>0
    state=r.load(r.OUT/'CHECKPOINT.json');assert state['status']=='RUN1_BLOCKED'
    system=r.verify_system();assert system['system_identifier']==state['system_identifier']
    _,runtime=r.config();repo=r.PostgresRepository(r.PostgresConfig(**runtime))
    run=r.Run.__new__(r.Run);run.repo=repo;run.c=repo.connection;run.state=state;run.manifests,run.scopes=r.frozen_inputs()
    try:
        current=r.fingerprints(run.c);prior=r.load(PHASE/'BEFORE/FINAL_DB_VERIFICATION.json')['fingerprints']
        assert current==prior
        assert tuple(current[n]['count'] for n in ('source_products','offers','supplier_http_captures','offer_commercial_observations'))==(1278,1278,1322,1322)
        r.save(PHASE/'RETAINED_BASELINE_VERIFIED.json',dict(**system,fingerprints=current,checked_at=r.now()))
        msk_id=r.load(r.OUT/'SUPPLIERS/ltt_msk/BEFORE.json')['supplier_id']
        # Check every closed MSK event and projection before sealing its namespace.
        state['sources']['ltt_msk']['closed_namespace_fingerprints']=r.namespace_digest(run.c,msk_id)
        run.reconcile('ltt_msk',True,closed=True)
        run.reconcile('ltt_sib')
        repaired=[];state['reviewed_row_revisions']={'ltt_sib':{}}
        for item in r.load(PHASE/'INPUT_VERIFICATION.json')['rows']:
            original=r.OUT/item['journal'];assert r.digest(original.read_bytes())==item['journal_sha256']
            revision=r.load(PHASE/'PREPARED'/original.name)
            # Repeat preparation against real historical exact-URL identity guards.
            revision['phase']='CAPTURED';path=r.OUT/'REVISED_ROWS/ltt_sib'/original.name
            run.prepare(revision,path);assert revision['outcome']=='PERSISTED'
            product,capture=run.product(revision)
            revision['db_verification'],revision['recovered_committed_event']=r.guarded_commit(repo,product,capture)
            r.verify_persisted(repo,product,capture)
            assert product.site_internal_id==item['id'] and str(capture.observed_at)==item['original_time']
            assert product.price.raw_text==item['raw'] and product.price.diagnostics==('sales_unit_raw:бухта',)
            revision.update(phase='TERMINAL',completed_at=r.now());r.save(path,revision)
            state['reviewed_row_revisions']['ltt_sib'][str(item['index'])]=dict(path=str(path.relative_to(r.OUT)),original_journal_sha256=item['journal_sha256'])
            repaired.append(dict(index=item['index'],id=item['id'],db_verification=revision['db_verification'],price=str(product.price.current_price),
                raw_price=item['raw'],sales_unit_raw='бухта',capture_fingerprint=capture.fingerprint,original_time=item['original_time']))
        receipt=run.reconcile('ltt_sib');assert receipt['processed']==881 and receipt['terminal_counts']=={'PERSISTED':881}
        assert not run.c.execute('SELECT supplier_id,external_id FROM source_products GROUP BY supplier_id,external_id HAVING count(*)>1').fetchall()
        assert not run.c.execute('SELECT source_product_id FROM offers GROUP BY source_product_id HAVING count(*)>1').fetchall()
        for item in r.load(PHASE/'INPUT_VERIFICATION.json')['rows']:
            assert r.digest((r.OUT/item['journal']).read_bytes())==item['journal_sha256']
        r.save(PHASE/'REPAIR_RECEIPT.json',dict(status='SAVED_UNIT_PRICE_ROWS_REPAIRED_AND_VERIFIED',saved_rows_repaired=8,
            repair_http_gets=0,rows=repaired,fingerprints=r.fingerprints(run.c),reconciliation=receipt,checked_at=r.now()))
        pending=r.OUT/'ROWS/ltt_sib/000881_47e00f61bd54eab2.json';assert r.digest(pending.read_bytes())==offline['pending_journal_sha256']
        journal=r.load(pending);journal.update(phase='NEW',reviewed_intent_reset=dict(previous_phase='REQUESTING',
            previous_intent=journal.pop('intent'),basis='proven intent-only: no HTTP callback, response, or SQL',transition='UNIT_FIX_2026-10-14/TRANSITION.json'))
        r.save(pending,journal)
        transition=dict(status='RUN1_RESUMABLE_AFTER_PRICE_UNIT_FIX',previous_status='RUN1_BLOCKED',operator_decision='EXPLICIT_UNIT_PRICE',
            before_code_hashes=state['code_hashes'],after_code_hashes=r.code_hashes(),saved_rows_repaired=8,repair_http_gets=0,
            historical_ambiguous_journals_preserved=True,pending_index_881='REQUESTING_INTENT_ONLY_TO_NEW',cursor_before=881,cursor_after=881,
            test_receipt='UNIT_FIX_2026-10-14/TEST_RECEIPT.json',repair_receipt='UNIT_FIX_2026-10-14/REPAIR_RECEIPT.json',checked_at=r.now())
        r.save(PHASE/'TRANSITION.json',transition)
        state.update(status=transition['status'],code_hashes=r.code_hashes(),reviewed_transition='UNIT_FIX_2026-10-14/TRANSITION.json')
        state.pop('blocker',None);own=state['sources']['ltt_sib'];own.update(status='RUN1_RUNNING');own.pop('blocker',None)
        r.save(r.OUT/'CODE_SEAL.json',r.code_hashes());r.save(r.OUT/'CHECKPOINT.json',state)
        r.save(PHASE/'AFTER_CHECKPOINT.json',state)
        print(r.encoded(dict(status=transition['status'],saved_rows_repaired=8,repair_http_gets=0,
            counts={n:r.fingerprints(run.c)[n]['count'] for n in ('source_products','offers','supplier_http_captures','offer_commercial_observations')})).decode())
    finally:repo.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('mode',choices=('prepare','apply'));args=parser.parse_args()
    prepare() if args.mode=='prepare' else apply()
