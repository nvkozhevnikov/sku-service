"""Authorized RUN1 only: one worker, frozen inputs, durable prepared-row journal.

No crawler/scheduler SQL tables, matching, deactivation, imports or RUN2 calls.
Each response is saved before parsing; each parsed payload before its transaction.
An uncertain request is never automatically repeated on recovery.
"""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
from collections import Counter
import argparse
import hashlib
import json
import os
import random
import sys
import time
import msvcrt
import psycopg
from psycopg import sql
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.run_ltt_uss_live_canary import BASE, SOURCES, ALLOWED, parse, verify_persisted
from scripts.run_ltt_uss_offline_canary import fingerprints
from scripts.continue_ltt_uss_category_canary import verify_system, config
from universal_supplier.http_capture import UrllibPublicHttpClient, HttpResponse, CaptureStatus
from universal_supplier.ltt_uss_capture import capture_source_html, safe_source_url
from universal_supplier.commercial_persistence import HttpCapture, product_card_from_snapshot, observation_from_snapshot
from universal_supplier.postgres import PostgresConfig, PostgresRepository

OUT = BASE / 'FULL_RUN1_2026-10-14'
STOP = {'PARSE_ERROR','SOURCE_BLOCKED','IDENTITY_DRIFT','INFLIGHT_REQUEST_UNRESOLVED'}
CODE = ('scripts/run_ltt_uss_run1.py','scripts/run_ltt_uss_live_canary.py',
        'universal_supplier/http_capture.py','universal_supplier/ltt_uss_capture.py',
        'universal_supplier/postgres.py','universal_supplier/commercial_persistence.py',
        'universal_supplier/adapters/ltt.py','universal_supplier/adapters/ltt_yug.py',
        'universal_supplier/adapters/ltt_yug_role.py','universal_supplier/adapters/uss_stanko.py',
        'universal_supplier/adapters/ltt_uss_html.py')


def now(): return datetime.now(timezone.utc).isoformat()
def digest(body): return hashlib.sha256(body).hexdigest()
def encoded(value): return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2,default=str)+'\n').encode('utf8')


def atomic_bytes(path, body):
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.partial')
    with temporary.open('wb') as stream:
        stream.write(body);stream.flush();os.fsync(stream.fileno())
    os.replace(temporary,path)


def save(path,value): atomic_bytes(path,encoded(value))
def load(path): return json.loads(path.read_text('utf8'))
def code_hashes(): return {name:digest((BASE.parents[1]/name).read_bytes()) for name in CODE}


class BudgetReached(BaseException):
    """Propagate past network exception handling without inventing a failure."""


def check_http_budget(deadline, *, clock=lambda:datetime.now(timezone.utc), reserve_seconds=0):
    if deadline and (deadline-clock()).total_seconds()<=reserve_seconds:
        raise BudgetReached('OPERATOR_TIME_BOUND_30_MIN')


def frozen_inputs():
    hashes=load(BASE/'MANIFESTS/SHA256.json');sealed={};scopes={}
    for supplier in SOURCES:
        path=BASE/'MANIFESTS'/(supplier+'.json');body=path.read_bytes();manifest=json.loads(body)
        assert digest(body)==hashes[supplier]['sha256']
        candidates=manifest['candidates'];excluded=manifest.get('non_product_discovery',[])
        assert len(candidates)==hashes[supplier]['manifest_total']
        urls=[r['canonical_url'] for r in candidates+excluded]
        assert len(urls)==len(set(urls))
        for url in urls:safe_source_url(supplier,url)
        sealed[supplier]=dict(supplier=supplier,sha256=digest(body),manifest_total=len(candidates),
            discovery_scope_total=len(urls),version=manifest['format'],created_at=manifest.get('created_at'),
            created_at_basis='Not recorded in original manifest; filesystem mtime is not creation provenance',
            filesystem_modified_at=datetime.fromtimestamp(path.stat().st_mtime,timezone.utc).isoformat())
        scopes[supplier]=[dict(row,scope_kind='PRODUCT_CANDIDATE') for row in candidates]+[
            dict(row,scope_kind='EXCLUDED_DISCOVERY') for row in excluded]
    return sealed,scopes


