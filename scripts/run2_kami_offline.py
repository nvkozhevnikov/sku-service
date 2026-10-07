"""Frozen KAMI RUN2. Default: filesystem validation only, never DB/HTTP.

Future --execute requires separate operator permission and the original RUN1 DB.
Live runner stays separate. No discovery, parser recrawl, restore or fallback.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import http.client
import json
import os
from pathlib import Path
import socket
import subprocess
from unittest.mock import patch
import urllib.request

import psycopg
from psycopg import sql
from scripts.persist_kami_full_run1 import preflight, runtime_preflight, complete_fingerprint
from scripts.verify_kami_canary import fingerprint, digest
from universal_supplier.commercial_persistence import HttpCapture
from universal_supplier.kami_checkpoint import write_checkpoint
from universal_supplier.kami_persistence import prepare_observation
from universal_supplier.models import ProductCard
from universal_supplier.postgres import PostgresConfig, PostgresRepository
from scripts.kami_offline_preflight_guards import migration_integrity, commercial_proof, CompatibilityGuardError

ROOT = Path(__file__).resolve().parents[1]
BASE = Path('reports/KAMI_INTEGRATION_2026-10-05')
PREP = BASE / 'OFFLINE_RUN2_PREP_2026-10-06'
EXPECTED_COUNT = 5264
CONTRACT_VERSION = 'kami-frozen-offline-run2-v1'
LIVE_SHA = '6d04341ae7deb7d52a616eec70b51b91a20b0085b7ff60014edfec5415597b5c'
PINNED_FILES = {
    str(BASE / 'SHORT_WINDOW_RUN1/CHECKPOINT.json'): 'd05117dc3f08e05495673b4b7e685adcbc0237881d29f9c1d3726c4e50de1670',
    str(BASE / 'SOURCE_CONTINUATION/CHECKPOINT.json'): '1f4a613d5c8a6bb4d8fcf38287cf3fa6e0cb6bf389c3aa731d1fa8d2130e8864',
    str(BASE / 'RECONCILED/FINAL_DISCOVERY_VERIFIED.json'): 'dd1ccc51a1efd021ffdd8b799faf197bddc047ed9982cd5b4613eb0e8dd0bc87',
    str(BASE / 'RECONCILED/CARDS.jsonl'): '365fce78658e3cb692be48cda3633c4b8ec7642c200ce47e7ed4f67850345138',
    str(BASE / 'FULL_RUN1_CHECKPOINT.json'): 'b8156dc5da0ea7a9c619c9d8b1315928732a94a5eb23c5074e579938617f288f',
    str(BASE / 'DATABASE/RESTORE_VERIFICATION.json'): '76eaabd58821ef79b9a928456bf6381dca4593db997cee60c7d09c39e3952a1f',
    str(BASE / 'VARIANT_CANARY_VERIFIED.json'): '87261520aaeb75db5274aca7ef4675cc7e2e1fbf1e0674e538c0afb207036f2f',
    'reports/KAMI_SOURCE_AUDIT_2026-10-05/PRODUCT_RECONCILIATION/CHECKPOINT.json': '37dafd4c88fb47d73636c4f32898fe99e1e219d78184a0b47886ae637e7c3425',
    'universal_supplier/postgres.py': '8534f3f1327b1ab4fd437e618caa4a1e24dca4bd29ac5da1d459211dd565bbd5',
    'universal_supplier/kami_persistence.py': '47b4f4307b01b5debb71ee776d46e04146fbc5f2dcb67610fdaf02587dc4cb87',
    'scripts/run2_kami_products.py': LIVE_SHA,
    'scripts/persist_kami_full_run1.py': '19b5753a4e8a508fae11ac35043a2a9d077b494e99637e135e1c6bde11b804eb',
}
INVENTORY_SHA = '51ccf06fcc9879c49646839ab4147bac298f28e25d58ca743c0c9b7fb142460d'


class OfflineContractError(RuntimeError):
    pass


@contextmanager
def network_guard(*, database_allowed=False):
    """Deny HTTP independently of URL; deny sockets except approved local PG.

    libpq is C-backed, so exact PostgresConfig + server identity checks are also
    mandatory. This is defense in depth, not an OS firewall claim.
    """
    attempts = {'HTTP': 0, 'blocked_HTTP_attempts': 0, 'blocked_sockets': 0}
    def deny_http(*args, **kwargs):
        attempts['blocked_HTTP_attempts'] += 1
        raise OfflineContractError('HTTP hard-disabled in offline RUN2; no fallback')
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    def connect(sock, address):
        if not database_allowed or address != ('127.0.0.1', 55451):
            attempts['blocked_sockets'] += 1
            raise OfflineContractError('Non-approved socket blocked')
        return original_connect(sock, address)
    def connect_ex(sock, address):
        if not database_allowed or address != ('127.0.0.1', 55451):
            attempts['blocked_sockets'] += 1
            raise OfflineContractError('Non-approved socket blocked')
        return original_connect_ex(sock, address)
    # requests is not used for fetching: this patch closes a future accidental
    # dependency/fallback path, in addition to stdlib HTTP/socket traps.
    with patch('requests.sessions.Session.request', deny_http), \
         patch.object(urllib.request, 'urlopen', deny_http), \
         patch.object(http.client.HTTPConnection, 'request', deny_http), \
         patch.object(socket.socket, 'connect', connect), \
         patch.object(socket.socket, 'connect_ex', connect_ex):
        yield attempts


def verified_bytes(path, expected):
    if not path.is_file():
        raise OfflineContractError('Missing frozen evidence: '+str(path))
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected:
        raise OfflineContractError('Frozen hash mismatch: '+str(path))
    return data


def load_frozen(root=ROOT, *, pins=None, inventory_sha=None):
    """Validate ALL evidence before opening any DB connection."""
    pins = PINNED_FILES if pins is None else pins
    inventory_sha = INVENTORY_SHA if inventory_sha is None else inventory_sha
    checked = {p:verified_bytes(root/p, sha) for p,sha in pins.items()}
    def document(relative):
        key = str(BASE / relative)
        if key not in checked:
            raise OfflineContractError('Required checkpoint is not pinned')
        return json.loads(checked[key])
    source = document('SOURCE_CONTINUATION/CHECKPOINT.json')
    proof = document('RECONCILED/FINAL_DISCOVERY_VERIFIED.json')
    run1 = document('FULL_RUN1_CHECKPOINT.json')
    marker = document('DATABASE/RESTORE_VERIFICATION.json')
    if (source['status'] != 'SOURCE_RECONCILIATION_VERIFIED_STOP'
            or proof['status'] != 'REPOSITORY_VERIFIED_FULL_RECONCILIATION'
            or proof['source_completion'] != 'COMPLETE_WITH_DECLARED_EXCLUSIONS'
            or proof['proven_source_identities'] != EXPECTED_COUNT
            or proof['robots_excluded'] != 2 or proof['unresolved'] != 0
            or run1['status'] != 'LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY'
            or run1['applied_source_rows'] != EXPECTED_COUNT
            or run1['cards_sha256'] != proof['cards_sha256']):
        raise OfflineContractError('Source/RUN1 checkpoint mismatch')
    data = checked[str(BASE/'RECONCILED/CARDS.jsonl')]
    if hashlib.sha256(data).hexdigest() != proof['cards_sha256']:
        raise OfflineContractError('Cards/manifest mismatch')
    rows = [json.loads(line) for line in data.decode('utf-8').splitlines()]
    if len(rows) != EXPECTED_COUNT:
        raise OfflineContractError('Exactly 5264 frozen identities required')
    parents = sorted({str(Path(p['evidence_ref']).parent) for p in rows})
    inventory = {p:sorted(f.name for f in (root/p).glob('*.capture')) for p in parents}
    if digest(inventory) != inventory_sha:
        raise OfflineContractError('Missing/unexpected extra capture inventory')
    allowed = (root/'reports/KAMI_SOURCE_AUDIT_2026-10-05').resolve()
    # Resolve the three SHA-pinned capture directories once. Resolving every
    # ancestor for thousands of Windows junction-backed files is needlessly
    # slow; basename and exact manifest hash remain checked for every file.
    resolved_parents = {p:(root/p).resolve() for p in parents}
    if any(not p.is_relative_to(allowed) for p in resolved_parents.values()):
        raise OfflineContractError('Capture directory outside approved saved evidence')
    identities, references, pk_ids, prepared = set(), set(), set(), []
    for p in rows:
        card = ProductCard.from_jsonable(p['card'])
        if card.supplier_code != 'kami' or card.external_id in identities:
            raise OfflineContractError('Supplier scoping/duplicate frozen identity')
        rec = p['capture']
        capture_path = root / p['evidence_ref']
        if (capture_path.name != Path(p['evidence_ref']).name
                or '..' in Path(p['evidence_ref']).parts or Path(p['evidence_ref']).is_absolute()
                or capture_path.is_symlink()):
            raise OfflineContractError('Capture outside approved saved evidence')
        if rec['status'] != 200 or capture_path.name != rec['capture']:
            raise OfflineContractError('Frozen capture metadata mismatch')
        verified_bytes(capture_path, rec['sha256'])
        if p['evidence_ref'] in references:
            raise OfflineContractError('Duplicate frozen capture assignment')
        capture = HttpCapture(rec['url'], rec['url'], 200, rec['content_type'],
            datetime.fromisoformat(rec['observed_at']), rec['sha256'], rec['sha256'], p['evidence_ref'])
        projected, _ = prepare_observation(card, capture)
        previous = run1['applied'].get(card.external_id)
        if not previous or not previous['exact_replay_noop'] or previous['source_product_id'] in pk_ids:
            raise OfflineContractError('Frozen identities/RUN1 persisted ledger mismatch')
        identities.add(card.external_id); references.add(p['evidence_ref'])
        pk_ids.add(previous['source_product_id'])
        prepared.append((projected, capture, previous))
        if len(prepared) % 1000 == 0:
            print(json.dumps({'frozen_captures_validated':len(prepared),'HTTP':0,'DB_connections':0}),flush=True)
    if identities != set(run1['applied']):
        raise OfflineContractError('Unexpected extra RUN1 identity')
    marker = dict(marker, _offline_expected_run1_hashes=run1['full_saved_replay_hashes'])
    return prepared, marker, document('VARIANT_CANARY_VERIFIED.json')['baseline_hashes']


def full_state_hashes(conn):
    """Exact ALL-public-table rows plus sequence state, not just commercial flags."""
    hashes = {}
    tables = [r[0] for r in conn.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    for table in tables:
        rows = [r[0] for r in conn.execute(sql.SQL('SELECT to_jsonb(t) FROM {} t ORDER BY to_jsonb(t)::text').format(sql.Identifier(table)))]
        hashes[table] = digest(rows)
    hashes['sequences'] = digest(conn.execute("SELECT schemaname,sequencename,last_value FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename").fetchall())
    return hashes


def assert_previous_state(conn, prepared, baseline, *, snapshot_verified=False):
    """No inserts may be attempted: all source/offer/capture PKs already exist."""
    if fingerprint(conn, True) != baseline:
        raise OfflineContractError('Old namespace baseline drift')
    supplier = conn.execute("SELECT id,enabled FROM suppliers WHERE code='kami'").fetchone()
    if not supplier or supplier[1] is not False:
        raise OfflineContractError('KAMI supplier missing/enabled')
    rows = conn.execute("SELECT id,external_id,catalog_product_id,raw_data FROM source_products WHERE supplier_id=%s", (supplier[0],)).fetchall()
    sources = {r[1]:r for r in rows}
    if len(rows) != EXPECTED_COUNT or len(sources) != EXPECTED_COUNT:
        raise OfflineContractError('KAMI 5264 source baseline/duplicates mismatch')
    offers = conn.execute("SELECT source_product_id,active,offer_kind FROM offers WHERE supplier_id=%s",(supplier[0],)).fetchall()
    if (len(offers) != EXPECTED_COUNT or any(r[1] or r[2] != 'default' for r in offers)
            or {r[0] for r in offers} != {r[0] for r in rows}):
        raise OfflineContractError('KAMI 5264 inactive offers baseline mismatch')
    for card,capture,previous in prepared:
        row = sources.get(card.external_id)
        if not row or row[0] != previous['source_product_id'] or row[2] is not None:
            raise OfflineContractError('RUN1 PK/identity/link baseline mismatch')
        if row[3].get('_kami_content_fact_hash') != card.raw_data['_kami_content_fact_hash']:
            raise OfflineContractError('Saved RUN1 content fact drift')
        saved = conn.execute("SELECT id FROM supplier_http_captures WHERE supplier_id=%s AND source_product_id=%s AND capture_fingerprint=%s",
                             (supplier[0],row[0],capture.fingerprint)).fetchall()
        if saved != [(previous['capture_id'],)]:
            raise OfflineContractError('Exact RUN1 capture missing/duplicated; no insert allowed')
        stored_commercial = row[3].get('_kami_commercial_fact_hash')
        if stored_commercial is not None:
            if stored_commercial != card.raw_data['_kami_commercial_fact_hash']:
                raise OfflineContractError('Non-null stored commercial hash mismatch')
        else:
            offer = conn.execute("""SELECT to_jsonb(o) || jsonb_build_object(
                'price',o.price::text,'old_price',o.old_price::text,'quantity',o.quantity::text)
                FROM offers o WHERE supplier_id=%s AND source_product_id=%s AND offer_kind='default'""",
                (supplier[0],row[0])).fetchone()[0]
            observations = [r[0] for r in conn.execute("""SELECT to_jsonb(o) || jsonb_build_object(
                'price',o.price::text,'old_price',o.old_price::text) FROM offer_commercial_observations o
                WHERE supplier_id=%s AND source_product_id=%s AND capture_id=%s""",
                (supplier[0],row[0],previous['capture_id']))]
            commercial_proof(card,capture,previous,row[3],offer,observations,
                             snapshot_verified=snapshot_verified,capture_verified=True)
    return {'supplier_id':supplier[0], 'products':len(rows), 'offers':len(offers)}


def replay_exact(repo, prepared, fresh_preflight):
    for card,capture,previous in prepared:
        if card.supplier_code != 'kami':
            raise OfflineContractError('Non-KAMI replay refused before repository call')
        fresh_preflight()
        result = repo.persist_kami_observation(card,capture)
        if (not result['exact_noop'] or result['source_product_id'] != previous['source_product_id']
                or result['capture_id'] != previous['capture_id']):
            raise OfflineContractError('Replay not exact no-op; rollback required')


def validate_runtime_config(config):
    if (config.get('DB_HOST'),str(config.get('DB_PORT')),config.get('DB_NAME'),config.get('DB_USER')) != (
            '127.0.0.1','55451','universal_supplier_kami_rc','kami_app'):
        raise OfflineContractError('Only original isolated KAMI target permitted')


def writer_identity_in_transaction(repo):
    # Do not SET TRANSACTION READ ONLY inside an already active replay
    # transaction/savepoint (the exact replay uses SELECT FOR UPDATE).
    actual = repo.connection.execute("""SELECT current_database(),host(inet_server_addr()),
        inet_server_port(),current_setting('server_version'),current_user,
        (SELECT rolsuper FROM pg_roles WHERE rolname=current_user)""").fetchone()
    if actual != ('universal_supplier_kami_rc','127.0.0.1',55451,'17.11','kami_app',False):
        raise OfflineContractError('Actual writer identity changed inside replay')


def atomic_replay(repo, prepared, baseline, fresh_preflight, *, expected_snapshot=None):
    """Any drift must leave the outer transaction via exception, not commit."""
    with repo.connection.transaction():
        before = full_state_hashes(repo.connection)
        snapshot_verified = expected_snapshot is not None
        if snapshot_verified and complete_fingerprint(repo.connection) != expected_snapshot:
            raise OfflineContractError('RUN1 snapshot changed before atomic replay')
        assert_previous_state(repo.connection,prepared,baseline,snapshot_verified=snapshot_verified)
        replay_exact(repo,prepared,fresh_preflight)
        assert_previous_state(repo.connection,prepared,baseline,snapshot_verified=snapshot_verified)
        if full_state_hashes(repo.connection) != before:
            raise OfflineContractError('All-table/sequence drift; entire replay rejected')
        return before


def execute_frozen(prepared, marker, baseline):
    """Future explicitly authorized execution; never invoked in preparation."""
    private = Path(os.environ['LOCALAPPDATA'])/'UniversalSupplier/kami-rc-20261005'
    config = json.loads((private/'runtime.json').read_bytes()); validate_runtime_config(config)
    admin = (private/'pgpass.conf').read_text().strip().split(':',4)[4]
    if marker['system_identifier'] != '7693098812512405428':
        raise OfflineContractError('Original RUN1 system identity required')
    with psycopg.connect(host='127.0.0.1',port=55451,dbname='universal_supplier_kami_rc',
                         user='kami_admin',password=admin,connect_timeout=5,autocommit=True) as conn:
        preflight(conn,marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            verify_migrations(conn)
            if complete_fingerprint(conn) != marker['_offline_expected_run1_hashes']:
                raise OfflineContractError('Current DB does not match previous verified RUN1 snapshot')
            state = assert_previous_state(conn,prepared,baseline,snapshot_verified=True)
        os.environ.update(config)
        repo = PostgresRepository(PostgresConfig.from_env())
        try:
            repo.gate(); runtime_preflight(repo)
            print(json.dumps({'fresh_full_preflight':'PASS','system_identifier':marker['system_identifier'],
                              'supplier_id':state['supplier_id'],'source_products':state['products'],
                              'offers':state['offers'],'legacy_canaries_semantic_proof':4,'HTTP':0}),flush=True)
            # Nested repository calls cannot commit before the final global
            # invariant checks. Any detected drift rolls back the whole replay.
            before = atomic_replay(repo,prepared,baseline,
                                   lambda: (preflight(conn,marker),writer_identity_in_transaction(repo)),
                                   expected_snapshot=marker['_offline_expected_run1_hashes'])
            return dict(status='LIVE_VERIFIED_OFFLINE_RUN2_FROZEN_EXACT_NOOP', mode='OFFLINE_FROZEN_ONLY',
                        contract_version=CONTRACT_VERSION, HTTP=0, products=state['products'],offers=state['offers'],
                        replay_rows=len(prepared),inserted=0,deleted=0,semantic_fact_changes=0,audit_timestamp_changes=0,
                        old_namespaces_preserved=True,all_public_table_sequence_hashes=before)
        finally:
            repo.close()


def verify_migrations(conn):
    actual = dict(conn.execute('SELECT filename,sha256 FROM schema_migrations'))
    files = {p.name:p for p in (ROOT/'migrations').glob('[0-9][0-9][0-9]_*.sql')}
    git_files = subprocess.check_output(['git','ls-tree','-r','--name-only','HEAD','migrations/'],cwd=ROOT,text=True).splitlines()
    canonical = {Path(p).name:p for p in git_files if p.endswith('.sql')}
    if set(files) != set(actual) or set(files) != set(canonical):
        raise OfflineContractError('Migration filename set mismatch')
    results = {}
    for name,path in files.items():
        blob = subprocess.check_output(['git','show','HEAD:'+canonical[name]],cwd=ROOT)
        results[name] = migration_integrity(path.read_bytes(),blob,actual[name])
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--execute',action='store_true',help='Future separately authorized DB replay; never during preparation')
    args = parser.parse_args(argv)
    git = lambda *args: subprocess.check_output(['git',*args],cwd=ROOT,text=True).strip()
    branch = git('branch','--show-current')
    if branch != 'feature/universal-supplier-kami-rc1':
        raise OfflineContractError('KAMI feature branch required; protected branches forbidden')
    if (git('rev-parse','develop') != 'deefb595868d19ba471b9b943ff779194e231c26'
            or git('rev-parse','production') != '36924836bb02965e857173773cdb70f302914643'):
        raise OfflineContractError('Protected branch baseline mismatch')
    with network_guard(database_allowed=args.execute) as attempts:
        prepared,marker,baseline = load_frozen()
        if args.execute:
            report = execute_frozen(prepared,marker,baseline)
            folder = ROOT/BASE/'OFFLINE_RUN2'; folder.mkdir(exist_ok=True)
        else:
            report = dict(status='REPOSITORY_VERIFIED_FROZEN_VALIDATION_ONLY',mode='VALIDATION_NO_DB',
                          contract_version=CONTRACT_VERSION,frozen_identities=len(prepared),HTTP=0,DB_connections=0,DB_writes=0,
                          semantic_dataset_sha256=digest([card.as_jsonable() for card,_,_ in prepared]),
                          live_runner_unchanged=True,actual_RUN2='NOT_APPLIED',pinned_files=PINNED_FILES,
                          capture_inventory_sha256=INVENTORY_SHA)
            folder = ROOT/PREP; folder.mkdir(exist_ok=True)
        if attempts['blocked_HTTP_attempts'] or attempts['blocked_sockets']:
            raise OfflineContractError('Unexpected network attempt; no verified verdict')
        report.update(recorded_at=datetime.now(timezone.utc).isoformat(),network_guard=attempts,
                      branch=branch,git_head=git('rev-parse','HEAD'))
        write_checkpoint(folder/'VALIDATION.json' if not args.execute else folder/'CHECKPOINT.json',report)
        print(json.dumps({k:v for k,v in report.items() if k not in {'pinned_files','all_public_table_sequence_hashes'}},ensure_ascii=False))


if __name__ == '__main__':
    main()
