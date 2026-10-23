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
    'universal_supplier/control_plane/source_controls.py',
    'universal_supplier/control_plane/source_resume.py',
    'universal_supplier/control_plane/source_backend.py',
    'universal_supplier/adapters/bekamak_official.py',
}
REVIEWED_PARSER_SHA = 'bba4ffdea6591488f0873243b5106426056ee821d8edd8741f28dd2c7f09d4f4'
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
    if 'universal_supplier/adapters/bekamak_official.py' in changed:
        require(file_sha(root/'universal_supplier/adapters/bekamak_official.py')==REVIEWED_PARSER_SHA,
                'RECOVERY_UNREVIEWED_PARSER_CHANGE')
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
    for item in value.get('matching_recoveries', {}).values():
        for key in ('receipt', 'verified', 'before'):
            artifact = item[key]
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
    if value.get('continuation'):
        continuation_envelope_check(value,envelope)
    elif envelope.get('attempt')==4:
        extra_attempt_envelope(value,envelope)
    else:
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


def validate_matching(cursor, snapshot, parent_id=17, run_id=None, config=None, lock_parent=True):
    """Fresh SQL readback under the queue's admission lock, and again in worker."""
    from .source_runtime import job_dir, read_input
    from .source_batch import validate_snapshot
    value, sha = approval()
    if value.get('continuation'):
        return validate_continuation(cursor,snapshot,parent_id,config,lock_parent,value,sha)
    if str(parent_id) in value.get('post_stop',{}).get('parents',{}):
        return validate_post_stop(cursor,snapshot,parent_id,config,lock_parent,value,sha)
    if parent_id != 17:
        return validate_discovery_matching(cursor, snapshot, parent_id, run_id, config, lock_parent, value, sha)
    run_id = 18 if run_id is None else run_id
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
    if proof.get('kind') in ('post-stop','continuation'):
        require(digest(runtime)==proof['runtime_sha256'] and
                set(runtime.get('resume_uncertain') or [])<=set(proof.get('allowed_cached_uncertain',[])),
                'RECOVERY_RUNTIME_OWNER_OR_RECEIPTS_CHANGED')
        return
    require(runtime.get('owner_job_id')==proof.get('parent_job_id') and runtime.get('input_sha256')==proof['input_sha256'] and
            runtime.get('status')=='partial' and runtime.get('sql_committed')==proof.get('result',{}).get('total') and
            runtime.get('remaining')==0 and not runtime.get('resume_uncertain'),
            'RECOVERY_RUNTIME_OWNER_OR_RECEIPTS_CHANGED')


def validate_discovery_saved(item, receipt, verified, before, cp, node, original, snapshot):
    """Pure proof check; a finished traversal and committed products are required."""
    parent=item['parent_job_id']; run=item['run_id']; code=snapshot['supplier_code']
    require(parent==39 and run==20 and code=='beka_mak', 'RECOVERY_PARENT_NOT_APPROVED')
    for state in (cp,node):
        unsigned=deepcopy(state); checksum=unsigned.pop('checksum')
        require(digest(unsigned)==checksum and state['job_id']==parent and state['source']==code and
                state['manifest_sha256']==digest(original), 'RECOVERY_CHECKPOINT_CHECKSUM')
        require(not state['frontier'] and not state['inflight'], 'RECOVERY_UNFINISHED_OR_UNCERTAIN')
    require(original['mode']=='DISCOVERY' and node['discovery_finished'] and not node['category_failures'],
            'RECOVERY_DISCOVERY_NOT_FINISHED')
    urls={p['url'] for r in node['discovery_receipts'].values() for p in r.get('products',[])}
    terminal=cp['terminal']; ids=[r['receipt']['external_id'] for r in terminal.values()]
    require(set(terminal)==urls and len(ids)==len(set(ids))==104 and
            all(r['state']==r['receipt']['state']=='COMMITTED' for r in terminal.values()),
            'RECOVERY_NOT_104_COMMITTED')
    require(digest(original)==digest(snapshot['source_input']) and receipt['committed'] and receipt['job_id']==parent and receipt['run_id']==run and
            receipt['batch_id']==snapshot['source_batch']['id'] and receipt['input_sha256']==digest(original) and
            sorted(receipt['external_ids'])==sorted(ids), 'RECOVERY_RECEIPT_LINEAGE_CHANGED')
    result=receipt['result']
    require(result['status']=='succeeded' and result['total']==104 and result['apply_allowed'] and
            result['existing_ids_preserved'] and result['f01_f03_enforced'] and
            not result['review_conflict_auto_accept'] and result['by_source']=={'beka_mak':{**verified['buckets'],'NOT_PROCESSED':0}} and
            sum(verified['buckets'].values())==104 and verified['buckets'].get('NOT_PROCESSED',0)==0,
            'RECOVERY_MATCHING_NOT_PROVEN')
    require(verified['mappings1282_exact'] and verified['protected_tables_exact'] and verified['checkpoint_unchanged'] and
            verified['http']==verified['ingest']==0 and len(before['state']['mappings'])==1282 and
            before['state']['mappings']==verified['after']['mappings'] and
            len(verified['current_matches'])==len({r[0] for r in verified['current_matches']})==104,
            'RECOVERY_VERIFICATION_MISSING')
    return result


