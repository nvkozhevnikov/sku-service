"""Existing narrow PostgreSQL writers plus exact capture/observation readback.

No matching algorithm, ID recipe, reconciliation or schema changes here.
Construction is deferred until explicit runtime SQL permission passes.
"""
from datetime import datetime,timezone
import time

class SourceBackend:
    def __init__(self,config,code):
        from universal_supplier.postgres import PostgresRepository
        self.repo=PostgresRepository(config);self.config=config;self.code=code;self.run_id=None;self.summary={};self.started=time.monotonic()
    def start(self,job,mode):
        self.is_batch = bool(job.settings_snapshot.get('source_batch'))
        value=job.settings_snapshot.get('source_input')
        if mode=='RESUME' and (job.settings_snapshot.get('crawl_settings') or {}).get('source_canary'):
            from .source_runtime import read_input
            settings=job.settings_snapshot['crawl_settings']
            value=read_input(self.code,settings['resume_owner_job_id'],settings['resume_input_sha256'])
        if value and value.get('canary'):
            from .source_canary import validate_config,validate_live_selection
            validate_config(self.config);validate_live_selection(self.repo.connection,value,allow_inactive=mode=='RESUME')
        self.run_id=self.repo.start_crawl_run(started_at=datetime.now(timezone.utc).isoformat(),baseline_discovered_count=None,
            supplier_code=self.code,metadata={'job_id':job.id,'source_mode':mode})
        with self.repo.connection.transaction(),self.repo.connection.cursor() as q:
            q.execute("UPDATE crawl_jobs SET crawl_run_id=%s WHERE id=%s AND supplier_id=%s AND status='running'",(self.run_id,job.id,job.supplier_id))
            if q.rowcount!=1:raise RuntimeError('SOURCE_QUEUE_OWNER_LOST')
        return self.run_id
    def persist(self,product,capture):
        if self.code in ('partner_st','optimum'):return self.persist_native(product,capture)
        if self.run_id is None:raise RuntimeError('SOURCE_RUN_NOT_STARTED')
        if self.code=='kami':return self.repo.persist_kami_observation(product,capture,source_run_id=self.run_id)
        return self.repo.persist_commercial_observation(product,capture,source_run_id=self.run_id)
    def persist_native(self,card,capture):
        """Native bounded ingest and receipt ledger commit in ONE transaction.

        Per-card batches deliberately omit full-catalog quality/missing logic.
        Existing native full/pilot entry points do not call this method.
        """
        from psycopg.types.json import Jsonb
        import hashlib,json
        if card.supplier_code!=self.code or card.requested_url!=capture.requested_url:raise ValueError('NATIVE_CAPTURE_IDENTITY')
        with self.repo.connection.transaction(),self.repo.connection.cursor() as q:
            q.execute('''SELECT c.id FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
                         WHERE s.code=%s AND c.capture_fingerprint=%s''',(self.code,capture.fingerprint))
            if q.fetchone():raise RuntimeError('NATIVE_CAPTURE_ALREADY_EXISTS_RECONCILE')
            counts=self.repo.ingest([card],capture.observed_at.isoformat(),supplier_code=self.code,bounded=True)
            q.execute('''SELECT sp.id,sp.supplier_id,o.id FROM source_products sp
                         JOIN suppliers s ON s.id=sp.supplier_id JOIN offers o ON o.source_product_id=sp.id AND o.offer_kind='default'
                         WHERE s.code=%s AND sp.external_id=%s AND sp.external_id_is_stable''',(self.code,card.external_id))
            row=q.fetchone()
            if not row:raise RuntimeError('NATIVE_INGEST_IDENTITY_NOT_FOUND')
            product_id,supplier_id,offer_id=row
            q.execute('''INSERT INTO supplier_http_captures(supplier_id,source_product_id,requested_url,final_url,http_status,
                         content_type,observed_at,capture_fingerprint,response_sha256,evidence_sha256,evidence_ref,redirect_urls,diagnostics)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
                      (supplier_id,product_id,capture.requested_url,capture.final_url,capture.http_status,capture.content_type,
                       capture.observed_at,capture.fingerprint,capture.response_sha256,capture.evidence_sha256,capture.evidence_ref,
                       Jsonb(list(capture.redirects)),Jsonb({'runtime':'native-checkpoint/v1','codes':capture.diagnostics})))
            capture_id=q.fetchone()[0]
            payload=card.as_jsonable();extraction=hashlib.sha256(json.dumps(payload,sort_keys=True,default=str).encode()).hexdigest()
            currency=card.currency if card.currency and len(card.currency)==3 and card.currency.isascii() and card.currency.isalpha() and card.currency.isupper() else None
            numeric=card.price is not None and card.price>0 and currency is not None
            price_state='numeric_public' if numeric else 'zero_invalid' if card.price==0 else 'parse_error' if card.price is not None else 'missing'
            q.execute('''INSERT INTO offer_commercial_observations(supplier_id,source_product_id,offer_id,capture_id,observed_at,
                         extraction_fingerprint,price_state,price,old_price,currency,availability_raw,availability_normalized,extraction_evidence)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                      (supplier_id,product_id,offer_id,capture_id,capture.observed_at,extraction,price_state,card.price if numeric else None,
                       card.old_price if numeric and card.old_price is not None and card.old_price>0 else None,
                       currency,card.availability_raw,card.availability_normalized,Jsonb({'parser':self.code,'evidence_sha256':capture.evidence_sha256})))
        return {'ingest_counts':counts}
    def probe(self,entry,receipt):
        if not receipt or not receipt.get('original_capture_metadata'):return 'UNKNOWN'
        from universal_supplier.commercial_persistence import HttpCapture
        fields=dict(receipt['original_capture_metadata']);fields['observed_at']=datetime.fromisoformat(fields['observed_at'])
        capture=HttpCapture(**fields)
        if capture.fingerprint!=receipt.get('capture_fingerprint') or capture.requested_url!=entry['url']:return 'UNKNOWN'
        with self.repo.connection.cursor() as q:
            q.execute('''SELECT c.evidence_sha256,c.observed_at,c.final_url,
                       EXISTS(SELECT 1 FROM offer_commercial_observations o WHERE o.capture_id=c.id
                           AND o.source_product_id=c.source_product_id AND o.observed_at=c.observed_at)
                       FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
                       JOIN source_products sp ON sp.id=c.source_product_id
                       WHERE s.code=%s AND c.capture_fingerprint=%s AND c.requested_url=%s
                           AND sp.external_id=%s''',
                      (self.code,capture.fingerprint,entry['url'],receipt.get('external_id')))
            rows=q.fetchall()
        if not rows:return 'NOT_FOUND'
        if len(rows)!=1:return 'UNKNOWN'
        sha,at,final,observation=rows[0]
        return 'COMMITTED' if (sha,at,final,observation)==(capture.evidence_sha256,capture.observed_at,capture.final_url,True) else 'UNKNOWN'
    def matching(self,external_ids):
        from universal_supplier.automatic_matching import run_post_ingest
        return run_post_ingest(self.config,self.code,external_ids)
    def progress(self,summary):
        from .adapter_registry import _finish_metadata
        self.summary.update(summary)
        total=self.summary.get('found');processed=self.summary.get('processed',0)
        phase={'PRODUCTS':'FETCHING_PRODUCTS'}.get(self.summary.get('phase'),self.summary.get('phase','QUEUED'))
        from .progress import PHASE_LABELS_RU
        speed=self.summary.get('http_cards_per_minute')
        progress={'phase':phase,'phase_label_ru':PHASE_LABELS_RU.get(phase,phase),'status':self.summary.get('status','running'),
            'total':total,'processed':processed,'succeeded':self.summary.get('sql_committed',0),'errors':self.summary.get('errors',0),
            'percent':None if total is None else 100 if total==0 else min(100,100*processed/total),
            'rate_per_second':None if speed is None else speed/60,'eta_seconds':None,'eta_human_ru':'Время окончания пока неизвестно',
            'elapsed_seconds':time.monotonic()-self.started,'updated_at':datetime.now(timezone.utc).isoformat()}
        _finish_metadata(self.repo,self.run_id,{'source_runtime':self.summary,'progress':progress})
    def finish(self,result):
        from .adapter_registry import _finish_metadata
        # Actual material history of this run; read only. Never infer separate
        # price/stock changes from changed_offers (which also includes quantity).
        if getattr(self, 'is_batch', False):
            with self.repo.connection.cursor() as q:
                q.execute('''SELECT old_value,new_value FROM entity_change_history
                    WHERE crawl_run_id=%s AND field_group='commercial' ORDER BY id''', (self.run_id,))
                history = q.fetchall()
            from .source_batch import commercial_change_counts
            result.update(commercial_change_counts(history))
        self.progress({**result,'phase':'FINISHED'})
        _finish_metadata(self.repo,self.run_id,{'source_runtime':result,'source_mode':result['collection_mode'],
            'identity_post_processing':result['identity_post_processing'],'termination_reason':result['termination_reason']})
        with self.repo.connection.transaction(),self.repo.connection.cursor() as q:
            counts=result['sql_counts']
            q.execute('''UPDATE crawl_runs SET status=%s,finished_at=now(),discovered_count=%s,
                      fetched_count=%s,error_count=%s,new_count=coalesce(%s,new_count),changed_count=coalesce(%s,changed_count) WHERE id=%s''',
                      (result['status'],result['found'],result['sql_committed_this_job'],result['errors'],
                       None if counts is None else counts['new_products'],None if counts is None else counts['changed_products'],self.run_id))
    def close(self):self.repo.close()