def foreign_digest(connection,target_id):
    result={}
    for name in sorted(ALLOWED):
        column='id' if name=='suppliers' else 'supplier_id'
        rows=connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t WHERE {}<>%s').format(
            sql.Identifier(name),sql.Identifier(column)),(target_id,)).fetchall()
        values=sorted(r[0] for r in rows)
        result[name]=dict(count=len(values),sha256=digest('\n'.join(values).encode('utf8')))
    return result


def namespace_digest(connection,target_id):
    """Seal a closed source while later authorized sources change independently."""
    result={}
    for name in sorted(ALLOWED):
        column='id' if name=='suppliers' else 'supplier_id'
        rows=connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} t WHERE {}=%s').format(
            sql.Identifier(name),sql.Identifier(column)),(target_id,)).fetchall()
        values=sorted(r[0] for r in rows)
        result[name]=dict(count=len(values),sha256=digest('\n'.join(values).encode('utf8')))
    return result


def counts(connection,supplier):
    r=connection.execute('''SELECT count(DISTINCT p.id),count(DISTINCT p.external_id),count(DISTINCT o.id),
        count(DISTINCT o.source_product_id),count(DISTINCT c.id),count(DISTINCT b.id)
        FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id
        LEFT JOIN offers o ON o.source_product_id=p.id LEFT JOIN supplier_http_captures c ON c.source_product_id=p.id
        LEFT JOIN offer_commercial_observations b ON b.capture_id=c.id WHERE s.code=%s''',(supplier,)).fetchone()
    return dict(zip(('source_products','distinct_external_id','offers','distinct_offer_links','captures','observations'),r))


def identities(connection,supplier):
    return {r[0]:dict(source_product_id=r[1],offer_id=r[2]) for r in connection.execute('''SELECT p.external_id,p.id,o.id
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
        WHERE s.code=%s''',(supplier,)).fetchall()}


def restore_capture(value):
    value=dict(value)
    if not isinstance(value['observed_at'],datetime):
        value['observed_at']=datetime.fromisoformat(value['observed_at'])
    value['redirects']=tuple(value['redirects']);value['diagnostics']=tuple(value['diagnostics'])
    return HttpCapture(**value)


def check_observation(repo,product,capture):
    observation=observation_from_snapshot(product,capture)
    rows=repo.connection.execute('''SELECT p.id,o.id,c.id,b.id,b.extraction_fingerprint,b.price_state,b.price,b.old_price,
        b.currency,b.availability_normalized,c.response_sha256,c.evidence_sha256,s.enabled,o.active,p.catalog_product_id
        FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
        JOIN supplier_http_captures c ON c.source_product_id=p.id AND c.supplier_id=s.id AND c.capture_fingerprint=%s
        JOIN offer_commercial_observations b ON b.capture_id=c.id AND b.offer_id=o.id AND b.supplier_id=s.id
        WHERE s.code=%s AND p.external_id=%s''',(capture.fingerprint,product.supplier,product.site_internal_id)).fetchall()
    assert len(rows)==1
    r=rows[0]
    assert (r[4],r[5],r[6],r[7],r[8] or '',r[9])==(observation.extraction_fingerprint,
        observation.price_state.value,observation.price,observation.old_price,observation.currency or '',observation.availability_normalized)
    assert r[10].strip()==capture.response_sha256 and r[11].strip()==capture.evidence_sha256
    assert r[12] is False and r[13] is False and r[14] is None
    return dict(source_product_id=r[0],offer_id=r[1],capture_id=r[2],observation_id=r[3])