def validate_discovery_matching(cursor,snapshot,parent_id,run_id,config,lock_parent,value,sha):
    from . import source_batch as batch
    from .source_runtime import job_dir,read_input,evidence_root
    from .source_controls import verify_resume
    item=value.get('matching_recoveries',{}).get(str(parent_id))
    require(item and item['parent_job_id']==parent_id and (run_id is None or item['run_id']==run_id),
            'RECOVERY_PARENT_NOT_APPROVED')
    require(snapshot==item['snapshot'], 'RECOVERY_PARENT_SCOPE_CHANGED')
    batch.validate_snapshot(snapshot)
    cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s'+
                   (' FOR SHARE' if lock_parent else ''),(parent_id,))
    require(cursor.fetchone()==('partial',item['run_id'],snapshot), 'RECOVERY_PARENT_CHANGED')
    data={k:json.loads(Path(item[k]['path']).read_text(encoding='utf-8')) for k in ('receipt','verified','before')}
    cp_path=job_dir('beka_mak',parent_id)/'checkpoint.json'
    node_path=evidence_root()/'discovery/beka_mak'/('job-'+str(parent_id))/'checkpoint.json'
    require(file_sha(cp_path)==data['receipt']['checkpoint_sha256'] and
            file_sha(node_path)==data['receipt']['discovery_checkpoint_sha256'], 'RECOVERY_CHECKPOINT_CHANGED')
    cp=json.loads(cp_path.read_text(encoding='utf-8'));node=json.loads(node_path.read_text(encoding='utf-8'))
    refresh_path=job_dir('beka_mak',17)/'checkpoint.json'
    refresh_sha=next((h for p,h in data['before']['checkpoints'].items()
        if p.replace('\\','/')=='reports/source-runner-evidence/beka_mak/job-17/checkpoint.json'),None)
    require(refresh_sha and file_sha(refresh_path)==refresh_sha,
            'RECOVERY_REFRESH_CHECKPOINT_CHANGED')
    original=read_input('beka_mak',parent_id,cp['manifest_sha256'])
    require(digest(original) in value['input_hashes'], 'RECOVERY_INPUT_NOT_APPROVED')
    result=validate_discovery_saved(item,data['receipt'],data['verified'],data['before'],cp,node,original,snapshot)
    # Same exact target/role guards as normal queue runtime. No writes here.
    batch.validate_config(config)
    cursor.execute("""SELECT current_database(),system_identifier::text,current_setting('server_version'),
        current_setting('data_directory'),host(inet_server_addr()),inet_server_port(),
        current_user,rolsuper,rolbypassrls FROM pg_control_system(),pg_roles WHERE rolname=current_user""")
    target=cursor.fetchone(); t=batch.TARGET
    require(target and target[:3]==(t['dbname'],t['system_identifier'],'17.11') and
        Path(target[3]).resolve()==Path(t['pgdata']).resolve() and
        target[4:]==(t['host'],t['port'],'rc_runtime',False,False),'RECOVERY_SQL_TARGET_OR_ROLE_CHANGED')
    fingerprints=[r['receipt']['capture_fingerprint'] for r in cp['terminal'].values()]
    cursor.execute('''SELECT count(DISTINCT c.id),count(DISTINCT o.id),count(DISTINCT c.source_product_id)
        FROM supplier_http_captures c JOIN offer_commercial_observations o ON o.capture_id=c.id
        AND o.source_product_id=c.source_product_id AND o.observed_at=c.observed_at
        JOIN suppliers s ON s.id=c.supplier_id WHERE s.code='beka_mak' AND c.capture_fingerprint=ANY(%s)''',(fingerprints,))
    require(cursor.fetchone()==(104,104,104),'RECOVERY_SQL_RECEIPTS_CHANGED')
    cursor.execute('''SELECT sp.id,s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id
        FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
        WHERE sp.catalog_product_id IS NOT NULL ORDER BY sp.id,sb.sterbrust_product_id''')
    verified=data['verified']
    require(normal(cursor.fetchall())==verified['after']['mappings'],'RECOVERY_SQL_MAPPINGS_CHANGED')
    ids=[r[0] for r in verified['current_matches']]
    for query,key in [
        ('SELECT source_product_id,status,auto_accepted,match_method,id,catalog_product_id FROM product_matches WHERE source_product_id=ANY(%s) AND is_current ORDER BY source_product_id','current_matches'),
        ('SELECT source_product_id,decision,best_sterbrust_id,rule_version FROM product_identity_decisions WHERE source_product_id=ANY(%s) ORDER BY source_product_id','identity_decisions'),
        ('SELECT source_product_id,lifecycle_status,automatic_decision,source_identity_fingerprint FROM review_cases WHERE source_product_id=ANY(%s) ORDER BY source_product_id','review_cases')]:
        cursor.execute(query,(ids,))
        require(normal(cursor.fetchall())==verified[key],'RECOVERY_SQL_'+key.upper()+'_CHANGED')
    for table in ('source_products','review_cases','review_decisions','candidate_rejections'):
        cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
            ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t''')
        require(normal(cursor.fetchone())==verified['after']['hashes'][table],'RECOVERY_SQL_IDENTITY_OR_MANUAL_CHANGED')
    verify_resume('beka_mak',parent_id,cp['manifest_sha256'],original,config)
    refresh=json.loads(refresh_path.read_text(encoding='utf-8'))
    refresh_input=read_input('beka_mak',17,refresh['manifest_sha256'])
    verify_resume('beka_mak',17,refresh['manifest_sha256'],refresh_input,config)
    return {'kind':'matching-only','parent_job_id':parent_id,'run_id':item['run_id'],'approval_sha256':sha,
            'input_sha256':cp['manifest_sha256'],'result':result,'external_ids':sorted(data['receipt']['external_ids'])}


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
    require(proof['kind'] in ('matching-only','post-stop','continuation'), 'RECOVERY_CHILD_KIND')
    value, sha = approval()
    require(proof['approval_sha256'] == sha, 'RECOVERY_CHILD_APPROVAL_CHANGED')
    settings=job.settings_snapshot['crawl_settings']
    owner=proof['parent_job_id']
    require(settings.get('collection_mode')=='RESUME' and (proof['kind']=='continuation' or settings.get('resume_owner_job_id')==owner),
            'RECOVERY_CHILD_OWNER_CHANGED')
    from .queue import PostgresJobQueue
    from .source_canary import validate_config, validate_connection
    validate_config(config)
    with PostgresJobQueue(config)._connect() as connection, connection.cursor() as cursor:
        connection.read_only = True
        validate_connection(connection)
        cursor.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
        require(cursor.fetchone() == ('rc_runtime',False,False), 'RECOVERY_ROLE')
        cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s', (owner,))
        parent = cursor.fetchone()
        require(parent and parent[:2] == ('partial',proof['run_id']), 'RECOVERY_PARENT_CHANGED')
        require(settings.get('resume_input_sha256')==digest(parent[2]['source_input']), 'RECOVERY_CHILD_OWNER_CHANGED')
        result=validate_matching(cursor, parent[2], owner, proof['run_id'], config=config, lock_parent=False)
        if proof['kind']=='continuation':require(settings.get('resume_owner_job_id')==result['owner_job_id'],'RECOVERY_CHILD_OWNER_CHANGED')
        return result


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
            if value.get('matching_recoveries'):
                snapshot=retry_stopped_snapshot(value,expected,attempts,sha)
                if snapshot is None:
                    cursor.execute('SELECT status FROM crawl_jobs WHERE id=%s FOR UPDATE',(attempts[0][0],))
                    require(cursor.fetchone()[0] in ('queued','running','succeeded'),'RECOVERY_RETRY_REQUIRES_DECISION')
                    jobs.append(attempts[0][0]);continue
                approved=value['cancelled_attempts'][str(expected['id'])]
                cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s FOR UPDATE',(approved['id'],))
                require(cursor.fetchone()==('cancelled',None,approved['snapshot']),'RECOVERY_CANCELLED_STARTED_OR_CHANGED')
                cursor.execute("SELECT id FROM crawl_jobs WHERE supplier_id=%s AND status IN ('queued','running')",(row[3],))
                require(not cursor.fetchone(),'BATCH_SOURCE_ALREADY_ACTIVE')
                from .source_runtime import checked_input
                checked_input(snapshot['source_input'],code)
                checked_input(snapshot['source_batch']['next_snapshot']['source_input'],code)
                jobs.append(batch.insert(cursor,row[3],snapshot));continue
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


def retry_stopped_snapshot(value,expected,attempts,sha):
    """Pure attempt-3 plan; only approved never-started attempt-2 cancellations."""
    from . import source_batch as batch
    approved=value.get('cancelled_attempts',{}).get(str(expected['id']))
    require(approved is not None and len(attempts) in (2,3),'RECOVERY_CANCELLED_ATTEMPTS_CHANGED')
    require(attempts[-1]==(expected['id'],expected['snapshot']), 'RECOVERY_ROOT_SCOPE_CHANGED')
    second=attempts[-2]
    require(second[0]==approved['id'] and second[1]==approved['snapshot'] and
            second[1]['source_batch']['attempt']==2 and
            second[1]['source_batch']['retry_parent_id']==expected['id'] and
            batch.phase_scope(second[1])==batch.phase_scope(expected['snapshot']),
            'RECOVERY_CANCELLED_SCOPE_CHANGED')
    result=deepcopy(expected['snapshot'])
    result['source_batch'].update(attempt=3,retry_parent_id=second[0],retry_root_id=expected['id'],recovery_approval_sha256=sha)
    if len(attempts)==3:
        require(attempts[0][1]==result,'RECOVERY_RETRY_ALREADY_CHANGED')
        return None
    return result


def recover(queue):
    """Existing RESUME first, then idempotent cancelled-root retry; no worker start."""
    value, sha = approval()
    if value.get('continuation'):
        return recover_continuation(queue,value,sha)
    if value.get('post_stop'):
        return recover_post_stop(queue,value,sha)
    if value.get('matching_recoveries'):
        return recover_discovery(queue,value,sha)
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


def recover_discovery(queue,value,sha):
    """Continue the same batch; no edits to parent statuses or checkpoint flags."""
    require(set(value['matching_recoveries'])=={'39'},'RECOVERY_PARENT_NOT_APPROVED')
    item=value['matching_recoveries']['39']
    with queue._connect() as connection,connection.cursor() as cursor:
        connection.read_only=True
        cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=28')
        completed=cursor.fetchone()
        require(completed and completed[:2]==('succeeded',19) and
                completed[2]['source_batch']['id']==value['batch_id'] and
                completed[2]['source_batch']['recovery']['parent_job_id']==17,
                'RECOVERY_REFRESH_NOT_COMPLETED')
        for approved in value['cancelled_attempts'].values():
            cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s',(approved['id'],))
            require(cursor.fetchone()==('cancelled',None,approved['snapshot']), 'RECOVERY_CANCELLED_STARTED_OR_CHANGED')
        cursor.execute('''SELECT id,status,settings_snapshot FROM crawl_jobs
            WHERE settings_snapshot->'source_batch'->>'id'=%s
            AND settings_snapshot->'source_batch'->'recovery'->>'parent_job_id'='39' ORDER BY id''',(value['batch_id'],))
        children=cursor.fetchall()
    if children:
        require(len(children)==1 and children[0][1] in ('queued','running','succeeded') and
                children[0][2]['source_batch']['recovery']['approval_sha256']==sha,
                'RECOVERY_CHILD_ALREADY_CHANGED')
        child_id=children[0][0]
    else:
        child=queue.enqueue(item['supplier_id'],trigger_type='manual',crawl_type='full',source_mode='RESUME',
                            resume_job_id=39,recovery_sha256=sha)
        child_id=child.job_id
    return {'batch_id':value['batch_id'],'technical_child':child_id,'retried_roots':retry_cancelled(queue),
            'worker_started':False,'note':'DISCOVERY COMMITTED receipts reused; Beka isolation confirmation remains mandatory.'}


def post_stop_item(value,parent_id):
    extra=value.get('post_stop',{})
    require(extra.get('format')=='live11-post-stop/v1' and
            set(extra.get('parents',{}))=={str(i) for i in range(41,51)},'RECOVERY_POST_STOP_NOT_APPROVED')
    item=extra['parents'].get(str(parent_id))
    require(item and item['id']==parent_id and item['snapshot']['source_batch']['id']==value['batch_id'] and
            item['snapshot']['source_batch']['phase']=='REFRESH' and
            item['snapshot']['source_batch']['attempt']==3 and
            item['snapshot']['source_batch']['scope_sha256'] in value['scope_hashes'],
            'RECOVERY_POST_STOP_PARENT_CHANGED')
    require(set(item.get('source_hashes',{}))=={'source_products','offers','product_matches','product_identity_decisions','review_cases'} and
            set(extra.get('manual_hashes',{}))=={'review_decisions','candidate_rejections'},'RECOVERY_SQL_PROOF_INCOMPLETE')
    return item


def extra_attempt_envelope(value,envelope):
    marker=envelope.get('post_stop_recovery',{})
    item=post_stop_item(value,marker.get('parent_job_id'))
    require(marker=={'kind':'post-stop','parent_job_id':item['id'],'approval_sha256':os.environ.get(ENV+'_SHA256')} and
            envelope['scope_sha256']==item['snapshot']['source_batch']['scope_sha256'] and
            envelope['phase']==item['snapshot']['source_batch']['phase'], 'BATCH_RESUME_ATTEMPTS_EXHAUSTED')


def post_stop_envelope(previous,count,latest_attempt,proof):
    # Preserve the ordinary queue limit; the one explicit exception is signed
    # for these exact parent rows and cannot create a fifth attempt.
    require(proof and proof.get('kind')=='post-stop' and count==latest_attempt==3,
            'BATCH_RESUME_ATTEMPTS_EXHAUSTED')
    value,sha=approval();item=post_stop_item(value,proof['parent_job_id'])
    require(proof['approval_sha256']==sha and previous==item['snapshot'], 'RECOVERY_PARENT_SCOPE_CHANGED')
    envelope=deepcopy(previous['source_batch']);envelope['attempt']=4
    envelope['post_stop_recovery']={'kind':'post-stop','parent_job_id':item['id'],'approval_sha256':sha}
    return envelope


def post_stop_receipts(code,owner,input_sha,state):
    """An approved FAILED capture becomes an in-memory offline input only.

    The checkpoint is untouched. Only normal successful SQL persistence can
    record its COMMITTED successor; no failed URL re-enters the HTTP frontier.
    """
    value,_=approval()
    if value.get('continuation'):
        return continuation_receipts(value,code,owner,input_sha,state)
    if str(owner) not in value.get('post_stop',{}).get('parents',{}):return {}
    item=post_stop_item(value,owner)
    require(code==item['snapshot']['supplier_code'] and input_sha==digest(item['snapshot']['source_input']),
            'RECOVERY_CAPTURE_OWNER_CHANGED')
    overrides={}
    for url,saved in item.get('cached_failures',{}).items():
        row=state['terminal'].get(url)
        require(row is not None,'RECOVERY_CAPTURE_MISSING')
        if row['state']=='COMMITTED':continue
        require(owner==41 and code=='beka_mak_tr' and url=='https://www.bekamak.com/urun/613vt/en' and
                row==saved['failed_row'] and row['state']=='FAILED' and
                row['receipt']['reason']=='official title, name and SKU disagree', 'RECOVERY_FAILED_CAPTURE_CHANGED')
        capture=saved['captured_receipt']
        require(capture in row['history'] and capture['state']=='HTTP_CAPTURED_NOT_COMMITTED' and
                capture['url']==url and capture['origin']=={'source':code,'job_id':owner,'manifest_sha256':input_sha},
                'RECOVERY_CAPTURE_LINEAGE_CHANGED')
        metadata=capture['original_capture_metadata']
        require(metadata['http_status']==200 and metadata['requested_url']==metadata['final_url']==url and
                metadata['evidence_sha256']=='9958e465b14a55293260ac7c93f960095e460471dad6c2c74a4492b604ec2abc' and
                file_sha(metadata['evidence_ref'])==metadata['evidence_sha256'], 'RECOVERY_CAPTURE_HASH_CHANGED')
        overrides[url]=deepcopy(capture)
    entries={e['url']:e for e in item['snapshot']['source_input']['entries']}
    # HTTP may have finished before parsing supplied external_id. Resolve it
    # from the frozen REFRESH entry so SQL readback can detect a lost commit.
    for url,row in state['terminal'].items():
        capture=overrides.get(url,row['receipt'])
        if capture['state']=='HTTP_CAPTURED_NOT_COMMITTED':
            require(url in entries and entries[url].get('source_external_id'),'RECOVERY_CAPTURE_IDENTITY_MISSING')
            require(capture.get('external_id') in (None,entries[url]['source_external_id']),'RECOVERY_CAPTURE_IDENTITY_CHANGED')
            overrides[url]={**deepcopy(capture),'external_id':entries[url]['source_external_id']}
    return overrides


def validate_post_stop(cursor,snapshot,parent_id,config,lock_parent,value,sha):
    from . import source_batch as batch
    from .source_runtime import job_dir,read_input
    from .source_controls import verify_resume
    item=post_stop_item(value,parent_id)
    require(snapshot==item['snapshot'],'RECOVERY_PARENT_SCOPE_CHANGED')
    batch.validate_snapshot(snapshot);batch.validate_config(config)
    cursor.execute("""SELECT current_database(),system_identifier::text,current_setting('server_version'),
        current_setting('data_directory'),host(inet_server_addr()),inet_server_port(),
        current_user,rolsuper,rolbypassrls FROM pg_control_system(),pg_roles WHERE rolname=current_user""")
    target=cursor.fetchone();t=batch.TARGET
    require(target and target[:3]==(t['dbname'],t['system_identifier'],'17.11') and
        Path(target[3]).resolve()==Path(t['pgdata']).resolve() and
        target[4:]==(t['host'],t['port'],'rc_runtime',False,False),'RECOVERY_SQL_TARGET_OR_ROLE_CHANGED')
    cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s'+
                   (' FOR SHARE' if lock_parent else ''),(parent_id,))
    require(cursor.fetchone()==(item['status'],item['run_id'],snapshot),'RECOVERY_PARENT_CHANGED')
    # Independent hosts can progress concurrently; bind this supplier's rows,
    # not a global source hash that would reject legitimate other-host writes.
    for table,expected in item['source_hashes'].items():
        require(table in ('source_products','offers','product_matches','product_identity_decisions','review_cases'),
                'RECOVERY_HASH_TABLE_NOT_APPROVED')
        predicate='t.supplier_id=%s' if table in ('source_products','offers') else 'EXISTS(SELECT 1 FROM source_products sp WHERE sp.id=t.source_product_id AND sp.supplier_id=%s)'
        cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
            ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t WHERE {predicate}''',(item['supplier_id'],))
        require(normal(cursor.fetchone())==expected,'RECOVERY_SQL_SOURCE_CHANGED')
    for table,expected in value['post_stop']['manual_hashes'].items():
        require(table in ('review_decisions','candidate_rejections'),'RECOVERY_MANUAL_TABLE_NOT_APPROVED')
        cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
            ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t''')
        require(normal(cursor.fetchone())==expected,'RECOVERY_MANUAL_DECISIONS_CHANGED')
    mappings=value['post_stop']['mappings1282'];require(len(mappings)==1282,'RECOVERY_MAPPINGS_CHANGED')
    cursor.execute('''SELECT sp.id,s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id
        FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
        WHERE sp.id=ANY(%s) AND sp.catalog_product_id IS NOT NULL ORDER BY sp.id,sb.sterbrust_product_id''',
        (sorted({r[0] for r in mappings}),))
    require(normal(cursor.fetchall())==mappings,'RECOVERY_SQL_MAPPINGS_CHANGED')
    if item['run_id'] is None:
        require(item['status']=='cancelled' and not item.get('cached_failures'),'RECOVERY_CANCELLED_STARTED_OR_CHANGED')
        return None
    require(item['status']=='partial' and item['id'] in (41,42,43),'RECOVERY_PARENT_NOT_APPROVED')
    cursor.execute('SELECT metadata FROM crawl_runs WHERE id=%s',(item['run_id'],))
    runtime=(cursor.fetchone()[0] or {}).get('source_runtime',{})
    require(digest(runtime)==item['runtime_sha256'] and runtime['owner_job_id']==parent_id and
            set(runtime.get('resume_uncertain') or [])<=set(item.get('cached_failures',{})),
            'RECOVERY_RUNTIME_OWNER_OR_RECEIPTS_CHANGED')
    path=job_dir(snapshot['supplier_code'],parent_id)/'checkpoint.json'
    require(file_sha(path)==item['checkpoint_sha256'],'RECOVERY_CHECKPOINT_CHANGED')
    cp=json.loads(path.read_text(encoding='utf-8'));state=deepcopy(cp);checksum=state.pop('checksum')
    require(digest(state)==checksum and not cp['inflight'],'RECOVERY_CHECKPOINT_CHECKSUM')
    require(all(r['state'] in ('COMMITTED','HTTP_CAPTURED_NOT_COMMITTED') or u in item.get('cached_failures',{})
            for u,r in cp['terminal'].items()),'RECOVERY_UNAPPROVED_FAILED_OR_UNCERTAIN')
    original=read_input(snapshot['supplier_code'],parent_id,cp['manifest_sha256'])
    require(original==snapshot['source_input'],'RECOVERY_INPUT_NOT_APPROVED')
    verify_resume(snapshot['supplier_code'],parent_id,cp['manifest_sha256'],original,config)
    return {'kind':'post-stop','parent_job_id':parent_id,'run_id':item['run_id'],'approval_sha256':sha,
            'input_sha256':cp['manifest_sha256'],'runtime_sha256':item['runtime_sha256'],
            'allowed_cached_uncertain':sorted(item.get('cached_failures',{}))}


def recover_post_stop(queue,value,sha):
    from . import source_batch as batch
    from .source_runtime import checked_input
    resumed=[];retried=[]
    for parent_id in range(41,51):
        item=post_stop_item(value,parent_id)
        with queue._connect() as connection,connection.cursor() as q:
            connection.read_only=True
            q.execute('''SELECT id,status,settings_snapshot FROM crawl_jobs
                WHERE settings_snapshot->'source_batch'->>'id'=%s AND
                settings_snapshot->'source_batch'->'post_stop_recovery'->>'parent_job_id'=%s ORDER BY id''',
                (value['batch_id'],str(parent_id)))
            children=q.fetchall()
        if children:
            require(len(children)==1 and children[0][1] in ('queued','running','succeeded') and
                    children[0][2]['source_batch']['post_stop_recovery']['approval_sha256']==sha,
                    'RECOVERY_ONE_ADDITIONAL_ATTEMPT_ONLY')
            (resumed if item['run_id'] else retried).append(children[0][0]);continue
        if item['run_id'] is not None:
            child=queue.enqueue(item['supplier_id'],trigger_type='manual',crawl_type='full',source_mode='RESUME',
                                resume_job_id=parent_id,recovery_sha256=sha)
            resumed.append(child.job_id);continue
        with queue._connect() as connection,connection.transaction(),connection.cursor() as q:
            q.execute('SELECT enabled,code,adapter_name,base_url FROM suppliers WHERE id=%s FOR UPDATE',(item['supplier_id'],))
            enabled,code,adapter,base=q.fetchone()
            require(enabled and code==adapter==item['snapshot']['supplier_code'] and
                    batch.normalized_domain(base)==item['snapshot']['domain'],'RECOVERY_SOURCE_NOT_ENABLED_OR_CHANGED')
            q.execute('SELECT pg_advisory_xact_lock(%s)',(8611011,))
            validate_post_stop(q,item['snapshot'],parent_id,queue.config,True,value,sha)
            q.execute("SELECT id FROM crawl_jobs WHERE supplier_id=%s AND status IN ('queued','running')",(item['supplier_id'],))
            require(not q.fetchone(),'BATCH_SOURCE_ALREADY_ACTIVE')
            q.execute("""SELECT count(*),max((settings_snapshot->'source_batch'->>'attempt')::int) FROM crawl_jobs
                WHERE supplier_id=%s AND settings_snapshot->'source_batch'->>'id'=%s
                AND settings_snapshot->'source_batch'->>'phase'='REFRESH'""",(item['supplier_id'],value['batch_id']))
            require(q.fetchone()==(3,3),'RECOVERY_ONE_ADDITIONAL_ATTEMPT_ONLY')
            snapshot=deepcopy(item['snapshot'])
            snapshot['source_batch']=post_stop_envelope(item['snapshot'],3,3,{'kind':'post-stop','parent_job_id':parent_id,'approval_sha256':sha})
            batch.validate_snapshot(snapshot)
            checked_input(snapshot['source_input'],code);checked_input(snapshot['source_batch']['next_snapshot']['source_input'],code)
            retried.append(batch.insert(q,item['supplier_id'],snapshot))
    return {'batch_id':value['batch_id'],'resumed_partial':resumed,'retried_roots':retried,
            'worker_started':False,'one_additional_attempt_only':True}


def continuation_rule(value,scope):
    plan=value.get('continuation',{})
    require(plan.get('format')=='live11-continuation/v1' and plan.get('batch_id')==value['batch_id'],
            'RECOVERY_CONTINUATION_NOT_APPROVED')
    require(set(plan.get('phases',{}))==set(value['scope_hashes']) and len(plan['phases'])==22 and
            set(plan.get('manual_hashes',{}))=={'review_decisions','candidate_rejections'} and
            len(plan.get('mappings1282',[]))==1282,'RECOVERY_CONTINUATION_PROOF_INCOMPLETE')
    rule=plan.get('phases',{}).get(scope)
    require(rule and scope in value['scope_hashes'] and type(rule.get('max_attempt')) is int and
            1<=rule['max_attempt']<=8,'RECOVERY_CONTINUATION_BOUND')
    return rule


def continuation_envelope_check(value,envelope):
    rule=continuation_rule(value,envelope['scope_sha256'])
    if digest(envelope) in value['continuation']['historical_envelopes']:return
    if envelope.get('attempt',0)<=3 and not envelope.get('continuation_recovery'):
        require(type(envelope['attempt']) is int and envelope['attempt']>=1,'BATCH_RESUME_ATTEMPTS_EXHAUSTED')
        return
    marker=envelope.get('continuation_recovery',{})
    require(marker.get('approval_sha256')==os.environ.get(ENV+'_SHA256') and
            type(marker.get('parent_job_id')) is int and
            type(envelope['attempt']) is int and 1<=envelope['attempt']<=rule['max_attempt'],
            'BATCH_RESUME_ATTEMPTS_EXHAUSTED')


def continuation_envelope(previous,count,latest,proof):
    value,sha=approval();env=previous['source_batch'];rule=continuation_rule(value,env['scope_sha256'])
    require(proof and proof.get('kind')=='continuation' and proof['approval_sha256']==sha and
            latest==env['attempt'] and count==latest and count<rule['max_attempt'],
            'BATCH_RESUME_ATTEMPTS_EXHAUSTED')
    result=deepcopy(env)
    result.pop('post_stop_recovery',None);result.pop('recovery',None)
    result.update(attempt=count+1,continuation_recovery={'parent_job_id':proof['parent_job_id'],'approval_sha256':sha})
    return result


def continuation_receipts(value,code,owner,input_sha,state):
    """Only the exact operator-approved historic failure gets quarantined.

    This is an in-memory proposal. Native runtime appends it after SQL proves
    no ingest; old FAILED history and original immutable input remain intact.
    """
    overrides={}
    for item in value['continuation'].get('quarantine',[]):
        if (item['source'],item['owner_job_id'],item['input_sha256'])!=(code,owner,input_sha):continue
        row=state['terminal'].get(item['url'])
        if row and row['state']=='QUARANTINED':continue
        require(row==item['failed_row'] and row['state']=='FAILED','RECOVERY_QUARANTINE_EVIDENCE_CHANGED')
        capture=item['captured_receipt'];meta=capture['original_capture_metadata']
        require(capture in row['history'] and capture['state']=='HTTP_CAPTURED_NOT_COMMITTED' and
                capture['origin']=={'source':code,'job_id':owner,'manifest_sha256':input_sha} and
                meta['http_status']==200 and meta['requested_url']==item['url'] and
                file_sha(meta['evidence_ref'])==meta['evidence_sha256'],'RECOVERY_QUARANTINE_CAPTURE_CHANGED')
        proposed=deepcopy(capture);proposed['state']='QUARANTINED'
        proposed['isolation']={'format':'source-url-isolation/v1','before_ingest':True,'category':'identity',
            'reason':row['receipt']['reason'],'operator_authorized':True}
        overrides[item['url']]=proposed
    return overrides


def continuation_invariants(cursor,value,config):
    from . import source_batch as batch
    batch.validate_config(config)
    cursor.execute("""SELECT current_database(),system_identifier::text,current_setting('server_version'),
        current_setting('data_directory'),host(inet_server_addr()),inet_server_port(),
        current_user,rolsuper,rolbypassrls FROM pg_control_system(),pg_roles WHERE rolname=current_user""")
    target=cursor.fetchone();t=batch.TARGET
    require(target and target[:3]==(t['dbname'],t['system_identifier'],'17.11') and
            Path(target[3]).resolve()==Path(t['pgdata']).resolve() and
            target[4:]==(t['host'],t['port'],'rc_runtime',False,False),'RECOVERY_SQL_TARGET_OR_ROLE_CHANGED')
    for table,expected in value['continuation']['manual_hashes'].items():
        require(table in ('review_decisions','candidate_rejections'),'RECOVERY_MANUAL_TABLE_NOT_APPROVED')
        cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
            ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t''')
        require(normal(cursor.fetchone())==expected,'RECOVERY_MANUAL_DECISIONS_CHANGED')
    mappings=value['continuation']['mappings1282'];require(len(mappings)==1282,'RECOVERY_MAPPINGS_CHANGED')
    cursor.execute('''SELECT sp.id,s.code,sp.external_id,sp.catalog_product_id,sb.sterbrust_product_id
        FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
        LEFT JOIN sterbrust_products sb ON sb.catalog_product_id=sp.catalog_product_id
        WHERE sp.id=ANY(%s) AND sp.catalog_product_id IS NOT NULL ORDER BY sp.id,sb.sterbrust_product_id''',
        (sorted({r[0] for r in mappings}),))
    require(normal(cursor.fetchall())==mappings,'RECOVERY_SQL_MAPPINGS_CHANGED')


