"""One bounded offline dispatch for manual UI and scheduled replay evaluation.

This RC never calls live collectors. Existing production orchestrators remain
unchanged; live capability is reported separately from frozen replay support.
"""
from datetime import datetime, timezone
import hashlib,json
from psycopg import sql

SUPPLIERS=('partner_st','optimum','beka_mak','beka_mak_tr','intervesp','kami','vekprom','ltt_msk','ltt_sib','ltt_yug','uss_stanko')

class FrozenDispatch:
    def __init__(self,connect):self.connect=connect

    def sources(self):
        with self.connect() as c:
            counts={code:(count,name) for code,count,name in c.execute('SELECT s.code,count(sp.id),s.name FROM suppliers s LEFT JOIN source_products sp ON sp.supplier_id=s.id GROUP BY s.code,s.name')}
            competitors=c.execute('SELECT code,name FROM competitor.competitor_sources ORDER BY code').fetchall()
        rows=[{'key':'supplier:'+x,'module':'supplier','source':x,'name':counts.get(x,(0,'Векпром'))[1],'records':counts.get(x,(0,''))[0],
               'snapshot_status':'BLOCKED_MISSING_VERIFIED_SOURCE_DATA' if x=='vekprom' else 'HISTORICAL_ONLY_NEW_SNAPSHOT_BLOCKED' if x=='intervesp' else 'AVAILABLE',
               'live_runner':'SHARED_JOB' if x in ('partner_st','optimum') else 'BOUNDED_COLLECTION_COMPONENT',
               'live_schedule_enabled':False} for x in SUPPLIERS]
        rows += [{'key':'competitor:'+x,'module':'competitor','source':x,'name':name,'snapshot_status':'AVAILABLE','live_runner':'MANUAL_CAPTURE' if x=='youmet' else 'DETACHED_WORKER','live_schedule_enabled':False} for x,name in competitors]
        return rows

    def enqueue(self,key,origin,slot=None):
        if origin not in ('OPERATOR_UI','CLI_MANUAL','SCHEDULED_REPLAY'):raise ValueError('Unsupported replay origin')
        known={r['key']:r for r in self.sources()}
        if key not in known:raise ValueError('Unknown namespaced source')
        if known[key]['snapshot_status']=='BLOCKED_MISSING_VERIFIED_SOURCE_DATA':return {'status':'BLOCKED_MISSING_VERIFIED_SOURCE_DATA'}
        if origin=='SCHEDULED_REPLAY' and not slot:raise ValueError('Scheduled replay requires exact due slot')
        with self.connect() as c,c.transaction():
            c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,19))',(key,))
            active=c.execute("SELECT id FROM rc_replay_jobs WHERE source_key=%s AND status IN ('queued','running') ORDER BY id LIMIT 1",(key,)).fetchone()
            if active:return {'status':'SKIP_ALREADY_RUNNING','job_id':active[0]}
            if slot:
                prior=c.execute('SELECT id FROM rc_replay_jobs WHERE source_key=%s AND due_slot=%s',(key,slot)).fetchone()
                if prior:return {'status':'SKIP_ALREADY_EVALUATED','job_id':prior[0]}
            job=c.execute('INSERT INTO rc_replay_jobs(source_key,origin,due_slot) VALUES(%s,%s,%s) RETURNING id',(key,origin,slot)).fetchone()[0]
            return {'status':'queued','job_id':job}

    def work_one(self):
        with self.connect() as c,c.transaction():
            row=c.execute("SELECT id,source_key FROM rc_replay_jobs WHERE status='queued' ORDER BY id FOR UPDATE SKIP LOCKED LIMIT 1").fetchone()
            if not row:return None
            job,key=row
            c.execute("UPDATE rc_replay_jobs SET status='running',started_at=now() WHERE id=%s",(job,))
            module,code=key.split(':',1)
            if module=='supplier':
                records=c.execute('SELECT sp.id,sp.external_id,sp.identity_hash,sp.last_seen_at FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id WHERE s.code=%s ORDER BY sp.id',(code,)).fetchall()
            else:
                records=c.execute('SELECT p.id,p.canonical_url,p.current_content_hash,p.last_seen_at FROM competitor.competitor_pages p JOIN competitor.competitor_sources s ON s.id=p.source_id WHERE s.code=%s ORDER BY p.id',(code,)).fetchall()
            result={'mode':'FROZEN_DATABASE_REPLAY','rows':len(records),'payload_sha256':hashlib.sha256(json.dumps(records,default=str,separators=(',',':')).encode()).hexdigest(),'external_http':0,'source_writes':0,'matching_applied':False}
            from psycopg.types.json import Jsonb
            c.execute("UPDATE rc_replay_jobs SET status='succeeded',finished_at=now(),result=%s WHERE id=%s",(Jsonb(result),job))
            return {'job_id':job,**result}

    def evaluate_due(self,now,*,simulate_enabled=False):
        """Pure bounded due evaluation. No daemon or live schedule activation."""
        if now.tzinfo is None:raise ValueError('Timezone-aware clock required')
        now=now.astimezone(timezone.utc)
        from croniter import croniter
        if not simulate_enabled:return []
        if not croniter.match('0 8 * * *',now):return []
        slot=now.replace(second=0,microsecond=0)
        return [self.enqueue(s['key'],'SCHEDULED_REPLAY',slot) for s in self.sources()]

    def history(self):
        with self.connect() as c:
            return c.execute('SELECT id,source_key,origin,status,created_at,result FROM rc_replay_jobs ORDER BY id DESC LIMIT 100').fetchall()
