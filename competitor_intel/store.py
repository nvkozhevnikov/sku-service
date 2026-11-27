"""Fail-closed dedicated PostgreSQL access, append-only versions and observations."""
from dataclasses import asdict
import hashlib
import json
import os
from pathlib import Path
import re
import psycopg
from psycopg.conninfo import conninfo_to_dict
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from . import EXTRACTOR_VERSION
from .model import SOURCES

DB_NAME = 'sterbrust_competitor_intel'
PORT = '55454'

def json_value(value):
    return json.loads(json.dumps(value,default=str,ensure_ascii=False))

def server_identity():
    """Compose server (docker-compose.server.yml): the target comes from the server
    environment, not the historical Windows defaults. Every role check still applies."""
    if os.environ.get('STERBRUST_COMPETITOR_SERVER') != 'YES':
        return None
    system = os.environ.get('DB_SYSTEM_IDENTIFIER', '')
    name, host, port = (os.environ.get(k, '') for k in ('DB_NAME', 'DB_HOST', 'DB_PORT'))
    if not re.fullmatch(r'[0-9]{15,20}', system) or not name or not host or not re.fullmatch(r'[0-9]{1,5}', port or ''):
        raise ValueError('Competitor server identity requires DB_NAME, DB_HOST, DB_PORT and DB_SYSTEM_IDENTIFIER')
    return {'dbname': name, 'host': host, 'port': port, 'user': 'competitor_runtime', 'system': system}


