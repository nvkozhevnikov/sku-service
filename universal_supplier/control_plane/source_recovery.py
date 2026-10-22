"""Explicit, hash-approved recovery of frozen LIVE11 jobs in the existing queue.

Preparation is SELECT-only. An approval file grants no permission by itself.
Its exact SHA, current HEAD and narrowly checked engine delta must be confirmed
in the operator session; HTTP/SQL/host/role guards remain separate prerequisites.
"""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
import hashlib
import json
import os
import subprocess
from functools import lru_cache
from io import BytesIO

from .source_resume import digest

ENV = 'STERBRUST_LIVE11_RECOVERY'
ENGINE_CHANGES = {
    'universal_supplier/control_plane/source_recovery.py',
    'universal_supplier/control_plane/source_batch.py',
    'universal_supplier/control_plane/source_runtime.py',
    'universal_supplier/control_plane/queue.py',
}
FILES = ('DURABLE_MATCHING_RESULT.json', 'VERIFIED_DURABLE.json',
         'FINAL_VERIFICATION.json', 'BEFORE.json')


def prepare(queue, evidence_dir, batch_id):
    """SELECT-only approval material. Does not confirm compatibility or activate."""
    from . import source_batch as batch
    from .source_runtime import job_dir
    artifacts={name:{'path':str((Path(evidence_dir)/name).resolve()),
                     'sha256':file_sha(Path(evidence_dir)/name)} for name in FILES}
    with queue._connect() as connection, connection.cursor() as cursor:
        connection.read_only=True
        batch.validate_config(queue.config);batch.validate_connection(connection)
        cursor.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(cursor.fetchone()==('rc_runtime',False,False),'RECOVERY_ROLE')
        cursor.execute('''SELECT id,supplier_id,status,crawl_run_id,settings_snapshot FROM crawl_jobs
            WHERE settings_snapshot->'source_batch'->>'id'=%s ORDER BY id''',(batch_id,))
        rows=cursor.fetchall()
        require(len(rows)==11 and [r[0] for r in rows]==list(range(17,28)), 'RECOVERY_EXACT_PARENTS_REQUIRED')
        require(rows[0][2:4]==('partial',18) and all(r[2:4]==('cancelled',None) for r in rows[1:]),
                'RECOVERY_PARENT_STATE_CHANGED')
        cursor.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running')")
        require(not cursor.fetchall(),'RECOVERY_ACTIVE_JOBS')
        cursor.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code')
        settings=normal(cursor.fetchall())
    envelopes=[];inputs=[]
    for _,_,_,_,snapshot in rows:
        for phase in (snapshot,snapshot['source_batch']['next_snapshot']):
            require(phase is not None,'RECOVERY_EXPECTS_22_PHASES')
            require(digest(batch.phase_scope(phase))==phase['source_batch']['scope_sha256'],'RECOVERY_SCOPE_CHANGED')
            envelopes.append(phase['source_batch']);inputs.append(phase['source_input'])
    first=envelopes[0]
    require({s['supplier_code'] for *_,s in rows}==set(batch.SOURCE_ORDER),'RECOVERY_EXPECTS_ELEVEN')
    require(all(e['head']==first['head'] and e['started_at']==first['started_at'] and
        e['deadline']==first['deadline'] and e['matrix_sha256']==batch.MATRIX_SHA and e['id']==batch_id
        for e in envelopes),'RECOVERY_BATCH_ENVELOPES_CHANGED')
    value={'format':'live11-recovery/v1','batch_id':batch_id,'started_at':first['started_at'],
        'deadline':first['deadline'],'matrix_sha256':batch.MATRIX_SHA,
        'compatibility':compatibility(batch.ROOT,first['head'],inputs[0]['engine_sha256']),
        'parents':[{'id':jid,'supplier_id':sid,'snapshot':snap} for jid,sid,_,_,snap in rows],
        'scope_hashes':sorted(e['scope_sha256'] for e in envelopes),
        'input_hashes':sorted(digest(v) for v in inputs),'envelopes':envelopes,
        'artifacts':artifacts,'settings_before':settings,'activation_matrix':batch.matrix_rows()}
    data=read_artifacts(value)
    cp_path=job_dir('beka_mak',17)/'checkpoint.json'
    require(file_sha(cp_path)==data['DURABLE_MATCHING_RESULT.json']['checkpoint_sha256'],'RECOVERY_CHECKPOINT_CHANGED')
    require(all(v['engine_sha256']==value['compatibility']['base_engine'] for v in inputs),'RECOVERY_OLD_ENGINE_CHANGED')
    checkpoint=json.loads(cp_path.read_text(encoding='utf-8'))
    validate_saved(value,data,checkpoint,inputs[0])
    with queue._connect() as connection,connection.cursor() as cursor:
        connection.read_only=True
        verify_sql(cursor,data,checkpoint,inputs[0],queue.config)
    return value


