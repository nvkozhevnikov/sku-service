"""Existing narrow PostgreSQL writers plus exact capture/observation readback.

No matching algorithm, ID recipe, reconciliation or schema changes here.
Construction is deferred until explicit runtime SQL permission passes.
"""
from datetime import datetime,timezone
import time


class SourceUpdateRejected(RuntimeError):
    """Known semantic rejection, AFTER proven rollback/no capture SQL write."""
    def __init__(self,reason,proof):
        super().__init__(reason)
        self.proof=proof

class SourceBackend:
    def __init__(self,config,code):
        from universal_supplier.postgres import PostgresRepository
        self.repo=PostgresRepository(config);self.config=config;self.code=code;self.run_id=None;self.summary={};self.started=time.monotonic()
    def start(self,job,mode):
        from .execution_canary import is_canary
        self.release_canary=is_canary(job.settings_snapshot.get('automation',{}))
        if self.release_canary:
            from .automation_execution import validate_job
            validate_job(job.settings_snapshot)
        self.is_batch = bool(job.settings_snapshot.get('source_batch'))
        self.commercial_only=bool(job.settings_snapshot.get('automation',{}).get('purpose')=='confirmed-commercial-refresh/v1')
        if self.commercial_only:
            from .automation_execution import validate_job
            validate_job(job.settings_snapshot)
        value=job.settings_snapshot.get('source_input')
        from .manual_collection import running_manually
        if mode=='RESUME' and not running_manually(job) and (job.settings_snapshot.get('crawl_settings') or {}).get('source_canary'):
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
        if getattr(self,'release_canary',False):
            from .execution_canary import authorize_native
            authorize_native(product,capture)
        commercial_only=getattr(self,'commercial_only',False)
        if self.code in ('partner_st','optimum') and not commercial_only:return self.persist_native(product,capture)
        if self.run_id is None:raise RuntimeError('SOURCE_RUN_NOT_STARTED')
        try:
            if commercial_only:
                from universal_supplier.commercial_persistence import native_observation,product_card_from_snapshot,observation_from_snapshot
                if self.code in ('partner_st','optimum'):card,observation=product,native_observation(product,capture)
                elif self.code=='kami':
                    from universal_supplier.kami_persistence import prepare_observation
                    card,observation=prepare_observation(product,capture)
                else:card,observation=product_card_from_snapshot(product,capture),observation_from_snapshot(product,capture)
                return self.repo._persist_passive_observation(card,observation,capture,source_run_id=self.run_id,commercial_only=True)
            if self.code=='kami':return self.repo.persist_kami_observation(product,capture,source_run_id=self.run_id)
            return self.repo.persist_commercial_observation(product,capture,source_run_id=self.run_id)
        except RuntimeError as error:
            # This exact guard is raised before source updates by prepare_update,
            # under the passive writer's own transaction. Do NOT generalize to
            # permission, target/URL/mapping errors, or arbitrary SQL failures.
            if not str(error).startswith('SOURCE_UPDATE_SEMANTIC_CONFLICT:'):raise
            from psycopg.pq import TransactionStatus
            if self.repo.connection.info.transaction_status!=TransactionStatus.IDLE:
                raise RuntimeError('SEMANTIC_REJECTION_ROLLBACK_NOT_PROVEN') from error
            from dataclasses import asdict
            fields=asdict(capture);fields['observed_at']=capture.observed_at.isoformat()
            external=getattr(product,'external_id',None) or getattr(product,'supplier_external_id',None) or getattr(product,'site_internal_id',None)
            receipt={'capture_fingerprint':capture.fingerprint,'original_capture_metadata':fields,'external_id':external}
            if self.probe({'url':capture.requested_url},receipt)!='NOT_FOUND':
                raise RuntimeError('SEMANTIC_REJECTION_SQL_NOT_PROVEN') from error
            with self.repo.connection.cursor() as q:
                q.execute('''SELECT sp.id,sp.catalog_product_id,sb.sterbrust_product_id
                    FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                    JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
                    WHERE s.code=%s AND sp.external_id=%s AND sp.external_id_is_stable''',
                    (self.code,external))
                mappings=q.fetchall()
            if len(mappings)!=1 or not all(x is not None for x in mappings[0]):
                raise RuntimeError('SEMANTIC_REJECTION_MAPPING_NOT_PROVEN') from error
            source_id,catalog_id,target=mappings[0]
            raise SourceUpdateRejected(str(error),{'rollback_verified':True,'sql_absent':True,
                'source_product_id':source_id,'catalog_product_id':catalog_id,'sterbrust_product_id':target}) from error
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
            target=None
            if getattr(self,'release_canary',False):
                from .execution_canary import authorize_native
                target=authorize_native(card,capture)
                q.execute('SELECT id,catalog_product_id FROM source_products WHERE id=%s FOR UPDATE',(target['source_product_id'],))
                if q.fetchone()!=(target['source_product_id'],target['catalog_product_id']):raise RuntimeError('CANARY_LINK_CHANGED_BEFORE_INGEST')
            counts=self.repo.ingest([card],capture.observed_at.isoformat(),supplier_code=self.code,bounded=True)
            if target is not None:
                q.execute('SELECT id,catalog_product_id FROM source_products WHERE id=%s',(target['source_product_id'],))
                if q.fetchone()!=(target['source_product_id'],target['catalog_product_id']):raise RuntimeError('CANARY_LINK_CHANGE_ROLLBACK')
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
            from universal_supplier.commercial_persistence import native_observation
            observation=native_observation(card,capture)
            q.execute('''INSERT INTO offer_commercial_observations(supplier_id,source_product_id,offer_id,capture_id,observed_at,
                         extraction_fingerprint,price_state,price,old_price,currency,availability_raw,availability_normalized,
                         price_type,price_raw,price_source,extraction_evidence)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)''',
                      (supplier_id,product_id,offer_id,capture_id,capture.observed_at,
                       observation.extraction_fingerprint,observation.price_state.value,observation.price,observation.old_price,
                       observation.currency,observation.availability_raw,observation.availability_normalized,
                       observation.price_type,observation.price_raw,observation.price_source,Jsonb(observation.extraction_evidence)))
        return {'ingest_counts':counts}
    def probe(self,entry,receipt):
        from .source_resume import MANUAL_REDIRECT, MANUAL_STOP_INTERRUPTED, MANUAL_TRANSPORT_ERRORS, digest, quarantine_evidence_present
        if receipt and receipt.get('state')=='QUARANTINED' and receipt.get('isolation',{}).get('category') in MANUAL_TRANSPORT_ERRORS:
            # This outcome was archived before any capture could reach ingest.
            return 'NOT_FOUND' if quarantine_evidence_present(receipt,entry) else 'UNKNOWN'
        category = (receipt or {}).get('isolation',{}).get('category')
        if receipt and receipt.get('state')=='QUARANTINED' and category in (MANUAL_REDIRECT, MANUAL_STOP_INTERRUPTED):
            if not quarantine_evidence_present(receipt,entry):return 'UNKNOWN'
            proof = receipt['stop_evidence' if category == MANUAL_STOP_INTERRUPTED else 'redirect_evidence']
            with self.repo.connection.cursor() as q:
                q.execute("""SELECT j.settings_snapshot->'source_input',
                    EXISTS(SELECT 1 FROM supplier_http_captures c
                        WHERE c.supplier_id=s.id AND (c.requested_url=%s OR c.source_product_id=sp.id)
                        AND c.observed_at>=r.started_at AND (r.finished_at IS NULL OR c.observed_at<=r.finished_at)),
                    EXISTS(SELECT 1 FROM offer_commercial_observations o WHERE o.source_product_id=sp.id
                        AND o.observed_at>=r.started_at AND (r.finished_at IS NULL OR o.observed_at<=r.finished_at)),
                    EXISTS(SELECT 1 FROM entity_change_history h WHERE h.crawl_run_id=r.id AND h.source_product_id=sp.id)
                    FROM crawl_jobs j JOIN crawl_runs r ON r.id=j.crawl_run_id
                    JOIN suppliers s ON s.id=j.supplier_id JOIN source_products sp ON sp.supplier_id=s.id
                    WHERE j.id=%s AND s.code=%s AND sp.id=%s AND sp.external_id=%s""",
                    (entry['url'],proof['job_id'],self.code,
                     entry['source_product_id'],entry['source_external_id']))
                row=q.fetchone()
            return 'NOT_FOUND' if row and digest(row[0])==receipt['origin']['manifest_sha256'] and not any(row[1:]) else 'UNKNOWN'
        if receipt and receipt.get('state')=='QUARANTINED' and receipt.get('isolation',{}).get('category')=='operator_scope':
            from .source_recovery import approved_scope_item,scope_sql_absent
            item=approved_scope_item(receipt,entry)
            with self.repo.connection.cursor() as q:scope_sql_absent(q,item)
            return 'NOT_FOUND'
        if receipt and receipt.get('state')=='QUARANTINED' and receipt.get('isolation',{}).get('category')=='http':
            if not quarantine_evidence_present(receipt,entry):return 'UNKNOWN'
            record=receipt['http_evidence']
            with self.repo.connection.cursor() as q:
                q.execute('''SELECT count(*) FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
                    WHERE s.code=%s AND c.requested_url=%s AND c.evidence_sha256=%s AND c.observed_at=%s''',
                    (self.code,entry['url'],record['evidence_sha256'],record['observed_at']))
                return 'NOT_FOUND' if q.fetchone()==(0,) else 'UNKNOWN'
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
                           AND (sp.external_id=%s OR %s::text IS NULL)''',
                      (self.code,capture.fingerprint,entry['url'],receipt.get('external_id'),receipt.get('external_id')))
            rows=q.fetchall()
        if not rows:return 'NOT_FOUND'
        if len(rows)!=1:return 'UNKNOWN'
        sha,at,final,observation=rows[0]
        return 'COMMITTED' if (sha,at,final,observation)==(capture.evidence_sha256,capture.observed_at,capture.final_url,True) else 'UNKNOWN'
    def matching(self,external_ids):
        if getattr(self,'release_canary',False):
            import os
            if os.environ.get('STERBRUST_RC_MATCHING_APPLY_APPROVED')!='NO':raise PermissionError('CANARY_MATCHING_APPLY_FORBIDDEN')
        from universal_supplier.automatic_matching import run_post_ingest
        return run_post_ingest(self.config,self.code,external_ids)
    def progress(self,summary):
        from .adapter_registry import _finish_metadata
        self.summary.update(summary)
        total=self.summary.get('found');processed=self.summary.get('processed',0)
        phase={'PRODUCTS':'FETCHING_PRODUCTS'}.get(self.summary.get('phase'),self.summary.get('phase','QUEUED'))
        from .progress import PHASE_LABELS_RU
        speed=self.summary.get('http_cards_per_minute')
        saved=self.summary.get('sql_committed',0)
        progress={'phase':phase,'phase_label_ru':PHASE_LABELS_RU.get(phase,phase),'status':self.summary.get('status','running'),
            'total':total,'processed':processed,'succeeded':saved,'errors':self.summary.get('errors',0),
            'saved_percent':None if not total else min(100,100*saved/total),
            'deferred':self.summary.get('deferred',0),
            'percent':None if total is None else 100 if total==0 else min(100,100*processed/total),
            'rate_per_second':None if speed is None else speed/60,'eta_seconds':None,'eta_human_ru':'Время окончания пока неизвестно',
            'elapsed_seconds':time.monotonic()-self.started,'updated_at':datetime.now(timezone.utc).isoformat()}
        _finish_metadata(self.repo,self.run_id,{'source_runtime':self.summary,'progress':progress})
    def finish(self,result):
        from .adapter_registry import _finish_metadata
        # Actual material history of this run; read only. Never infer separate
        # price/stock changes from changed_offers (which also includes quantity).
        if getattr(self, 'is_batch', False) or getattr(self,'commercial_only',False):
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