def guarded_commit(repo,product,capture):
    # Inspect the complete exact event before calling the writer on recovery.
    existing=repo.connection.execute('''SELECT c.id FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
        WHERE s.code=%s AND c.capture_fingerprint=%s''',(product.supplier,capture.fingerprint)).fetchall()
    if existing:
        assert len(existing)==1
        return check_observation(repo,product,capture),True
    repo.persist_commercial_observation(product,capture)
    return check_observation(repo,product,capture),False


class RandomPacer:
    def __init__(self,had_previous=False,uniform=random.uniform,sleep=time.sleep):
        self.had_previous=had_previous;self.uniform=uniform;self.sleep=sleep
    def before(self):
        delay=self.uniform(2.0,7.0) if self.had_previous else None
        if delay is not None:self.sleep(delay)
        self.had_previous=True
        return delay


class Run:
    def __init__(self,resume=False):
        self.manifests,self.scopes=frozen_inputs();self.codes=code_hashes()
        self.path=OUT/'CHECKPOINT.json';self.resume=resume
        if resume:
            self.state=load(self.path)
            assert self.state['manifests']==self.manifests and self.state['code_hashes']==self.codes
            assert self.state['status'] in {'RUN1_RUNNING','RUN1_RESUMABLE_AFTER_PRICE_UNIT_FIX','RUN1_RESUMABLE_AFTER_CURRENCY_FIX','RUN1_PARTIAL_SAFE_CHECKPOINT'}, 'Blocked runs require operator review; never automatic replay'
            if self.state['status']=='RUN1_RESUMABLE_AFTER_PRICE_UNIT_FIX':
                transition=load(OUT/self.state['reviewed_transition'])
                assert transition['status']=='RUN1_RESUMABLE_AFTER_PRICE_UNIT_FIX'
                assert transition['after_code_hashes']==self.codes
                assert transition['saved_rows_repaired']==8 and transition['repair_http_gets']==0
            if self.state['status']=='RUN1_RESUMABLE_AFTER_CURRENCY_FIX':
                transition=load(OUT/self.state['reviewed_transition'])
                assert transition['status']=='RUN1_RESUMABLE_AFTER_CURRENCY_FIX' and transition['after_code_hashes']==self.codes
                assert transition['uf100_repaired'] and transition['repair_http_gets']==0
        else:
            assert not self.path.exists(),'Existing RUN1 checkpoint: use reviewed resume, never overwrite'
            self.state=dict(status='RUN1_PREFLIGHT',started_at=now(),updated_at=now(),supplier_order=list(SOURCES),
                manifests=self.manifests,code_hashes=self.codes,http_gets=0,pacing_policy='RANDOM_UNIFORM_2_7',
                system_identifier='7696153366151515704',sources={s:dict(status='NOT_STARTED',cursor=0,
                    manifest_sha256=self.manifests[s]['sha256'],intended_total=len(self.scopes[s]),terminal_counts={},
                    http_gets=0,persisted=0,last_completed_row=None) for s in SOURCES},
                run1_started=False,run2_started=False,full_qa_started=False,finalizer_started=False,matching_run=False)
        self.pacer=RandomPacer(self.state['http_gets']>0)
        self.deadline=None
        private,runtime=config();self.repo=PostgresRepository(PostgresConfig(**runtime));self.c=self.repo.connection
        self.c.execute("SET application_name='ltt_uss_full_run1'")
        self.c.execute("SET statement_timeout='30s'");self.c.execute("SET lock_timeout='5s'")

    def checkpoint(self):
        self.state['updated_at']=now();save(self.path,self.state)
        # Keep the main recovery pointer current without changing manifests or prior evidence.
        root=load(BASE/'CHECKPOINT.json');root.update(status=self.state['status'],stage='RUN1',
            current_run1_receipt='FULL_RUN1_2026-10-14/CHECKPOINT.json',run1='STARTED',run1_started=self.state['run1_started'],
            run2_started=False,full_qa_started=False,finalizer_started=False,isolated_db_runtime='RUN1_ACTIVE',safe_to_power_off=False)
        save(BASE/'CHECKPOINT.json',root)

    def journal_path(self,supplier,index):
        url=self.scopes[supplier][index]['canonical_url']
        original=OUT/'ROWS'/supplier/(f'{index:06d}_'+digest(url.encode('utf8'))[:16]+'.json')
        revision=getattr(self,'state',{}).get('reviewed_row_revisions',{}).get(supplier,{}).get(str(index))
        if revision:
            assert digest(original.read_bytes())==revision['original_journal_sha256']
            revised=OUT/revision['path']
            assert revised.parent==OUT/'REVISED_ROWS'/supplier and revised.name==original.name
            return revised
        return original

    def before_request(self,supplier,url):
        check_http_budget(getattr(self,'deadline',None),reserve_seconds=35)
        delay=self.pacer.before()
        check_http_budget(getattr(self,'deadline',None),reserve_seconds=25)
        assert code_hashes()==self.codes,'Runner/parser code changed during RUN1; stop before HTTP'
        event=dict(supplier=supplier,url=url,request_started_at=now(),delay_seconds=delay,policy='RANDOM_UNIFORM_2_7')
        self.state['http_gets']+=1;self.state['sources'][supplier]['http_gets']+=1
        save(OUT/'HTTP'/f"{self.state['http_gets']:06d}.json",event);self.checkpoint()

    def fetch(self,journal,path):
        attempt=len(journal.get('attempts',[]))+1
        response_path=path.with_name(path.stem+f'.response{attempt}.json')
        intent=journal.get('intent',{})
        if journal['phase']=='REQUESTING' and not response_path.exists():
            raise RuntimeError('INFLIGHT_REQUEST_UNRESOLVED: no durable response; no automatic GET replay')
        if journal['phase']!='REQUESTING':
            journal.update(phase='REQUESTING',intent=dict(attempt=attempt,observed_at=now()))
            save(path,journal)
        owner=self
        class Client:
            def get_validated(self,url,*,timeout_seconds,validator):
                if response_path.exists():
                    record=load(response_path);raw=(OUT/record['raw_path']).read_bytes()
                    assert digest(raw)==record['response_sha256']
                    value=dict(record['response']);value['redirects']=tuple(value['redirects'])
                    validator(value['final_url'])
                    for redirect in value['redirects']:validator(redirect)
                    return HttpResponse(**value,body=raw)
                inner=UrllibPublicHttpClient(lambda url:owner.before_request(journal['supplier'],url))
                response=inner.get_validated(url,timeout_seconds=timeout_seconds,validator=validator)
                body_hash=digest(response.body);raw_path=Path('EVIDENCE/PRIVATE_RAW')/journal['supplier']/(body_hash+'.bin')
                atomic_bytes(OUT/raw_path,response.body)
                value=asdict(response);value.pop('body')
                save(response_path,dict(response=value,response_sha256=body_hash,raw_path=str(raw_path)))
                return response
        result=capture_source_html(journal['url'],supplier=journal['supplier'],evidence_dir=OUT/'EVIDENCE',
            client=Client(),timeout_seconds=25)
        observed=datetime.fromisoformat(journal['intent']['observed_at'])
        result=replace(result,observed_at=observed,capture=replace(result.capture,observed_at=observed) if result.capture else None)
        metadata=asdict(result);metadata.pop('evidence_body')
        # Preserve status-response evidence too; private raw bytes never enter SQL or logs.
        if response_path.exists():
            response=load(response_path)
            metadata['response_sha256']=response['response_sha256'];metadata['response_record']=str(response_path.relative_to(OUT))
        journal.setdefault('attempts',[]).append(metadata);journal.update(phase='CAPTURED',result=metadata)
        save(path,journal)
        return result.status

    def prepare(self,journal,path):
        result=journal['result'];status=result['status']
        outcome={'BLOCKED':'SOURCE_BLOCKED','HTTP_STATUS':'HTTP_STATUS','NETWORK_ERROR':'NETWORK_ERROR',
            'NON_PRODUCT_CATEGORY_PAGE':'NON_PRODUCT_CATEGORY_PAGE','AMBIGUOUS':'AMBIGUOUS',
            'UNSAFE_URL':'PARSE_ERROR','NON_HTML':'HTTP_STATUS'}.get(status,'PARSE_ERROR')
        if result.get('http_status') in (404,410):outcome='STALE_MISSING'
        if status=='AMBIGUOUS' and 'MULTI_VARIANT' in result['diagnostics']:outcome='MULTI_VARIANT'
        if status=='PARSE_ERROR':journal['error']=';'.join(result['diagnostics'])
        if status=='SUCCESS':
            body=(OUT/'EVIDENCE'/journal['supplier']/(result['evidence_sha256']+'.html')).read_bytes()
            assert digest(body)==result['evidence_sha256']
            try:
                product=parse(journal['supplier'],body,result['final_url'])
                assert product.name and product.site_internal_id,'required stable identity/title missing'
                assert product.source_url and product.source_category,'required source category missing'
                assert product.enrichment_evidence is not None,'required source evidence missing'
                if product.supplier=='ltt_yug':assert product.site_internal_id!='1','confirmed non-product ID1'
                capture=restore_capture(result['capture'])
                product_card_from_snapshot(product,capture);observation_from_snapshot(product,capture)
                known=self.c.execute('''SELECT DISTINCT p.external_id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                    LEFT JOIN supplier_http_captures c ON c.source_product_id=p.id
                    WHERE s.code=%s AND (p.canonical_url=%s OR c.requested_url=%s)''',
                    (product.supplier,product.source_url,journal['url'])).fetchall()
                if any(r[0]!=product.site_internal_id for r in known):
                    outcome='IDENTITY_DRIFT';journal['error']='stable source ID differs from historical exact URL evidence'
                else:
                    outcome='PERSISTED'
                    journal.update(external_id=product.site_internal_id,product_payload_sha256=digest(encoded(asdict(product))),
                        capture_fingerprint=capture.fingerprint,title=product.name,model=product.supplier_model,
                        price_state=product.price.state.value,price=str(product.price.current_price) if product.price.current_price is not None else None,
                        availability=product.availability,category=product.source_category,parser_diagnostics=list(product.diagnostics))
                    previous=self.c.execute('''SELECT p.canonical_url FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                        WHERE s.code=%s AND p.external_id=%s''',(product.supplier,product.site_internal_id)).fetchone()
                    if previous and previous[0]!=product.source_url:
                        journal['alias_evidence']=dict(basis='SAME_STABLE_SOURCE_ID',stable_id=product.site_internal_id,previous_url=previous[0],current_url=product.source_url)
            except Exception as error:
                outcome='PARSE_ERROR';journal['error']=type(error).__name__+':'+str(error)
        journal.update(outcome=outcome,phase='PREPARED',prepared_at=now());save(path,journal)

    def product(self,journal):
        result=journal['result'];body=(OUT/'EVIDENCE'/journal['supplier']/(result['evidence_sha256']+'.html')).read_bytes()
        assert digest(body)==result['evidence_sha256']
        product=parse(journal['supplier'],body,result['final_url'])
        assert digest(encoded(asdict(product)))==journal['product_payload_sha256']
        return product,restore_capture(result['capture'])

    def process(self,supplier,index):
        row=self.scopes[supplier][index];path=self.journal_path(supplier,index)
        journal=load(path) if path.exists() else dict(supplier=supplier,index=index,url=row['canonical_url'],phase='NEW')
        if journal['phase']=='TERMINAL':return journal
        if row['scope_kind']=='EXCLUDED_DISCOVERY':
            assert supplier=='ltt_yug' and row['status']=='NON_PRODUCT_CATEGORY_PAGE'
            journal.update(phase='TERMINAL',outcome='NON_PRODUCT_CATEGORY_PAGE',provenance='OPERATOR-CONFIRMED',
                discovery_record=row,evidence_trace='YUG_ROLE_REVIEW_2026-10-14/PAGE_ROLE_EVIDENCE.json',completed_at=now())
            save(path,journal);return journal
        while journal['phase'] in ('NEW','REQUESTING','RETRY'):
            previous_gets=getattr(self,'state',{}).get('http_gets',0)
            try:
                status=self.fetch(journal,path)
            except BudgetReached:
                if self.state['http_gets']==previous_gets:
                    journal.update(phase='NEW',unstarted_intent_due_to_time_bound=journal.pop('intent',None))
                    save(path,journal)
                raise
            if status==CaptureStatus.NETWORK_ERROR and len(journal['attempts'])<2:
                journal['phase']='RETRY';save(path,journal);continue
            break
        if journal['phase']=='CAPTURED':self.prepare(journal,path)
        assert journal['phase']=='PREPARED'
        if journal['outcome']=='PERSISTED':
            product,capture=self.product(journal)
            journal['db_verification'],journal['recovered_committed_event']=guarded_commit(self.repo,product,capture)
        journal.update(phase='TERMINAL',completed_at=now());save(path,journal)
        return journal

    def account(self,supplier):
        own=self.state['sources'][supplier];rows=[]
        for i in range(len(self.scopes[supplier])):
            path=self.journal_path(supplier,i)
            if not path.exists():break
            row=load(path)
            if row['phase']!='TERMINAL':break
            rows.append(row)
        own.update(cursor=len(rows),terminal_counts=dict(Counter(r['outcome'] for r in rows)),
            persisted=sum(r['outcome']=='PERSISTED' for r in rows),
            last_completed_row=dict(index=rows[-1]['index'],url=rows[-1]['url'],outcome=rows[-1]['outcome']) if rows else None,
            updated_at=now())
        return rows

    def reconcile(self,supplier,complete=False,closed=False):
        own=self.state['sources'][supplier];rows=self.account(supplier);before=load(OUT/'SUPPLIERS'/supplier/'BEFORE.json')
        after=fingerprints(self.c)
        assert all(after[n]==before['fingerprints'][n] for n in after if n not in ALLOWED)
        if closed:
            assert namespace_digest(self.c,before['supplier_id'])==own['closed_namespace_fingerprints']
        else:
            assert foreign_digest(self.c,before['supplier_id'])==before['foreign_fingerprints']
        previous=before['identities'];current=identities(self.c,supplier)
        assert all(current.get(key)==value for key,value in previous.items())
        latest={};persisted=[r for r in rows if r['outcome']=='PERSISTED']
        for row in persisted:
            product,capture=self.product(row);assert check_observation(self.repo,product,capture)==row['db_verification']
            latest[product.site_internal_id]=(product,capture)
        for product,capture in latest.values():verify_persisted(self.repo,product,capture)
        expected_ids=set(previous)|set(latest);assert set(current)==expected_ids
        actual=counts(self.c,supplier)
        assert actual['source_products']==actual['distinct_external_id']==len(expected_ids)
        assert actual['offers']==actual['distinct_offer_links']==len(expected_ids)
        assert actual['captures']==before['counts']['captures']+len(persisted)
        assert actual['observations']==before['counts']['observations']+len(persisted)
        assert self.c.execute('SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL').fetchone()[0]==0
        yug=self.c.execute('''SELECT p.external_id,p.id,o.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
            JOIN offers o ON o.source_product_id=p.id WHERE s.code='ltt_yug' AND p.external_id IN ('1','312','297') ORDER BY p.external_id''').fetchall()
        assert len(yug)==2 and {r[0] for r in yug}=={'312','297'} and yug[0][1]!=yug[1][1] and yug[0][2]!=yug[1][2]
        if complete:assert len(rows)==own['intended_total'] and sum(own['terminal_counts'].values())==own['intended_total']
        receipt=dict(status='RECONCILED_COMPLETE' if complete else 'RECONCILED_PARTIAL',supplier=supplier,
            counts=actual,processed=len(rows),total=own['intended_total'],terminal_counts=own['terminal_counts'],
            duplicate_external_ids=0,duplicate_offer_links=0,unexplained_missing=0,foreign_mutations=0,identity_overwrite=0,
            yug_312_present=True,yug_297_present=True,yug_id1_present=False,yug_collapsed=False,fingerprints=after,checked_at=now())
        if complete and not closed:
            own['closed_namespace_fingerprints']=namespace_digest(self.c,before['supplier_id'])
        filename='CLOSED_SOURCE_VERIFICATION.json' if closed else 'RECONCILIATION.json'
        save(OUT/'SUPPLIERS'/supplier/filename,receipt);own['reconciliation']=receipt['status']
        return receipt

    def preflight(self):
        system=verify_system();assert system['system_identifier']==self.state['system_identifier']
        if not self.resume:
            baseline=fingerprints(self.c);prior=load(BASE/'CATEGORY_RESUME_2026-10-14/FINAL_DB_VERIFICATION.json')['fingerprints']
            assert baseline==prior
            assert tuple(baseline[n]['count'] for n in ('source_products','offers','offer_commercial_observations'))==(73,73,80)
            yug=self.c.execute('''SELECT p.external_id,p.id,o.id FROM source_products p JOIN suppliers s ON s.id=p.supplier_id
                JOIN offers o ON o.source_product_id=p.id WHERE s.code='ltt_yug' AND p.external_id IN ('1','312','297') ORDER BY p.external_id''').fetchall()
            assert len(yug)==2 and {r[0] for r in yug}=={'312','297'} and yug[0][1]!=yug[1][1] and yug[0][2]!=yug[1][2]
            assert self.c.execute('SELECT count(*) FROM source_products WHERE catalog_product_id IS NOT NULL').fetchone()[0]==0
            assert self.c.execute('SELECT count(*) FROM suppliers WHERE enabled').fetchone()[0]==0
            assert self.c.execute('SELECT count(*) FROM offers WHERE active').fetchone()[0]==0
            save(OUT/'BASELINE.json',dict(**system,fingerprints=baseline,yug_rows=yug,yug_id1_present=False,
                source_products=73,offers=73,observations=80,all_prior_table_fingerprints_equal=True,checked_at=now()))
        else:
            baseline=load(OUT/'BASELINE.json')['fingerprints'];current=fingerprints(self.c)
            assert all(current[n]==baseline[n] for n in baseline if n not in ALLOWED)
            for supplier in SOURCES:
                own=self.state['sources'][supplier]
                if own['status']=='RUN1_COMPLETED':self.reconcile(supplier,True,closed=True)
                elif own['status']=='RUN1_RUNNING':
                    rows=self.account(supplier)
                    if len(rows)<own['intended_total']:
                        path=self.journal_path(supplier,len(rows))
                        if path.exists() and load(path)['phase']=='PREPARED':
                            # A transaction may have committed before terminal JSON.
                            # Roll forward its exact saved event before count checks.
                            self.process(supplier,len(rows));self.account(supplier)
                    self.reconcile(supplier)
        self.state.update(status='RUN1_RUNNING',run1_started=True,worker_pid=os.getpid());self.checkpoint()

    def main(self):
        try:
            self.preflight()
            for supplier in self.state['supplier_order']:
                own=self.state['sources'][supplier]
                if own['status']=='RUN1_COMPLETED':continue
                directory=OUT/'SUPPLIERS'/supplier;directory.mkdir(parents=True,exist_ok=True)
                if not (directory/'BEFORE.json').exists():
                    sid=self.c.execute('SELECT id FROM suppliers WHERE code=%s',(supplier,)).fetchone()[0]
                    save(directory/'BEFORE.json',dict(supplier_id=sid,fingerprints=fingerprints(self.c),
                        foreign_fingerprints=foreign_digest(self.c,sid),identities=identities(self.c,supplier),counts=counts(self.c,supplier)))
                own.update(status='RUN1_RUNNING',started_at=own.get('started_at',now()));self.account(supplier);self.checkpoint()
                rows=self.account(supplier)
                if rows and rows[-1]['outcome'] in STOP:
                    own.update(status='RUN1_BLOCKED',blocker=rows[-1].get('error',rows[-1]['outcome']))
                    self.state.update(status='RUN1_BLOCKED',source=supplier,blocker=own['blocker'])
                    self.checkpoint();self.reconcile(supplier);return 1
                for index in range(own['cursor'],own['intended_total']):
                    try:
                        check_http_budget(self.deadline,reserve_seconds=90)
                        row=self.process(supplier,index)
                    except BudgetReached:
                        own.update(status='RUN1_PAUSED_TIME_BOUND',pause_reason='OPERATOR_TIME_BOUND_30_MIN')
                        self.state.update(status='RUN1_PARTIAL_SAFE_CHECKPOINT',source=supplier,pause_reason='OPERATOR_TIME_BOUND_30_MIN')
                        self.account(supplier);self.checkpoint();self.reconcile(supplier);self.checkpoint();return 0
                    except Exception as error:
                        own.update(status='RUN1_BLOCKED',blocker=type(error).__name__+':'+str(error),blocked_index=index,
                            blocked_url=self.scopes[supplier][index]['canonical_url'])
                        self.state.update(status='RUN1_BLOCKED',source=supplier,blocker=own['blocker'])
                        self.account(supplier);self.checkpoint();self.reconcile(supplier);return 1
                    self.account(supplier);self.checkpoint()
                    print(json.dumps(dict(supplier=supplier,processed=own['cursor'],total=own['intended_total'],
                        terminal_counts=own['terminal_counts'],http_gets=own['http_gets'],last_outcome=row['outcome']),ensure_ascii=True),flush=True)
                    if row['outcome'] in STOP:
                        own.update(status='RUN1_BLOCKED',blocker=row.get('error',row['outcome']),blocked_url=row['url'])
                        self.state.update(status='RUN1_BLOCKED',source=supplier,blocker=own['blocker'])
                        self.checkpoint();self.reconcile(supplier);return 1
                self.reconcile(supplier,True);own['status']='RUN1_COMPLETED';own['finished_at']=now();self.checkpoint()
            final={supplier:self.reconcile(supplier,True,closed=True) for supplier in SOURCES}
            save(OUT/'ALL_SOURCE_RECONCILIATION.json',dict(status='RUN1_APPLIED_AND_VERIFIED',sources=final,checked_at=now()))
            self.state.update(status='RUN1_COMPLETED',finished_at=now());self.checkpoint();return 0
        finally:
            save(OUT/'DB_AFTER.json',dict(fingerprints=fingerprints(self.c),counts={s:counts(self.c,s) for s in SOURCES},checked_at=now()))
            self.repo.close()


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--resume',action='store_true');parser.add_argument('--http-deadline-utc',required=True);args=parser.parse_args()
    deadline=datetime.fromisoformat(args.http_deadline_utc);assert deadline.tzinfo is not None
    check_http_budget(deadline,reserve_seconds=90)
    OUT.mkdir(parents=True,exist_ok=True)
    with (OUT/'WORKER.lock').open('a+b') as lock:
        if lock.seek(0,2)==0:lock.write(b'0');lock.flush()
        lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        try:
            run=Run(resume=args.resume);run.deadline=deadline;run.state['session_http_deadline_utc']=deadline.isoformat()
            return run.main()
        finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)


if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf8');raise SystemExit(main())
