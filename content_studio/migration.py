"""Consistent read-only SQLite -> explicit PostgreSQL migration, no credentials.

The importer keeps original IDs, serialized payloads and revision hashes. It is
never invoked by application startup. Prepared/applied VK records are not replayed.
"""
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path
from psycopg import sql
from .postgres_store import LOCK_ID

TABLE_ORDER=('projects','revisions','media','composition_runs','ai_settings','ai_gateway','vk_settings','vk_intents','vk_steps')
SINGLETONS=frozenset({'ai_settings','ai_gateway','vk_settings'})


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()


def fingerprint(rows):
    return {'rows':len(rows),'sha256':digest(sorted(digest(dict(r)) for r in rows))}


def read_source(directory):
    root=Path(directory).resolve();db=root/'studio.sqlite3'
    if not db.is_file() or db.is_symlink():raise ValueError('SOURCE_SQLITE_REQUIRED')
    rows={}
    with sqlite3.connect(db.as_uri()+'?mode=ro',uri=True) as c:
        c.row_factory=sqlite3.Row;c.execute('BEGIN')
        if c.execute('PRAGMA user_version').fetchone()[0]!=1:raise ValueError('SOURCE_SCHEMA_VERSION_REQUIRED')
        if c.execute('PRAGMA quick_check').fetchone()[0]!='ok':raise ValueError('SOURCE_INTEGRITY_FAILED')
        for name in TABLE_ORDER:rows[name]=[dict(r) for r in c.execute('SELECT * FROM '+name)]
        c.rollback()
    media={}
    for row in rows['media']:
        aid=row['id']
        if not re.fullmatch('[0-9a-f]{32}',aid):raise ValueError('SOURCE_MEDIA_ID_INVALID')
        metadata=json.loads(row['metadata'])
        for suffix,key in (('original','original_hash'),('jpg','jpeg_hash')):
            p=root/'media'/(aid+'.'+suffix)
            if p.is_symlink() or not p.is_file():raise ValueError('SOURCE_MEDIA_MISSING')
            raw=p.read_bytes()
            if hashlib.sha256(raw).hexdigest()!=metadata.get(key):raise ValueError('SOURCE_MEDIA_HASH_MISMATCH')
            media[p.name]=raw
    # Preserve any unreferenced immutable originals too; do not silently delete them.
    for p in (root/'media').iterdir():
        if p.is_file() and p.name not in media:
            if p.is_symlink() or not re.fullmatch(r'[0-9a-f]{32}\.(?:jpg|original)',p.name):raise ValueError('SOURCE_MEDIA_UNEXPECTED_FILE')
            media[p.name]=p.read_bytes()
    manifest={'format':'content-studio-sqlite-import/v1','tables':{t:fingerprint(v) for t,v in rows.items()},
              'media':{k:hashlib.sha256(v).hexdigest() for k,v in sorted(media.items())},'secret_policy':'DPAPI_NOT_READ_RECONNECT_REQUIRED'}
    return {'rows':rows,'media':media,'manifest':manifest,'source_sha256':digest(manifest)}


def target_fingerprints(connection):
    from psycopg.rows import dict_row
    result={}
    for name in TABLE_ORDER:
        with connection.cursor(row_factory=dict_row) as q:
            q.execute(sql.SQL('SELECT * FROM content_studio.{}').format(sql.Identifier(name)))
            result[name]=fingerprint(q.fetchall())
    return result


