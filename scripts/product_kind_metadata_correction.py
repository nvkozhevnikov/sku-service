"""PRODUCT_KIND_METADATA_CORRECTION: isolated PG55455, exact authorized29 only.

No capture/observation insertion. File-backed rollback evidence precedes all
updates; source and offer JSON changes commit together in one transaction.
"""
import copy
from scripts.prepare_product_kind_29 import P, r, o

AUTH_SHA='ac06c29456511226cffcb9eebb3fcfafbf39f6eb65069d6a4c9f6508f37aab13'

def same(a,b):return r.encoded(a)==r.encoded(b)

def validate_manifest(manifest):
    path=P/'AUTHORIZED_29_ROWS.json';assert r.digest(path.read_bytes())==AUTH_SHA,'AUTHORIZATION_REWRITTEN'
    allowed={(a['supplier'],a['index']):a for a in r.load(path)['rows']}
    rows=manifest['rows'];assert len(rows)==29,'EXACT_29_REQUIRED'
    assert {(a['supplier'],a['index']) for a in rows}==set(allowed),'ALLOWLIST_MISMATCH'
    assert len({a['source_product_id'] for a in rows})==len({a['offer_id'] for a in rows})==29
    for a in rows:
        expected=allowed[a['supplier'],a['index']]
        assert a['external_id']==expected['external_id'] and a['url']==expected['url'] and a['title']==expected['title'],'IDENTITY_MISMATCH'
        assert a['source_product_id']==expected['db_evidence']['source_product_id'] and a['offer_id']==expected['db_evidence']['offer_id'],'DB_ID_MISMATCH'
        assert a['old_kind']==expected['raw_enrichment_evidence']['product_kind'],'OLD_KIND_MISMATCH'
        assert a['new_kind']['product_kind']==expected['expected_conceptual_kind'].split('/')[-1] and a['new_kind']['confidence']=='HIGH','NEW_KIND_MISMATCH'
        assert a['evidence_sha256']==expected['evidence_sha256'] and a['response_sha256']==expected['response_sha256'],'EVIDENCE_MISMATCH'
    return sorted(rows,key=lambda a:(a['supplier'],a['external_id']))

def validate_gate():
    gate=r.load(P/'OFFLINE_PREPARATION.json');assert gate['status']=='OFFLINE_PREPARATION_PASS','OFFLINE_GATE_REQUIRED'
    for path,sha in gate['files'].items():assert r.digest((o.ROOT/path).read_bytes())==sha,('GATED_FILE_DRIFT',path)
    assert gate['manifest_sha256']==r.digest((P/'PRODUCT_KIND_29_MANIFEST.json').read_bytes())
    return gate

def expected_after(row,new_kind):
    assert isinstance(row['raw_data']['enrichment_evidence']['product_kind'],dict),'MISSING_KIND_PATH'
    after=copy.deepcopy(row);after['raw_data']['enrichment_evidence']['product_kind']=copy.deepcopy(new_kind)
    return after

def compare_pair(before,actual,new_kind):
    after=tuple(expected_after(x,new_kind) for x in before)
    if same(actual,before):return 'OLD',after
    if same(actual,after):return 'CORRECTED',after
    raise AssertionError('BEFORE_STATE_OR_PARTIAL_PAIR_MISMATCH')

def cas_update(connection,table,before,after):
    assert table in {'source_products','offers'}
    # Full-row JSON CAS includes identity/commercial fields and all previous raw
    # keys; only the already-proven nested kind path is changed, no timestamps.
    query=f"""UPDATE {table} p SET raw_data=jsonb_set(raw_data,
        '{{enrichment_evidence,product_kind}}',%s::jsonb,false)
        WHERE id=%s AND row_to_json(p)::jsonb=%s::jsonb RETURNING row_to_json(p)"""
    got=connection.execute(query,(r.encoded(after['raw_data']['enrichment_evidence']['product_kind']).decode('utf8'),
        before['id'],r.encoded(before).decode('utf8'))).fetchone()
    assert got is not None and same(got[0],after),'COMPARE_AND_SET_FAILED'

def apply(connection,manifest,*,rollback_path,verify_only=False):
    validate_gate();rows=validate_manifest(manifest)
    target=connection.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version')").fetchone()
    assert target==('universal_supplier_ltt_uss','127.0.0.1',55455,'17.11'),'ISOLATED_TARGET_REQUIRED'
    system=r.verify_system();assert system['system_identifier']=='7696153366151515704','ISOLATED_SYSTEM_REQUIRED'
    rollback=[];states=[]
    with connection.transaction():
        for a in rows:
            record_path=o.ROOT/a['original_db_record']
            assert r.digest(record_path.read_bytes())==a['original_db_record_sha256'],'BEFORE_EVIDENCE_DRIFT'
            record=r.load(record_path);before=(record['product'],record['offer'])
            assert before[0]['raw_data']['enrichment_evidence']['product_kind']==a['old_kind']
            assert before[1]['raw_data']['enrichment_evidence']['product_kind']==a['old_kind']
            found=connection.execute('''SELECT row_to_json(p),row_to_json(o) FROM source_products p
                JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
                WHERE s.code=%s AND p.external_id=%s AND p.id=%s AND o.id=%s
                AND p.external_id_is_stable AND o.offer_kind='default' FOR UPDATE OF p,o''',
                (a['supplier'],a['external_id'],a['source_product_id'],a['offer_id'])).fetchall()
            assert len(found)==1,'EXACT_SOURCE_OFFER_PAIR_REQUIRED'
            state,after=compare_pair(before,found[0],a['new_kind']);states.append(state)
            rollback.append(dict(manifest=a,before=list(before),planned_after=list(after)))
        assert len(set(states))==1,'PARTIALLY_CORRECTED_ALLOWLIST'
        if states[0]=='CORRECTED':return dict(operation='PRODUCT_KIND_METADATA_CORRECTION',changed_rows=0,idempotent=True,verified_pairs=29)
        assert not verify_only,'VERIFICATION_FOUND_UNCORRECTED_ROWS'
        assert not rollback_path.exists(),'ROLLBACK_EVIDENCE_ALREADY_EXISTS_INSPECT_DURABLE_STATE'
        r.save(rollback_path,dict(operation='PRODUCT_KIND_METADATA_CORRECTION',target=target,system=system,
            authorized_manifest_sha256=r.digest(r.encoded(manifest)),rows=rollback,checked_at=r.now(),commit_pending=True))
        # Atomic rollback data is durably fsynced by r.save before first UPDATE.
        for pair in rollback:
            cas_update(connection,'source_products',pair['before'][0],pair['planned_after'][0])
            cas_update(connection,'offers',pair['before'][1],pair['planned_after'][1])
    return dict(operation='PRODUCT_KIND_METADATA_CORRECTION',changed_rows=29,source_rows_updated=29,offer_rows_updated=29,
        new_source_products=0,new_offers=0,new_captures=0,new_observations=0,identity_mutation=0,commercial_mutation=0,
        rollback_file=str(rollback_path),committed=True,system=system)