def require(value, reason):
    if not value:
        raise PermissionError(reason)


def normal(value):
    return json.loads(json.dumps(value, default=str))


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@lru_cache(maxsize=4)
def base_files(root,base_head,expected):
    # Git stores LF while this Windows checkout originally contained a mix of
    # LF/CRLF. Reconstruct exact prior bytes only for the three reviewed edits;
    # the immutable input digest must select a unique matching combination.
    from itertools import product
    paths=subprocess.check_output(['git','ls-tree','-r','--name-only',base_head,
        '--','universal_supplier','sterbrust_matching'],cwd=root,text=True).splitlines()
    paths=[p for p in paths if p.endswith('.py')]
    stream=BytesIO(subprocess.check_output(['git','cat-file','--batch'],
        input=(''.join(base_head+':'+p+'\n' for p in paths)).encode(),cwd=root))
    fixed={};variants={}
    for path in paths:
        header=stream.readline().split();require(len(header)==3,'RECOVERY_BASE_BLOB')
        raw=stream.read(int(header[2]));require(stream.read(1)==b'\n','RECOVERY_BASE_FRAME')
        if path in ENGINE_CHANGES:
            lf=raw.replace(b'\r\n',b'\n')
            variants[path]=list(dict.fromkeys([hashlib.sha256(lf).hexdigest(),hashlib.sha256(lf.replace(b'\n',b'\r\n')).hexdigest()]))
        else:
            actual=(root/path).read_bytes()
            require(actual.replace(b'\r\n',b'\n')==raw.replace(b'\r\n',b'\n'),'RECOVERY_UNCHANGED_FILE_CHANGED')
            fixed[path]=hashlib.sha256(actual).hexdigest()
    matches=[]
    for choice in product(*variants.values()):
        files={**fixed,**dict(zip(variants,choice))}
        if digest(files)==expected:matches.append(files)
    require(len(matches)==1,'RECOVERY_BASE_ENGINE_NOT_RECONSTRUCTED')
    return matches[0]


def compatibility(root, base_head, base_engine):
    from .source_runtime import engine_fingerprint
    from .source_batch import current_head
    head=current_head()
    changed=subprocess.check_output(['git','diff','--name-only',base_head,head,
        '--','universal_supplier','sterbrust_matching'],cwd=root,text=True).splitlines()
    require(set(changed)<=ENGINE_CHANGES,'RECOVERY_UNREVIEWED_ENGINE_CHANGE')
    require(set(changed),'RECOVERY_NO_COMPATIBILITY_DELTA')
    require(not subprocess.check_output(['git','status','--porcelain'],cwd=root,text=True).strip(),
            'RECOVERY_DIRTY_CHECKOUT')
    tracked=set(subprocess.check_output(['git','ls-tree','-r','--name-only',head,
        '--','universal_supplier','sterbrust_matching'],cwd=root,text=True).splitlines())
    actual={p.relative_to(root).as_posix() for folder in ('universal_supplier','sterbrust_matching') for p in (root/folder).rglob('*.py')}
    require(actual=={p for p in tracked if p.endswith('.py')},'RECOVERY_UNTRACKED_ENGINE')
    files=base_files(root,base_head,base_engine)
    return {'base_head':base_head,'head':head,'base_engine':digest(files),
            'engine':engine_fingerprint(),'changed_engine_files':sorted(changed),
            'base_files_sha256':files}