def import_snapshot(connection,snapshot,directory,owners,*,legacy_admin_only=False,apply_schema=False):
    """Caller must validate the explicit target identity and operator approval."""
    ids={r['id'] for r in snapshot['rows']['projects']}
    if set(owners)-ids or not legacy_admin_only and set(owners)!=ids:raise ValueError('EXPLICIT_PROJECT_OWNER_MAP_REQUIRED')
    if any(type(v) is not int or v<=0 for v in owners.values()):raise ValueError('PROJECT_OWNER_ID_INVALID')
    manifest={**snapshot['manifest'],'owners':owners,'unassigned_legacy_admin_only':sorted(ids-set(owners))}
    plan_hash=digest(manifest);root=Path(directory).resolve();media_root=root/'media'
    created=[]
    try:
        with connection.transaction():
            connection.execute('SELECT pg_advisory_xact_lock(%s)',(LOCK_ID,))
            exists=connection.execute("SELECT to_regnamespace('content_studio')").fetchone()[0]
            if not exists:
                if not apply_schema:raise ValueError('TARGET_MIGRATION_020_REQUIRED')
                ddl=(Path(__file__).resolve().parents[1]/'migrations/020_content_studio.sql').read_text(encoding='utf-8')
                # One transaction for schema + rows + receipt; no intermediate COMMIT.
                connection.execute(ddl.replace('BEGIN;','',1).rsplit('COMMIT;',1)[0])
            if connection.execute('SELECT version FROM content_studio.schema_version').fetchone()[0]!=1:raise ValueError('TARGET_SCHEMA_VERSION_MISMATCH')
            previous=connection.execute('SELECT manifest FROM content_studio.migration_receipts WHERE source_sha256=%s',(snapshot['source_sha256'],)).fetchone()
            if previous:
                if digest(json.loads(previous[0]))!=plan_hash:raise ValueError('PREVIOUS_IMPORT_OWNER_PLAN_DIFFERS')
                return {'status':'ALREADY_IMPORTED_NOT_REPLAYED','source_sha256':snapshot['source_sha256'],'plan_sha256':plan_hash}
            if any(connection.execute(sql.SQL('SELECT EXISTS(SELECT 1 FROM content_studio.{})').format(sql.Identifier(t))).fetchone()[0] for t in TABLE_ORDER if t not in SINGLETONS):
                raise ValueError('TARGET_NOT_EMPTY_NEVER_OVERWRITE')
            defaults={'ai_settings':{},'vk_settings':{},'ai_gateway':{'revision':0,'profiles':{},'tasks':{},'default':'legacy'}}
            for name,expected in defaults.items():
                current=connection.execute(sql.SQL('SELECT payload FROM content_studio.{} WHERE singleton=1').format(sql.Identifier(name))).fetchone()
                if current and json.loads(current[0])!=expected:raise ValueError('TARGET_SETTINGS_CHANGED_NEVER_OVERWRITE')
            for owner in set(owners.values()):
                if not connection.execute('SELECT 1 FROM public.app_users WHERE id=%s AND is_active',(owner,)).fetchone():raise ValueError('OWNER_NOT_ACTIVE_EXISTING_RC_USER')
            for table in TABLE_ORDER:
                for row in snapshot['rows'][table]:
                    names=list(row);query=sql.SQL('INSERT INTO content_studio.{} ({}) VALUES ({})').format(
                        sql.Identifier(table),sql.SQL(',').join(map(sql.Identifier,names)),sql.SQL(',').join(sql.Placeholder() for _ in names))
                    if table in SINGLETONS:query+=sql.SQL(' ON CONFLICT(singleton) DO UPDATE SET payload=EXCLUDED.payload')
                    connection.execute(query,[row[n] for n in names])
            for pid,owner in owners.items():
                connection.execute('INSERT INTO content_studio.project_access(project_id,user_id,can_edit) VALUES(%s,%s,true)',(pid,owner))
            maximum=connection.execute('SELECT max(id) FROM content_studio.vk_steps').fetchone()[0]
            connection.execute("SELECT setval(pg_get_serial_sequence('content_studio.vk_steps','id'),%s,%s)",(maximum or 1,maximum is not None))
            if target_fingerprints(connection)!=snapshot['manifest']['tables']:raise ValueError('TARGET_DATA_HASH_MISMATCH')
            media_root.mkdir(parents=True,exist_ok=True,mode=0o700)
            for name,raw in snapshot['media'].items():
                path=media_root/name
                if path.is_symlink() or path.resolve().parent!=media_root.resolve():raise ValueError('TARGET_MEDIA_PATH_INVALID')
                if path.exists():
                    if hashlib.sha256(path.read_bytes()).hexdigest()!=snapshot['manifest']['media'][name]:raise ValueError('TARGET_MEDIA_EXISTS_WITH_OTHER_BYTES')
                else:
                    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600);created.append(path)
                    with os.fdopen(fd,'wb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
            connection.execute('INSERT INTO content_studio.migration_receipts(source_sha256,manifest) VALUES(%s,%s)',(snapshot['source_sha256'],json.dumps(manifest,ensure_ascii=False,sort_keys=True,separators=(',',':'))))
    except BaseException:
        # Only this invocation's new immutable files, inside the verified target.
        for p in created:
            if p.resolve().parent==media_root.resolve():p.unlink(missing_ok=True)
        raise
    return {'status':'IMPORTED_VERIFIED','source_sha256':snapshot['source_sha256'],'plan_sha256':plan_hash,
            'tables':snapshot['manifest']['tables'],'media_files':len(snapshot['media']),'legacy_admin_only':legacy_admin_only,
            'secrets_transferred':False,'external_calls':0}