class Store:
    def __init__(self, dsn=None):
        dsn = dsn or os.environ.get('COMPETITOR_INTEL_DSN')
        if not dsn: raise ValueError('COMPETITOR_INTEL_DSN required; no supplier DSN fallback')
        cfg = conninfo_to_dict(dsn)
        if os.environ.get('STERBRUST_UNIFIED_RC') == 'YES':
            self._connect_unified(dsn, cfg)
            return
        if cfg.get('dbname') != DB_NAME or cfg.get('host') != '127.0.0.1' or cfg.get('port') != PORT or cfg.get('user') != 'competitor_dev':
            raise ValueError('Only dedicated local competitor_dev / 127.0.0.1:55454 / sterbrust_competitor_intel allowed')
        self.conn = psycopg.connect(dsn,row_factory=dict_row)
        with self.conn.cursor() as c:
            c.execute('SELECT current_database() AS db, current_user AS role, host(inet_server_addr()) AS host, inet_server_port() AS port')
            actual = c.fetchone()
            if actual != {'db': DB_NAME, 'role':'competitor_dev','host':'127.0.0.1','port':int(PORT)}:
                self.conn.close(); raise ValueError('Actual database identity mismatch')
            c.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename NOT LIKE 'competitor_%'")
            if c.fetchone(): self.conn.close(); raise ValueError('Foreign business tables present: isolation failed')
        self.conn.commit()

    def _connect_unified(self, dsn, cfg):
        """Explicit RC identity, schema and grants; dedicated legacy guard remains."""
        expected = {'dbname':'sterbrust_unified_rc_20261019', 'host':'127.0.0.1',
                    'port':'55461', 'user':'app_competitor'}
        main = os.environ.get('STERBRUST_COMPETITOR_MAIN') == 'YES'
        server = server_identity() if main else None
        if server:
            expected = {k: server[k] for k in ('dbname', 'host', 'port', 'user')}
        elif main:
            expected.update(dbname='rc_verify_vi_20261019', user='competitor_runtime')
            if os.environ.get('STERBRUST_COMPETITOR_REHEARSAL') == 'YES':
                expected['dbname']='ci_restore_target_20261028'
        elif os.environ.get('STERBRUST_UNIFIED_RC_RESTORE') == 'YES':
            expected['dbname']='sterbrust_unified_restore_20261019'
        if any(cfg.get(k) != v for k,v in expected.items()):
            raise ValueError('Unified competitor identity mismatch')
        conn = psycopg.connect(dsn, row_factory=dict_row, connect_timeout=5)
        try:
            with conn.cursor() as c:
                c.execute('SELECT current_database() db,current_user role,host(inet_server_addr()) host,inet_server_port() port')
                actual = c.fetchone()
                if server:
                    # A compose service name resolves to a container address; the
                    # cluster identifier below pins the server instead.
                    actual.pop('host')
                    wanted = {'db':expected['dbname'],'role':expected['user'],'port':int(expected['port'])}
                else:
                    wanted = {'db':expected['dbname'],'role':expected['user'], 'host':expected['host'],'port':int(expected['port'])}
                if actual != wanted:
                    raise ValueError('Actual unified database identity mismatch')
                c.execute("SHOW search_path")
                if c.fetchone()['search_path'] != 'competitor, pg_catalog':
                    raise ValueError('Unsafe unified competitor search_path')
                c.execute("SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user")
                if any(c.fetchone().values()):
                    raise ValueError('Privileged competitor role forbidden')
                if main:
                    c.execute("SELECT system_identifier::text system FROM pg_control_system()")
                    from universal_supplier.partner_pilot import SYSTEM
                    if c.fetchone()['system'] != (server['system'] if server else SYSTEM):
                        raise ValueError('Main competitor cluster identity mismatch')
                    c.execute("SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='competitor' AND t.relkind IN ('r','p') AND has_table_privilege(current_user,t.oid,'DELETE,TRUNCATE,REFERENCES,TRIGGER') LIMIT 1")
                    if c.fetchone():
                        raise ValueError('Destructive competitor permission forbidden')
                    c.execute("SELECT has_schema_privilege(current_user,'competitor','CREATE') unsafe")
                    if c.fetchone()['unsafe']:
                        raise ValueError('Competitor runtime DDL forbidden')
                c.execute("SELECT has_schema_privilege(current_user,'public','CREATE') unsafe")
                if c.fetchone()['unsafe']:
                    raise ValueError('Cross-schema DDL permission forbidden')
                c.execute("SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relkind IN ('r','p') AND has_table_privilege(current_user,t.oid,'SELECT,INSERT,UPDATE,DELETE,TRUNCATE,REFERENCES,TRIGGER') LIMIT 1")
                if c.fetchone():
                    raise ValueError('Cross-schema table permission forbidden')
                c.execute("SELECT 1 FROM pg_class t JOIN pg_namespace n ON n.oid=t.relnamespace WHERE n.nspname='public' AND t.relkind='S' AND has_sequence_privilege(current_user,t.oid,'USAGE,UPDATE') LIMIT 1")
                if c.fetchone():
                    raise ValueError('Cross-schema sequence permission forbidden')
                c.execute("SELECT marker FROM competitor.rc_identity")
                if c.fetchone()['marker'] != 'STERBRUST_UNIFIED_RC_20261019':
                    raise ValueError('Missing unified identity marker')
            conn.commit()
        except Exception:
            conn.close()
            raise
        self.conn = conn

    def close(self): self.conn.close()

    def migrate(self):
        with self.conn.transaction(), self.conn.cursor() as c:
            c.execute(Path(__file__).with_name('schema.sql').read_text(encoding='utf-8'))
            for code, s in SOURCES.items():
                c.execute('INSERT INTO competitor_sources(code,name,base_url,region) VALUES (%s,%s,%s,%s) ON CONFLICT(code) DO NOTHING',(code,s['name'],s['base_url'],s['region']))

    def run_start(self,since):
        with self.conn.transaction(), self.conn.cursor() as c:
            c.execute('INSERT INTO competitor_runs(since_date) VALUES (%s) RETURNING id',(since,)); return c.fetchone()['id']

    def run_finish(self,run_id,result):
        with self.conn.transaction(), self.conn.cursor() as c:
            c.execute('UPDATE competitor_runs SET finished_at=now(),status=%s,result=%s WHERE id=%s',('BLOCKED' if result['errors'] else 'DONE',Jsonb(json_value(result)),run_id))

    def save(self,page,capture,run_id):
        if not page.inclusion_reason: return {'excluded':1,'pages':0,'versions':0,'items':0}
        digest = page.content_hash()
        with self.conn.transaction(), self.conn.cursor() as c:
            c.execute('SELECT id FROM competitor_sources WHERE code=%s',(page.source,)); source_id = c.fetchone()['id']
            c.execute('SELECT id FROM competitor_pages WHERE source_id=%s AND canonical_url=%s',(source_id,page.canonical_url)); old = c.fetchone()
            c.execute('''INSERT INTO competitor_pages(source_id,page_type,canonical_url,title,published_at,valid_from,valid_to,inclusion_reason,first_seen_at,last_seen_at,current_content_hash)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                         ON CONFLICT(source_id,canonical_url) DO UPDATE SET title=excluded.title,page_type=excluded.page_type,published_at=excluded.published_at,
                         valid_from=excluded.valid_from,valid_to=excluded.valid_to,inclusion_reason=excluded.inclusion_reason,last_seen_at=excluded.last_seen_at,current_content_hash=excluded.current_content_hash RETURNING id''',
                      (source_id,page.page_type,page.canonical_url,page.title,page.published_at,page.valid_from,page.valid_to,page.inclusion_reason,capture.fetched_at,capture.fetched_at,digest))
            page_id = c.fetchone()['id']
            c.execute('''INSERT INTO competitor_page_versions(page_id,fetched_at,content_hash,title,raw_text,normalized_text,raw_capture_ref,extractor_version,payload)
                         VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(page_id,content_hash) DO NOTHING RETURNING id''',
                      (page_id,capture.fetched_at,digest,page.title,page.raw_text,page.normalized_text,capture.raw_ref,getattr(page,'extractor_version',EXTRACTOR_VERSION),Jsonb(json_value({**page.payload(),'publication_evidence':page.publication_evidence,**({'campaign_evidence':page.campaign_evidence} if hasattr(page,'campaign_evidence') else {}),**({'item_metadata':page.item_metadata} if hasattr(page,'item_metadata') else {})}))))
            new_version = c.fetchone()
            if new_version:
                version_id = new_version['id']
                for item in page.items:
                    d = asdict(item)
                    key = hashlib.sha256(json.dumps([item.product_url,item.product_name,item.campaign_name,item.tab_title],ensure_ascii=False).encode()).hexdigest()
                    cols = ['campaign_name','tab_title','product_name','product_url','brand','model','category','old_price','new_price','currency','discount_amount','discount_percent','displayed_discount_raw','price_type','price_label_raw','availability','region','priority','evidence_html','warnings']
                    values = [Jsonb(d[k]) if k=='warnings' else d[k] for k in cols]
                    c.execute('INSERT INTO competitor_campaign_items(page_version_id,item_key,observed_at,'+','.join(cols)+') VALUES('+','.join(['%s']*(len(cols)+3))+')', [version_id,key,capture.fetched_at]+values)
                c.execute("INSERT INTO competitor_ai_analysis(page_version_id,content_hash,provider,model,prompt_version,analysis_status) VALUES(%s,%s,'none','none','v1','NOT_REQUESTED')",(version_id,digest))
            else:
                c.execute('SELECT id FROM competitor_page_versions WHERE page_id=%s AND content_hash=%s',(page_id,digest)); version_id = c.fetchone()['id']
            c.execute('''INSERT INTO competitor_observations(run_id,page_id,page_version_id,observed_at,observed_url,raw_capture_ref,raw_hash,transport) VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(run_id,page_id) DO NOTHING''',
                      (run_id,page_id,version_id,capture.fetched_at,capture.url,capture.raw_ref,capture.raw_hash,capture.transport))
        return {'excluded':0,'pages':int(old is None),'versions':int(new_version is not None),'items':len(page.items) if new_version else 0}

    def query(self,sql,params=()):
        with self.conn.transaction(), self.conn.cursor() as c:
            c.execute(sql,params); return c.fetchall()

    def counts(self):
        return self.query('''SELECT s.code, count(DISTINCT p.id) AS pages, count(DISTINCT v.id) AS versions,
                            count(DISTINCT i.id) AS campaign_items, count(DISTINCT i.id) FILTER(WHERE i.old_price IS NOT NULL AND i.new_price IS NOT NULL) AS old_new,
                            count(DISTINCT i.id) FILTER(WHERE i.priority='HIGH') AS high
                            FROM competitor_sources s LEFT JOIN competitor_pages p ON p.source_id=s.id
                            LEFT JOIN competitor_page_versions v ON v.page_id=p.id
                            LEFT JOIN competitor_campaign_items i ON i.page_version_id=v.id GROUP BY s.code ORDER BY s.code''')
