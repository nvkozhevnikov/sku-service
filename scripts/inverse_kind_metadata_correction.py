"""Exact97 adaptation of accepted PRODUCT_KIND_METADATA_CORRECTION contract."""
from scripts import product_kind_metadata_correction as shared
from scripts.replay_inverse_kind97 import B,r,o
AUTH_SHA='01b4eb4607db0f3f68c7b8f14c9c36aed60622817d1a8aecc60e385a87dc792b'

def validate_gate():
    gate=r.load(B/'OFFLINE_PREPARATION.json');assert gate['status']=='PASS','OFFLINE_GATE_REQUIRED'
    for name,sha in gate['files'].items():assert r.digest((o.ROOT/name).read_bytes())==sha,('GATED_CODE_DRIFT',name)
    for name,sha in gate['artifacts'].items():assert r.digest((B/name).read_bytes())==sha,('GATED_EVIDENCE_DRIFT',name)
    return gate

def validate_manifest(manifest):
    assert r.digest((B/'AUTHORIZED_97_ROWS.json').read_bytes())==AUTH_SHA,'AUTHORIZATION_REWRITTEN'
    allowed={(a['supplier'],a['index']):a for a in r.load(B/'AUTHORIZED_97_ROWS.json')['rows']}
    rows=manifest['rows'];assert len(rows)==97 and {(a['supplier'],a['index']) for a in rows}==set(allowed),'EXACT_97_ALLOWLIST_REQUIRED'
    assert len({a['source_product_id'] for a in rows})==len({a['offer_id'] for a in rows})==97
    for a in rows:
        ref=allowed[a['supplier'],a['index']]
        assert all(a[k]==ref[k] for k in ['external_id','url','title','evidence_sha256','response_sha256']),'IDENTITY_OR_EVIDENCE_MISMATCH'
        assert a['old_kind']==ref['current_kind'],'OLD_KIND_MISMATCH'
        assert a['source_product_id']==ref['db_evidence']['source_product_id'] and a['offer_id']==ref['db_evidence']['offer_id'],'DB_ID_MISMATCH'
        assert a['new_kind']['product_kind'] in {'machine','lathe','milling_machine','drill_press','grinder','beveling_machine'} and a['new_kind']['confidence']=='HIGH','EXPECTED_MACHINE_REQUIRED'
    return sorted(rows,key=lambda a:(a['supplier'],a['external_id']))

def apply(connection,manifest,*,rollback_path,verify_only=False):
    validate_gate();rows=validate_manifest(manifest)
    target=connection.execute("SELECT current_database(),host(inet_server_addr()),inet_server_port(),current_setting('server_version')").fetchone()
    assert target==('universal_supplier_ltt_uss','127.0.0.1',55455,'17.11'),'ISOLATED_TARGET_REQUIRED'
    system=r.verify_system();assert system['system_identifier']=='7696153366151515704','ISOLATED_SYSTEM_REQUIRED'
    rollback=[];states=[]
    with connection.transaction():
        for a in rows:
            path=o.ROOT/a['original_db_record'];assert r.digest(path.read_bytes())==a['original_db_record_sha256'],'BEFORE_EVIDENCE_DRIFT'
            record=r.load(path);before=(record['product'],record['offer'])
            assert all(x['raw_data']['enrichment_evidence']['product_kind']==a['old_kind'] for x in before),'OLD_KIND_MISMATCH'
            found=connection.execute('''SELECT row_to_json(p),row_to_json(o) FROM source_products p
              JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
              WHERE s.code=%s AND p.external_id=%s AND p.id=%s AND o.id=%s
              AND p.external_id_is_stable AND o.offer_kind='default' FOR UPDATE OF p,o''',
              (a['supplier'],a['external_id'],a['source_product_id'],a['offer_id'])).fetchall()
            assert len(found)==1,'EXACT_SOURCE_OFFER_PAIR_REQUIRED'
            state,after=shared.compare_pair(before,found[0],a['new_kind']);states.append(state)
            rollback.append(dict(manifest=a,before=list(before),planned_after=list(after)))
        assert len(set(states))==1,'PARTIALLY_CORRECTED_ALLOWLIST'
        if states[0]=='CORRECTED':return dict(operation='PRODUCT_KIND_METADATA_CORRECTION',changed_rows=0,idempotent=True,verified_pairs=97)
        assert not verify_only,'VERIFICATION_FOUND_UNCORRECTED_ROWS'
        assert not rollback_path.exists(),'ROLLBACK_EVIDENCE_ALREADY_EXISTS_INSPECT_DURABLE_STATE'
        r.save(rollback_path,dict(operation='PRODUCT_KIND_METADATA_CORRECTION',target=target,system=system,authorized97_sha256=AUTH_SHA,
            manifest_sha256=r.digest(r.encoded(manifest)),rows=rollback,checked_at=r.now(),commit_pending=True))
        # Reuse the accepted CAS UPDATE implementation; both rows are in one transaction.
        for pair in rollback:
            shared.cas_update(connection,'source_products',pair['before'][0],pair['planned_after'][0])
            shared.cas_update(connection,'offers',pair['before'][1],pair['planned_after'][1])
    return dict(operation='PRODUCT_KIND_METADATA_CORRECTION',changed_rows=97,source_rows_updated=97,offer_rows_updated=97,
        new_source_products=0,new_offers=0,new_captures=0,new_observations=0,identity_mutation=0,commercial_mutation=0,
        rollback_file=str(rollback_path),committed=True,system=system)