def approval():
    """No implicit legacy-hash acceptance: only this exact confirmed manifest."""
    from .source_batch import ROOT
    path = os.environ.get(ENV + '_FILE')
    sha = os.environ.get(ENV + '_SHA256')
    require(path and sha and os.environ.get(ENV + '_CONFIRM') == sha,
            'RECOVERY_OPERATOR_CONFIRMATION_REQUIRED')
    require(file_sha(path) == sha, 'RECOVERY_APPROVAL_CHANGED')
    value = json.loads(Path(path).read_text(encoding='utf-8'))
    require(value.get('format') == 'live11-recovery/v1', 'RECOVERY_APPROVAL_FORMAT')
    require(value['compatibility'] == compatibility(ROOT, value['compatibility']['base_head'],value['compatibility']['base_engine']),
            'RECOVERY_HEAD_ENGINE_COMPATIBILITY_CHANGED')
    require(datetime.now(timezone.utc) < datetime.fromisoformat(value['deadline']),
            'BATCH_WINDOW_EXPIRED')
    for artifact in value['artifacts'].values():
        require(file_sha(artifact['path']) == artifact['sha256'], 'RECOVERY_EVIDENCE_CHANGED')
    return value, sha


def allow_old_head(envelope):
    value, _ = approval()
    require(envelope['head'] == value['compatibility']['base_head'] and
            envelope['id'] == value['batch_id'] and
            envelope['started_at'] == value['started_at'] and
            envelope['deadline'] == value['deadline'] and
            envelope['matrix_sha256'] == value['matrix_sha256'] and
            envelope['scope_sha256'] in value['scope_hashes'], 'RECOVERY_ENVELOPE_CHANGED')
    expected = next(e for e in value['envelopes'] if e['scope_sha256']==envelope['scope_sha256'])
    require(all(envelope.get(k)==v for k,v in expected.items() if k!='attempt'), 'RECOVERY_APPROVED_ENVELOPE_CHANGED')
    require(type(envelope['attempt']) is int and 1 <= envelope['attempt'] <= 3,
            'BATCH_RESUME_ATTEMPTS_EXHAUSTED')


def allow_old_input(value):
    manifest, _ = approval()
    require(value['engine_sha256'] == manifest['compatibility']['base_engine'] and
            digest(value) in manifest['input_hashes'], 'RECOVERY_INPUT_NOT_APPROVED')


def read_artifacts(value):
    return {name: json.loads(Path(artifact['path']).read_text(encoding='utf-8'))
            for name, artifact in value['artifacts'].items()}


def validate_saved(value, data, checkpoint, input_value):
    """Pure receipt/checkpoint contract, shared with saved real fixture tests."""
    receipt = data['DURABLE_MATCHING_RESULT.json']
    verified = data['VERIFIED_DURABLE.json']
    final = data['FINAL_VERIFICATION.json']
    terminal = checkpoint['terminal']
    state = deepcopy(checkpoint)
    checksum = state.pop('checksum')
    require(digest(state) == checksum, 'RECOVERY_CHECKPOINT_CHECKSUM')
    require(receipt['job_id'] == checkpoint['job_id'] == 17 and receipt['run_id'] == 18 and
            checkpoint['source'] == 'beka_mak' and receipt['batch_id'] == value['batch_id'],
            'RECOVERY_LINEAGE_CHANGED')
    require(checkpoint['manifest_sha256'] == digest(input_value) and
            digest(input_value) in value['input_hashes'], 'RECOVERY_MANIFEST_CHANGED')
    require(not checkpoint['frontier'] and not checkpoint['inflight'] and
            len(terminal) == 109 and all(r['state'] == 'COMMITTED' and
                r['receipt']['state'] == 'COMMITTED' for r in terminal.values()),
            'RECOVERY_NOT_109_COMMITTED')
    ids = [r['receipt']['external_id'] for r in terminal.values()]
    require(len(set(ids)) == 109 and sorted(ids) == sorted(receipt['external_ids']) and
            set(ids) == {e['source_external_id'] for e in input_value['entries']} and
            set(terminal) == {e['url'] for e in input_value['entries']}, 'RECOVERY_SCOPE_CHANGED')
    require(sorted(r[0] for r in verified['current_matches']) ==
            sorted(e['source_product_id'] for e in input_value['entries']), 'RECOVERY_SOURCE_PK_CHANGED')
    result = receipt['result']
    require(result['status'] == 'succeeded' and result['total'] == 109 and result['apply_allowed'] and
            result['existing_ids_preserved'] and result['f01_f03_enforced'] and
            not result['review_conflict_auto_accept'] and
            result['by_source'] == {'beka_mak': {'AUTO':14,'REVIEW':21,'CONFLICT':48,'NEW':26,'NOT_PROCESSED':0}},
            'RECOVERY_MATCHING_NOT_PROVEN')
    require(verified['mappings1282_exact'] and verified['protected_table_hashes_exact'] and
            verified['checkpoint_unchanged'] and final['native_checkpoint_sql_reconciliation_pass'] and
            final['manual_decisions_rejections_and_mapping_hashes_exact'], 'RECOVERY_VERIFICATION_MISSING')
    require(len(data['BEFORE.json']['mappings']) == 1282 and
            data['BEFORE.json']['mappings'] == verified['after']['mappings'], 'RECOVERY_MAPPINGS_CHANGED')
    return result