def validate_continuation(cursor,snapshot,parent_id,config,lock_parent,value,sha):
    from . import source_batch as batch
    from .source_runtime import job_dir,read_input
    from .source_controls import verify_resume
    batch.validate_snapshot(snapshot);rule=continuation_rule(value,snapshot['source_batch']['scope_sha256'])
    continuation_invariants(cursor,value,config)
    cursor.execute('SELECT status,crawl_run_id,settings_snapshot FROM crawl_jobs WHERE id=%s'+
                   (' FOR SHARE' if lock_parent else ''),(parent_id,))
    row=cursor.fetchone()
    require(row and row[2]==snapshot and row[0] in ('partial','cancelled'),'RECOVERY_PARENT_CHANGED')
    initial=rule.get('initial_parent')
    if initial and initial['id']==parent_id:
        require((row[0],row[1])==(initial['status'],initial['run_id']),'RECOVERY_PARENT_CHANGED')
        require(set(initial.get('source_hashes',{}))=={'source_products','offers','product_matches','product_identity_decisions','review_cases'},
                'RECOVERY_SQL_PROOF_INCOMPLETE')
        # Before the first continuation, reject concurrent source edits.
        for table,expected in initial['source_hashes'].items():
            require(table in ('source_products','offers','product_matches','product_identity_decisions','review_cases'),'RECOVERY_HASH_TABLE_NOT_APPROVED')
            predicate='t.supplier_id=%s' if table in ('source_products','offers') else 'EXISTS(SELECT 1 FROM source_products sp WHERE sp.id=t.source_product_id AND sp.supplier_id=%s)'
            cursor.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
                ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t WHERE {predicate}''',(initial['supplier_id'],))
            require(normal(cursor.fetchone())==expected,'RECOVERY_SQL_SOURCE_CHANGED')
    if row[1] is None:
        require(initial and initial['id']==parent_id and row[0]=='cancelled','RECOVERY_CANCELLED_NOT_APPROVED')
        return {'kind':'continuation','parent_job_id':parent_id,'run_id':None,'approval_sha256':sha}
    require(row[0]=='partial','RECOVERY_PARENT_CHANGED')
    cursor.execute('SELECT metadata FROM crawl_runs WHERE id=%s',(row[1],))
    runtime=(cursor.fetchone()[0] or {}).get('source_runtime',{})
    owner=runtime.get('owner_job_id');input_sha=digest(snapshot['source_input'])
    require(type(owner) is int and runtime.get('input_sha256')==input_sha,'RECOVERY_RUNTIME_OWNER_OR_RECEIPTS_CHANGED')
    path=job_dir(snapshot['supplier_code'],owner)/'checkpoint.json'
    cp=json.loads(path.read_text(encoding='utf-8'));state=deepcopy(cp);checksum=state.pop('checksum')
    require(digest(state)==checksum and not cp['inflight'],'RECOVERY_CHECKPOINT_CHECKSUM')
    allowed={i['url'] for i in value['continuation']['quarantine'] if i['owner_job_id']==owner}
    require(set(runtime.get('resume_uncertain') or [])<=allowed,'RECOVERY_UNAPPROVED_FAILED_OR_UNCERTAIN')
    if initial and initial['id']==parent_id:
        require(file_sha(path)==initial['checkpoint_sha256'] and digest(runtime)==initial['runtime_sha256'],
                'RECOVERY_CHECKPOINT_CHANGED')
    original=read_input(snapshot['supplier_code'],owner,input_sha)
    require(original==snapshot['source_input'],'RECOVERY_INPUT_NOT_APPROVED')
    verify_resume(snapshot['supplier_code'],owner,input_sha,original,config)
    return {'kind':'continuation','parent_job_id':parent_id,'owner_job_id':owner,'run_id':row[1],
            'approval_sha256':sha,'input_sha256':input_sha,'runtime_sha256':digest(runtime),
            'allowed_cached_uncertain':sorted(allowed)}


def recover_continuation(queue,value,sha):
    """Existing enqueue/RESUME only, signed per-phase ceiling, no new scheduler."""
    from . import source_batch as batch
    resumed=[];retried=[]
    with queue._connect() as c,c.cursor() as q:
        c.read_only=True
        q.execute('''SELECT DISTINCT ON (supplier_id,settings_snapshot->'source_batch'->>'phase')
            id,supplier_id,status,crawl_run_id,settings_snapshot FROM crawl_jobs
            WHERE settings_snapshot->'source_batch'->>'id'=%s ORDER BY supplier_id,
            settings_snapshot->'source_batch'->>'phase',id DESC''',(value['batch_id'],))
        latest=q.fetchall()
    latest.sort(key=lambda row:(batch.SOURCE_ORDER.index(row[4]['supplier_code']),row[4]['source_batch']['phase']))
    for jid,sid,status,run,snapshot in latest:
        if status in ('queued','running','succeeded'):continue
        require(status in ('partial','cancelled'),'RECOVERY_SYSTEMIC_FAILURE_REQUIRES_OPERATOR')
        if run is not None:
            # A later partial may only auto-continue a proven budget boundary.
            rule=continuation_rule(value,snapshot['source_batch']['scope_sha256'])
            if jid!=(rule.get('initial_parent') or {}).get('id'):
                with queue._connect() as c,c.cursor() as q:
                    c.read_only=True;q.execute('SELECT metadata FROM crawl_runs WHERE id=%s',(run,))
                    rt=q.fetchone()[0].get('source_runtime',{})
                require(rt.get('resume_available') is True and rt.get('termination_reason') in
                    ('MAX_RUN_DURATION_EXCEEDED','DISCOVERY_PAGE_BUDGET','PRODUCT_BATCH_BUDGET'),
                    'RECOVERY_SYSTEMIC_FAILURE_REQUIRES_OPERATOR')
            result=queue.enqueue(sid,trigger_type='manual',crawl_type='full',source_mode='RESUME',resume_job_id=jid,recovery_sha256=sha)
            resumed.append(result.job_id);continue
        with queue._connect() as c,c.transaction(),c.cursor() as q:
            q.execute('SELECT enabled,code,adapter_name,base_url FROM suppliers WHERE id=%s FOR UPDATE',(sid,))
            enabled,code,adapter,base=q.fetchone()
            require(enabled and code==adapter==snapshot['supplier_code'] and batch.normalized_domain(base)==snapshot['domain'],
                    'RECOVERY_SOURCE_NOT_ENABLED_OR_CHANGED')
            q.execute('SELECT pg_advisory_xact_lock(%s)',(8611011,))
            proof=validate_continuation(q,snapshot,jid,queue.config,True,value,sha)
            q.execute("SELECT id FROM crawl_jobs WHERE supplier_id=%s AND status IN ('queued','running')",(sid,))
            if q.fetchone():continue
            q.execute("""SELECT count(*),bool_or(status='succeeded'),max((settings_snapshot->'source_batch'->>'attempt')::int)
                FROM crawl_jobs WHERE supplier_id=%s AND settings_snapshot->'source_batch'->>'id'=%s
                AND settings_snapshot->'source_batch'->>'phase'=%s""",(sid,value['batch_id'],snapshot['source_batch']['phase']))
            count,done,attempt=q.fetchone();require(not done,'RECOVERY_PHASE_ALREADY_COMPLETED')
            new=deepcopy(snapshot);new['source_batch']=continuation_envelope(snapshot,count,attempt,proof)
            batch.validate_snapshot(new);retried.append(batch.insert(q,sid,new))
    return {'batch_id':value['batch_id'],'resumed_partial':resumed,'retried_roots':retried,'worker_started':False}


def prepare_continuation(queue,previous,previous_sha,quarantine_urls):
    """Read-only, concrete current-batch plan; exact HEAD/SHA still unconfirmed."""
    from . import source_batch as batch
    from .source_runtime import job_dir
    require(file_sha(previous)==previous_sha,'RECOVERY_PREVIOUS_APPROVAL_CHANGED')
    value=json.loads(Path(previous).read_text(encoding='utf-8'))
    require(value.get('format')=='live11-recovery/v1',
            'RECOVERY_PREVIOUS_APPROVAL_FORMAT')
    prior_plan=value.get('continuation')
    extra={'format':'live11-continuation/v1','batch_id':value['batch_id'],'phases':{},'quarantine':[],
           'historical_envelopes':[],'manual_hashes':{},'mappings1282':(prior_plan or value['post_stop'])['mappings1282']}
    with queue._connect() as c,c.cursor() as q:
        c.read_only=True;batch.validate_config(queue.config);batch.validate_connection(c)
        q.execute("SELECT id FROM crawl_jobs WHERE status IN ('queued','running')")
        require(not q.fetchall(),'RECOVERY_ACTIVE_JOBS')
        q.execute('SELECT id,code,enabled,rate_settings,crawl_settings FROM suppliers ORDER BY code')
        require(normal(q.fetchall())==value['settings_before'],'RECOVERY_SETTINGS_BASELINE_DRIFT')
        q.execute('''SELECT j.id,j.supplier_id,j.status,j.crawl_run_id,j.settings_snapshot,r.metadata
            FROM crawl_jobs j LEFT JOIN crawl_runs r ON r.id=j.crawl_run_id
            WHERE j.settings_snapshot->'source_batch'->>'id'=%s ORDER BY j.id''',(value['batch_id'],))
        rows=q.fetchall();groups={}
        for row in rows:
            snap=row[4];env=snap['source_batch']
            require(env['scope_sha256'] in value['scope_hashes'] and
                    digest(batch.phase_scope(snap))==env['scope_sha256'],'RECOVERY_SCOPE_CHANGED')
            groups.setdefault(env['scope_sha256'],[]).append(row)
            extra['historical_envelopes'].append(digest(env))
        source_by_scope={phase['source_batch']['scope_sha256']:phase['supplier_code']
            for parent in value['parents'] for phase in
            (parent['snapshot'],parent['snapshot']['source_batch']['next_snapshot'])}
        for env in value['envelopes']:
            attempts=groups.get(env['scope_sha256'],[])
            rule={'source':source_by_scope[env['scope_sha256']],
                'phase':env['phase'],'max_attempt':min(8,len(attempts)+4)}
            if prior_plan:
                # Renew proof after a technical STOP without replenishing the
                # already approved per-phase attempt ceiling or time window.
                rule['max_attempt']=min(rule['max_attempt'],continuation_rule(value,env['scope_sha256'])['max_attempt'])
            extra['phases'][env['scope_sha256']]=rule
            if not attempts or attempts[-1][2]=='succeeded':continue
            jid,sid,status,run,snap,meta=attempts[-1]
            require(status in ('partial','cancelled') and len(attempts)==snap['source_batch']['attempt'],
                    'RECOVERY_PARENT_STATE_CHANGED')
            initial={'id':jid,'supplier_id':sid,'status':status,'run_id':run,'source_hashes':{}}
            rule['initial_parent']=initial
            for table in ('source_products','offers','product_matches','product_identity_decisions','review_cases'):
                predicate='t.supplier_id=%s' if table in ('source_products','offers') else 'EXISTS(SELECT 1 FROM source_products sp WHERE sp.id=t.source_product_id AND sp.supplier_id=%s)'
                q.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
                    ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t WHERE {predicate}''',(sid,))
                initial['source_hashes'][table]=normal(q.fetchone())
            if run is None:continue
            rt=meta['source_runtime'];owner=rt['owner_job_id'];folder=job_dir(snap['supplier_code'],owner)
            cp=json.loads((folder/'checkpoint.json').read_text(encoding='utf-8'))
            check=deepcopy(cp);checksum=check.pop('checksum')
            require(digest(check)==checksum and not cp['inflight'] and cp['manifest_sha256']==digest(snap['source_input']),
                    'RECOVERY_CHECKPOINT_CHECKSUM')
            initial.update(checkpoint_sha256=file_sha(folder/'checkpoint.json'),runtime_sha256=digest(rt))
            for url,row in cp['terminal'].items():
                if row['state']=='FAILED':
                    require(url in quarantine_urls,'RECOVERY_UNAPPROVED_FAILED_OR_UNCERTAIN')
                    captures=[h for h in row['history'] if h['state']=='HTTP_CAPTURED_NOT_COMMITTED']
                    require(len(captures)==1,'RECOVERY_QUARANTINE_CAPTURE_AMBIGUOUS')
                    capture=captures[0];metadata=capture['original_capture_metadata']
                    require(file_sha(metadata['evidence_ref'])==metadata['evidence_sha256'] and metadata['http_status']==200,
                            'RECOVERY_QUARANTINE_CAPTURE_CHANGED')
                    extra['quarantine'].append({'source':snap['supplier_code'],'owner_job_id':owner,
                        'input_sha256':cp['manifest_sha256'],'url':url,'failed_row':row,'captured_receipt':capture})
        require({item['url'] for item in extra['quarantine']}==set(quarantine_urls),'RECOVERY_QUARANTINE_SCOPE_CHANGED')
        require(len(extra['phases'])==22,'RECOVERY_EXPECTS_22_PHASES')
        for table in ('review_decisions','candidate_rejections'):
            q.execute(f'''SELECT count(*),md5(coalesce(string_agg(md5(to_jsonb(t)::text),''
                ORDER BY md5(to_jsonb(t)::text) COLLATE "C"),'')) FROM {table} t''')
            extra['manual_hashes'][table]=normal(q.fetchone())
        value['continuation']=extra
        continuation_invariants(q,value,queue.config)
    value['previous_approval_sha256']=previous_sha
    value['compatibility']=compatibility(batch.ROOT,value['compatibility']['base_head'],value['compatibility']['base_engine'])
    return value
