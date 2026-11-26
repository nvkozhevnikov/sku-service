"""PostgreSQL storage adapter preserving the existing Studio use cases and bytes.

All SQL is internal, parameterized, limited to this schema. Initialization never
creates a schema or migrates data. The migration is an explicit separate command.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
import hashlib
import json
import re

from .storage import Store, packed

CURRENT_ACTOR = ContextVar('content_studio_actor', default=None)
LOCK_ID = 762603161120
TABLES = frozenset({'projects','revisions','media','composition_runs','ai_settings','ai_gateway',
                   'vk_settings','vk_intents','vk_steps','project_access','audit_events'})


class Row(dict):
    def __getitem__(self, key):
        return tuple(self.values())[key] if isinstance(key,int) else super().__getitem__(key)


class Result:
    def __init__(self,cursor):self.cursor=cursor
    @property
    def rowcount(self):return self.cursor.rowcount
    def fetchone(self):
        value=self.cursor.fetchone()
        return None if value is None else Row(value)
    def fetchall(self):return [Row(v) for v in self.cursor.fetchall()]
    def __iter__(self):return iter(self.fetchall())


class Connection:
    def __init__(self,connection):self.raw=connection
    def execute(self,query,params=()):
        if query.strip().upper()=='BEGIN IMMEDIATE':
            return Result(self.raw.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,)))
        ignore=query.startswith('INSERT OR IGNORE INTO ')
        if ignore:query=query.replace('INSERT OR IGNORE INTO ','INSERT INTO ',1)
        # These are internal SQL identifiers, never request input. Qualifying every
        # table avoids a malicious/public search_path shadowing of the data store.
        for table in sorted(TABLES,key=len,reverse=True):
            query=re.sub(r'(?i)(\b(?:FROM|JOIN|INTO|UPDATE)\s+)'+table+r'\b',
                         lambda m:m.group(1)+'content_studio.'+table,query)
        query=query.replace('?', '%s')
        if ignore:query+=' ON CONFLICT DO NOTHING'
        return Result(self.raw.execute(query,params))


class PostgresStore(Store):
    dialect='postgresql'
    initialize_schema=False

    def __init__(self,directory,connect):
        self.root=Path(directory).resolve();self.connect=connect
        # Readiness only; no DDL or user-data write, even on startup.
        with self.connection() as c:
            row=c.raw.execute('SELECT version FROM content_studio.schema_version').fetchone()
            if not row or row['version']!=1:raise ValueError('CONTENT_STUDIO_MIGRATION_REQUIRED')
        (self.root/'media').mkdir(parents=True,exist_ok=True,mode=0o700)

    @contextmanager
    def connection(self):
        from psycopg.rows import dict_row
        with self.connect() as c:
            c.row_factory=dict_row
            with c.transaction():yield Connection(c)

    def _created(self,c,project):
        actor=CURRENT_ACTOR.get()
        if actor is None:raise PermissionError('CONTENT_STUDIO_ACTOR_REQUIRED')
        c.execute('INSERT INTO project_access(project_id,user_id,can_edit) VALUES(?,?,?)',(project['id'],actor.id,True))
        c.execute('INSERT INTO audit_events(actor_id,action,object_id) VALUES(?,?,?)',(actor.id,'PROJECT_CREATED',project['id']))

    def access(self,pid,actor,write=False):
        if actor is None:return False
        if actor.role=='ADMIN':return True
        with self.connection() as c:
            row=c.execute('SELECT can_edit FROM project_access WHERE project_id=? AND user_id=?',(pid,actor.id)).fetchone()
        return bool(row and (not write or row['can_edit']))

    def get(self,pid):
        if not self.access(pid,CURRENT_ACTOR.get()):raise KeyError('Материал не найден или недоступен.')
        return super().get(pid)

    def list(self,search=''):
        actor=CURRENT_ACTOR.get()
        if actor is None:raise PermissionError('CONTENT_STUDIO_ACTOR_REQUIRED')
        rows=super().list(search)
        if actor.role=='ADMIN':return rows
        with self.connection() as c:
            visible={r[0] for r in c.execute('SELECT project_id FROM project_access WHERE user_id=?',(actor.id,))}
        return [r for r in rows if r['id'] in visible]

    def audit(self,actor,action,pid=None):
        with self.connection() as c:c.execute('INSERT INTO audit_events(actor_id,action,object_id) VALUES(?,?,?)',(actor.id,action,pid))

    @contextmanager
    def dispatch_guard(self,ident):
        """Session lease lasts across external requests; a crash releases it."""
        key=int.from_bytes(hashlib.sha256(ident.encode()).digest()[:4],'big',signed=True)
        with self.connect() as c:
            from psycopg.rows import tuple_row
            c.row_factory=tuple_row
            acquired=c.execute('SELECT pg_try_advisory_lock(%s,%s)',(76260316,key)).fetchone()[0]
            try:yield acquired
            finally:
                if acquired:c.execute('SELECT pg_advisory_unlock(%s,%s)',(76260316,key))

    def recover_inflight(self):
        with self.connection() as c:ids=[r[0] for r in c.execute("SELECT id FROM vk_intents WHERE state='SENDING'")]
        for ident in ids:
            with self.dispatch_guard(ident) as acquired:
                if acquired:
                    with self.connection() as c:
                        c.execute('BEGIN IMMEDIATE')
                        c.execute("UPDATE vk_intents SET state='UNCERTAIN',result=? WHERE id=? AND state='SENDING'",
                                  (packed({'message':'Отправляющий процесс завершился. Проверьте результат ВК вручную; автоматический повтор запрещён.'}),ident))