def validate_matching(cursor, snapshot, parent_id=17, run_id=18, config=None, lock_parent=True):
    """Fresh SQL readback under the queue's admission lock, and again in worker."""
    from .source_runtime import job_dir, read_input
    from .source_batch import validate_snapshot
    value, sha = approval()
    data = read_artifacts(value)
    require(parent_id == 17 and run_id == 18, 'RECOVERY_PARENT_NOT_APPROVED')
    frozen = next(r for r in value['parents'] if r['id'] == parent_id)
    cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s' +
                   (' FOR SHARE' if lock_parent else ''), (parent_id,))
    row=cursor.fetchone()
    require(row and row[:2] == ('partial',18) and row[2] == frozen['snapshot'], 'RECOVERY_PARENT_CHANGED')
    require(snapshot == frozen['snapshot'], 'RECOVERY_PARENT_SCOPE_CHANGED')
    validate_snapshot(snapshot)
    path = job_dir('beka_mak', parent_id) / 'checkpoint.json'
    require(file_sha(path) == data['DURABLE_MATCHING_RESULT.json']['checkpoint_sha256'],
            'RECOVERY_CHECKPOINT_CHANGED')
    cp = json.loads(path.read_text(encoding='utf-8'))
    original = read_input('beka_mak', parent_id, cp['manifest_sha256'])
    result = validate_saved(value, data, cp, original)
    verify_sql(cursor,data,cp,original,config)
    return {'kind':'matching-only', 'parent_job_id':17, 'run_id':18,
            'approval_sha256':sha, 'input_sha256':cp['manifest_sha256'],
            'result':result, 'external_ids':sorted(data['DURABLE_MATCHING_RESULT.json']['external_ids'])}


def admission_runtime(runtime,proof):
    require(runtime.get('owner_job_id')==17 and runtime.get('input_sha256')==proof['input_sha256'] and
            runtime.get('status')=='partial' and runtime.get('sql_committed')==109 and
            runtime.get('remaining')==0 and not runtime.get('resume_uncertain'),
            'RECOVERY_RUNTIME_OWNER_OR_RECEIPTS_CHANGED')


def verify_sql(cursor,data,cp,original,config):
    """Read-only contract, usable by preparation without activating approval."""
    from .source_canary import validate_config,TARGET
    validate_config(config)
    cursor.execute("""SELECT current_database(),system_identifier::text,current_setting('server_version'),
        current_setting('data_directory'),host(inet_server_addr()),inet_server_port(),
        current_user,rolsuper,rolbypassrls FROM pg_control_system(),pg_roles WHERE rolname=current_user""")
    target=cursor.fetchone()
    require(target and target[:3]==(TARGET['dbname'],TARGET['system_identifier'],'17.11') and
        Path(target[3]).resolve()==Path(TARGET['pgdata']).resolve() and
        target[4:]==(TARGET['host'],TARGET['port'],'rc_runtime',False,False),'RECOVERY_SQL_TARGET_OR_ROLE_CHANGED')
    parent_id=17
    fingerprints = [r['receipt']['capture_fingerprint'] for r in cp['terminal'].values()]
    cursor.execute('''SELECT count(DISTINCT c.id),count(DISTINCT o.id),count(DISTINCT c.source_product_id)
        FROM supplier_http_captures c JOIN offer_commercial_observations o
        ON o.capture_id=c.id AND o.source_product_id=c.source_product_id AND o.observed_at=c.observed_at
        JOIN suppliers s ON s.id=c.supplier_id
        WHERE s.code='beka_mak' AND c.capture_fingerprint=ANY(%s)''', (fingerprints,))
    require(cursor.fetchone() == (109,109,109), 'RECOVERY_SQL_RECEIPTS_CHANGED')
    cursor.execute('''SELECT sp.id,s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id
        FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
        WHERE sp.catalog_product_id IS NOT NULL ORDER BY sp.id,sb.sterbrust_product_id''')
    require(normal(cursor.fetchall()) == data['BEFORE.json']['mappings'], 'RECOVERY_SQL_MAPPINGS_CHANGED')
    ids = [r[0] for r in data['VERIFIED_DURABLE.json']['current_matches']]
    cursor.execute('''SELECT source_product_id,status,auto_accepted,match_method,id,catalog_product_id
        FROM product_matches WHERE source_product_id=ANY(%s) AND is_current ORDER BY source_product_id''', (ids,))
    require(normal(cursor.fetchall()) == data['VERIFIED_DURABLE.json']['current_matches'], 'RECOVERY_SQL_MATCHES_CHANGED')
    cursor.execute('''SELECT source_product_id,decision,best_sterbrust_id,rule_version
        FROM product_identity_decisions WHERE source_product_id=ANY(%s) ORDER BY source_product_id''', (ids,))
    require(normal(cursor.fetchall()) == data['VERIFIED_DURABLE.json']['identity_decisions'], 'RECOVERY_SQL_DECISIONS_CHANGED')
    cursor.execute('''SELECT source_product_id,lifecycle_status,automatic_decision,source_identity_fingerprint
        FROM review_cases WHERE source_product_id=ANY(%s) ORDER BY source_product_id''', (ids,))
    require(normal(cursor.fetchall()) == data['VERIFIED_DURABLE.json']['review_cases'], 'RECOVERY_SQL_REVIEW_CHANGED')
    # A new manual decision/rejection or changed source identity makes an old
    # recovery receipt stale even if its current bucket happens to be the same.
    for table in ('review_decisions','candidate_rejections','source_products','review_cases'):
        cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
            ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t''')
        require(normal(cursor.fetchone()) == data['VERIFIED_DURABLE.json']['after']['hashes'][table],
                'RECOVERY_SQL_IDENTITY_OR_MANUAL_CHANGED')
    # Existing probe verifies every original capture/observation and pending plan.
    from .source_controls import verify_resume
    require(config is not None, 'RECOVERY_CONFIG_REQUIRED')
    verify_resume('beka_mak', parent_id, cp['manifest_sha256'], original, config)


def runtime_proof(job, config):
    proof = (job.settings_snapshot.get('source_batch') or {}).get('recovery')
    if not proof:
        return None
    require(proof['kind'] == 'matching-only', 'RECOVERY_CHILD_KIND')
    value, sha = approval()
    require(proof['approval_sha256'] == sha, 'RECOVERY_CHILD_APPROVAL_CHANGED')
    settings=job.settings_snapshot['crawl_settings']
    require(settings.get('collection_mode')=='RESUME' and settings.get('resume_owner_job_id')==17 and
            settings.get('resume_input_sha256')==digest(value['parents'][0]['snapshot']['source_input']),
            'RECOVERY_CHILD_OWNER_CHANGED')
    from .queue import PostgresJobQueue
    from .source_canary import validate_config, validate_connection
    validate_config(config)
    with PostgresJobQueue(config)._connect() as connection, connection.cursor() as cursor:
        connection.read_only = True
        validate_connection(connection)
        cursor.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(cursor.fetchone() == ('rc_runtime',False,False), 'RECOVERY_ROLE')
        cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s', (17,))
        parent = cursor.fetchone()
        require(parent and parent[:2] == ('partial',18), 'RECOVERY_PARENT_CHANGED')
        return validate_matching(cursor, parent[2], config=config, lock_parent=False)


def retry_cancelled(queue):
    """One atomic existing-queue insertion set; old cancelled evidence untouched."""
    from . import source_batch as batch
    value, sha = approval()
    with queue._connect() as connection, connection.transaction(), connection.cursor() as cursor:
        batch.validate_config(queue.config); batch.validate_connection(connection)
        cursor.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(cursor.fetchone() == ('rc_runtime',False,False), 'RECOVERY_ROLE')
        cursor.execute('SELECT pg_advisory_xact_lock(%s)', (8611011,))
        jobs=[]
        for expected in value['parents'][1:]:
            cursor.execute('SELECT status,crawl_run_id,settings_snapshot,supplier_id FROM crawl_jobs WHERE id=%s FOR UPDATE', (expected['id'],))
            row=cursor.fetchone()
            require(row and row[:2] == ('cancelled',None) and row[2] == expected['snapshot'] and
                    row[3] == expected['supplier_id'], 'RECOVERY_CANCELLED_ROOT_CHANGED')
            batch.validate_snapshot(row[2])
            cursor.execute('SELECT enabled,code,adapter_name,base_url FROM suppliers WHERE id=%s FOR UPDATE', (row[3],))
            enabled,code,adapter,base=cursor.fetchone()
            require(enabled and code == adapter == row[2]['supplier_code'] and
                    batch.normalized_domain(base) == row[2]['domain'], 'RECOVERY_SOURCE_NOT_ENABLED_OR_CHANGED')
            cursor.execute('''SELECT id,settings_snapshot FROM crawl_jobs WHERE supplier_id=%s
                AND settings_snapshot->'source_batch'->>'id'=%s
                AND settings_snapshot->'source_batch'->>'phase'='REFRESH' ORDER BY id DESC''', (row[3],value['batch_id']))
            attempts=cursor.fetchall()
            if len(attempts)>1:
                require(len(attempts)==2 and attempts[0][1]['source_batch'].get('retry_parent_id') == expected['id'], 'RECOVERY_RETRY_ALREADY_CHANGED')
                jobs.append(attempts[0][0]);continue
            cursor.execute("SELECT id FROM crawl_jobs WHERE supplier_id=%s AND status IN ('queued','running')",(row[3],))
            require(not cursor.fetchone(), 'BATCH_SOURCE_ALREADY_ACTIVE')
            snapshot=deepcopy(row[2])
            snapshot['source_batch'].update(attempt=2,retry_parent_id=expected['id'],recovery_approval_sha256=sha)
            batch.validate_snapshot(snapshot)
            # Policy activation remains an explicit prior operator prerequisite.
            from .source_runtime import checked_input
            checked_input(snapshot['source_input'],code)
            checked_input(snapshot['source_batch']['next_snapshot']['source_input'],code)
            jobs.append(batch.insert(cursor,row[3],snapshot))
        return jobs


def recover(queue):
    """Existing RESUME first, then idempotent cancelled-root retry; no worker start."""
    value, sha = approval()
    expected=value['parents'][0]
    require(expected['id']==17, 'RECOVERY_FIRST_PARENT')
    with queue._connect() as connection, connection.cursor() as cursor:
        connection.read_only=True
        cursor.execute('''SELECT id,settings_snapshot FROM crawl_jobs
            WHERE settings_snapshot->'source_batch'->>'id'=%s
            AND settings_snapshot->'source_batch'->'recovery'->>'parent_job_id'='17' ''',(value['batch_id'],))
        children=cursor.fetchall()
    if children:
        require(len(children)==1 and children[0][1]['source_batch']['recovery']['approval_sha256']==sha,
                'RECOVERY_CHILD_ALREADY_CHANGED')
        child_id=children[0][0]
    else:
        child=queue.enqueue(expected['supplier_id'],trigger_type='manual',crawl_type='full',
            source_mode='RESUME',resume_job_id=17,recovery_sha256=sha)
        child_id=child.job_id
    return {'batch_id':value['batch_id'],'technical_child':child_id,
            'retried_roots':retry_cancelled(queue),'worker_started':False,
            'note':'Old partial/cancelled jobs retained; native completion advances Beka to frozen DISCOVERY.'}
