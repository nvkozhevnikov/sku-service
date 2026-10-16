"""Read-only PG55455 LTT/USS export for an independent offline audit.

Hard guarantees:
- The whole session runs with default_transaction_read_only=on; no
  INSERT/UPDATE/DELETE/DDL/sequence changes (DB_WRITES=0).
- No supplier HTTP of any kind (no HTTP client is imported or called).
- No matching, selection, FULL QA, or correction replay (correction97 NOT repeated).
- Existing project code is untouched; this is a standalone export harness.
"""
import hashlib
import json
import os
import re
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BASE = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'
SWEEP = BASE / 'DISK_ALIAS_IMPACT_SWEEP_2026-10-16'
BATCH = BASE / 'INVERSE_KIND_97_BATCH_2026-10-16'
BIN = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
DATA = BASE / 'PRIVATE/pg17'
OUT = BASE / 'LTT_USS_READONLY_DB_SNAPSHOT_2026-10-16'
ZIP_PATH = OUT / 'LTT_USS_READONLY_DB_SNAPSHOT_2026-10-16.zip'

EXPECTED_SYSTEM_ID = '7696153366151515704'
EXPECTED_VERSION = '17.11'
EXPECTED_PORT = 55455
CAPTURES = 'supplier_http_captures'
OBSERVATIONS = 'offer_commercial_observations'
RELEVANT = ['suppliers', 'source_products', 'offers', CAPTURES, OBSERVATIONS]
EXPECTED_COUNTS = {'source_products': 2453, 'offers': 2453, CAPTURES: 2533, OBSERVATIONS: 2533}
EXPECTED_SUPPLIERS = 4
SECRET_KEY_RE = re.compile(r'(?i)(password|passwd|secret|api[_-]?key|token|authorization|cookie|credential)')
MANIFEST_EXCLUDE = {'postgres.log', 'EXPORT_PROGRESS.json', 'ARCHIVE_SHA256.txt', ZIP_PATH.name}


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(data):
    return hashlib.sha256(data).hexdigest()


def load(path):
    return json.loads(Path(path).read_text('utf-8'))


def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + '.partial')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str) + '\n', 'utf-8')
    tmp.replace(path)


def port_listening(port=EXPECTED_PORT):
    with socket.socket() as probe:
        probe.settimeout(1)
        return probe.connect_ex(('127.0.0.1', port)) == 0


def config():
    private = load(BASE / 'PRIVATE/connection.json')
    runtime = private['runtime']
    assert (runtime['host'], runtime['port'], runtime['dbname']) == ('127.0.0.1', EXPECTED_PORT, 'universal_supplier_ltt_uss')
    return private, runtime


def connect():
    import psycopg
    private, runtime = config()
    admin = dict(runtime, user='ltt_uss_admin', password=private['admin_password'],
                 application_name='ltt_uss_readonly_snapshot_export', connect_timeout=10)
    connection = psycopg.connect(**admin, autocommit=True)
    connection.execute('SET default_transaction_read_only=on')
    connection.execute("SET statement_timeout='600s'")
    connection.execute("SET lock_timeout='5s'")
    return connection


def identity(connection):
    row = connection.execute(
        "SELECT current_setting('server_version'), current_database(), current_user, current_setting('port'),"
        " system_identifier::text, pg_is_in_recovery(), current_setting('server_encoding'),"
        " current_setting('TimeZone') FROM pg_control_system()").fetchone()
    return dict(version=row[0], database=row[1], user=row[2], port=row[3], system_identifier=row[4],
                in_recovery=row[5], server_encoding=row[6], timezone=row[7])


def check_identity(info):
    assert info['version'] == EXPECTED_VERSION, ('VERSION_MISMATCH', info)
    assert info['system_identifier'] == EXPECTED_SYSTEM_ID, ('SYSTEM_ID_MISMATCH', info)
    assert info['port'] == str(EXPECTED_PORT), ('PORT_MISMATCH', info)
    assert info['database'] == 'universal_supplier_ltt_uss', ('DB_MISMATCH', info)
    assert info['in_recovery'] is False, ('IN_RECOVERY', info)
    return info
def fingerprints(connection):
    from psycopg import sql
    names = [r[0] for r in connection.execute(
        "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
    result = {}
    for name in names:
        rows = connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} AS t')
                                  .format(sql.Identifier(name))).fetchall()
        values = sorted(row[0] for row in rows)
        result[name] = dict(count=len(values), sha256=digest('\n'.join(values).encode('utf-8')))
    return result


def sequences(connection):
    from psycopg import sql
    result = {}
    for (name,) in connection.execute(
            "SELECT sequencename FROM pg_sequences WHERE schemaname='public' ORDER BY sequencename"):
        row = connection.execute(sql.SQL('SELECT last_value,is_called FROM {}')
                                 .format(sql.Identifier(name))).fetchone()
        result[name] = list(row)
    return result


def snapshot(connection):
    return dict(fingerprints=fingerprints(connection), sequences=sequences(connection))


def columns(connection, table):
    return [r[0] for r in connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema='public' AND table_name=%s"
        " ORDER BY ordinal_position", (table,))]


def start_postgres():
    assert not (DATA / 'postmaster.pid').exists(), 'postmaster.pid exists; inspect the cluster first'
    assert not port_listening(), 'port 55455 already listening'
    OUT.mkdir(parents=True, exist_ok=True)
    subprocess.run([str(BIN / 'pg_ctl.exe'), 'start', '-D', str(DATA), '-l', str(OUT / 'postgres.log'),
                    '-o', '-h 127.0.0.1 -p 55455', '-w', '-t', '30'], check=True, timeout=90)


def stop_postgres():
    return subprocess.run([str(BIN / 'pg_ctl.exe'), 'stop', '-D', str(DATA), '-m', 'fast', '-w', '-t', '30'],
                          capture_output=True, text=True, timeout=90)


def our_cluster_workers():
    command = ("@(Get-CimInstance Win32_Process -Filter \"Name='postgres.exe'\") |"
               " Where-Object {$_.CommandLine -like '*PRIVATE*pg17*'} |"
               " Measure-Object | Select-Object -ExpandProperty Count")
    result = subprocess.run(['powershell', '-NoProfile', '-Command', command],
                            capture_output=True, text=True, timeout=90)
    if result.returncode != 0:
        return None
    return int(result.stdout.strip() or 0)


def shutdown_receipt(stop):
    control = subprocess.check_output([str(BIN / 'pg_controldata.exe'), str(DATA)], text=True, timeout=60)
    lines = control.splitlines()
    state = next((line.split(':', 1)[1].strip() for line in lines if line.startswith('Database cluster state')), '')
    sysid_line = next((line for line in lines if 'Database system identifier' in line), '')
    pid_present = (DATA / 'postmaster.pid').exists()
    listening = port_listening()
    workers = our_cluster_workers()
    ok = (stop.returncode == 0 and state == 'shut down' and EXPECTED_SYSTEM_ID in sysid_line
          and not pid_present and not listening and workers == 0)
    return dict(status='CLEAN_SERVER_STOPPED' if ok else 'UNCLEAN', stop_exit=stop.returncode,
                stop_output=(stop.stdout or '') + (stop.stderr or ''), control_state=state,
                system_identifier_ok=EXPECTED_SYSTEM_ID in sysid_line, postmaster_pid_present=pid_present,
                port_55455_listening_after=listening, our_cluster_workers=workers,
                SAFE_TO_POWER_OFF='YES' if ok else 'NO', checked_at=now())
def preflight():
    import psycopg  # noqa: F401  (fail fast if the interpreter lacks the driver)
    OUT.mkdir(parents=True, exist_ok=True)
    assert not (OUT / 'STARTUP_AND_GATES.json').exists(), 'Preflight already recorded; never repeat blindly'
    assert (BIN / 'postgres.exe').exists(), 'Pinned PG17 runtime binaries missing'
    version = subprocess.check_output([str(BIN / 'postgres.exe'), '--version'], text=True).strip()
    assert version.endswith(EXPECTED_VERSION), ('PINNED_RUNTIME_MISMATCH', version)
    assert not (DATA / 'postmaster.pid').exists(), 'postmaster.pid exists; cluster may be running'
    assert not port_listening(), 'Port 55455 already in use'
    start_postgres()
    try:
        connection = connect()
        info = check_identity(identity(connection))
        actual = {name: connection.execute('SELECT count(*) FROM ' + name).fetchone()[0] for name in RELEVANT}
        table_names = [r[0] for r in connection.execute(
            "SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
        connection.close()
        expected = dict(EXPECTED_COUNTS, suppliers=EXPECTED_SUPPLIERS)
        counts_ok = actual == expected
        result = dict(status='PASS' if counts_ok else 'STOP', postgres_runtime_version=version,
                      identity=info, expected_counts=expected, actual_counts=actual,
                      counts_match=counts_ok, public_tables=len(table_names),
                      table_names=table_names, started_at=now())
        save(OUT / 'STARTUP_AND_GATES.json', result)
        assert counts_ok, ('COUNT_MISMATCH', actual)
        print('PREFLIGHT PASS', json.dumps(dict(counts=actual, tables=len(table_names)), ensure_ascii=True), flush=True)
    except BaseException:
        stop = stop_postgres()
        receipt = shutdown_receipt(stop)
        save(OUT / 'STARTUP_AND_GATES.json', dict(status='STOP_AFTER_FAILED_GATE', shutdown=receipt,
                                                  checked_at=now()))
        raise


def dump_relevant_schema(connection):
    private, _ = config()
    env = dict(os.environ, PGPASSWORD=private['admin_password'], PGCLIENTENCODING='UTF8')
    args = [str(BIN / 'pg_dump.exe'), '-h', '127.0.0.1', '-p', str(EXPECTED_PORT), '-U', 'ltt_uss_admin',
            '-d', 'universal_supplier_ltt_uss', '--schema-only', '--no-owner', '--no-privileges']
    for table in RELEVANT:
        args += ['-t', 'public.' + table]
    dump = subprocess.run(args, capture_output=True, env=env, timeout=600, check=True)
    (OUT / 'SCHEMA_RELEVANT.sql').write_bytes(dump.stdout)
    return dict(bytes=len(dump.stdout), sha256=digest(dump.stdout))


def export_suppliers_csv(connection):
    import csv
    cols = columns(connection, 'suppliers')
    path = OUT / 'suppliers.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(cols)
        for row in connection.execute('SELECT * FROM suppliers ORDER BY id'):
            writer.writerow(['' if value is None else (json.dumps(value, ensure_ascii=False, default=str)
                            if isinstance(value, (dict, list)) else value) for value in row])
    return dict(rows=connection.execute('SELECT count(*) FROM suppliers').fetchone()[0],
                sha256=digest(path.read_bytes()))


def export_table_jsonl(connection, table):
    from psycopg import sql
    path = OUT / (table + '.jsonl')
    count = 0
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for (line,) in connection.execute(sql.SQL('SELECT row_to_json(t)::text FROM {} AS t ORDER BY id')
                                          .format(sql.Identifier(table))):
            handle.write(line + '\n')
            count += 1
    return dict(rows=count, bytes=path.stat().st_size, sha256=digest(path.read_bytes()))


def jsonl_count(table):
    return sum(1 for _ in (OUT / (table + '.jsonl')).open('rb'))


def pick_path(connection, candidates, nested=False):
    if nested:
        available = {r[0] for r in connection.execute(
            "SELECT DISTINCT k FROM source_products, LATERAL jsonb_object_keys(raw_data->'source_identity') AS k"
            " WHERE raw_data ? 'source_identity'")}
        prefix = ['source_identity']
    else:
        available = {r[0] for r in connection.execute(
            'SELECT DISTINCT k FROM source_products, LATERAL jsonb_object_keys(raw_data) AS k')}
        prefix = []
    for candidate in candidates:
        if candidate in available:
            return prefix + [candidate]
    return None
def export_core():
    gates = load(OUT / 'STARTUP_AND_GATES.json')
    assert gates['status'] == 'PASS', ('GATES_NOT_PASSED', gates['status'])
    connection = connect()
    info = check_identity(identity(connection))
    before = snapshot(connection)
    assert len(before['fingerprints']) == 51, ('FINGERPRINT_TABLES', len(before['fingerprints']))
    assert len(before['sequences']) == 40, ('SEQUENCES', len(before['sequences']))
    save(OUT / 'SNAPSHOT_BEFORE.json', dict(captured_at=now(), identity=info, snapshot=before))
    table_counts = {name: value['count'] for name, value in before['fingerprints'].items()}
    save(OUT / 'TABLE_COUNTS.json', dict(captured_at=now(), expected=EXPECTED_COUNTS,
                                         relevant={k: table_counts[k] for k in RELEVANT},
                                         all_public_tables=table_counts))
    save(OUT / 'DATABASE_IDENTITY.json', dict(captured_at=now(), **info,
                                              data_directory=str(DATA), export_session='read_only',
                                              default_transaction_read_only='on'))
    receipts = dict(schema=dump_relevant_schema(connection),
                    suppliers_csv=export_suppliers_csv(connection))
    for table in ['source_products', 'offers', CAPTURES, OBSERVATIONS]:
        receipts[table] = export_table_jsonl(connection, table)
        print('EXPORTED', table, receipts[table]['rows'], 'rows', receipts[table]['bytes'], 'bytes', flush=True)
    # Verify exported row counts against the frozen baseline before anything else.
    for table, expected in EXPECTED_COUNTS.items():
        assert receipts[table]['rows'] == expected, ('EXPORT_ROW_MISMATCH', table, receipts[table]['rows'])
    return connection, info, before, receipts, table_counts


def export_product_kind_index(connection):
    import csv
    category_path = (pick_path(connection, ['source_category']) or
                     pick_path(connection, ['category', 'source_category'], nested=True))
    category_sql = ("p.raw_data#>>'{" + ','.join(category_path) + "}'" if category_path else 'NULL')
    rows = connection.execute(
        "SELECT s.code, p.id, p.external_id, p.name, p.source_url,"
        " p.raw_data#>>'{enrichment_evidence,product_kind,product_kind}',"
        " p.raw_data#>>'{enrichment_evidence,product_kind,confidence}',"
        " p.raw_data#>>'{enrichment_evidence,product_kind,source}',"
        " p.raw_data#>>'{enrichment_evidence,product_kind,matched_alias}',"
        " " + category_sql + ", o.id, o.price, o.currency, o.availability_normalized"
        " FROM source_products p JOIN suppliers s ON s.id=p.supplier_id"
        " LEFT JOIN LATERAL (SELECT * FROM offers ob WHERE ob.source_product_id=p.id ORDER BY ob.id LIMIT 1) o ON true"
        " ORDER BY p.id").fetchall()
    assert len(rows) == EXPECTED_COUNTS['source_products'], ('PRODUCT_KIND_INDEX_ROWS', len(rows))
    path = OUT / 'PRODUCT_KIND_INDEX.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['supplier', 'source_product_id', 'offer_id', 'stable_id', 'title', 'url',
                         'current_product_kind', 'confidence', 'source', 'matched_alias', 'category',
                         'price', 'currency', 'availability'])
        for row in rows:
            writer.writerow(['' if value is None else value for value in row])
    return dict(rows=len(rows), sha256=digest(path.read_bytes()), category_path=category_path)
def normalize_text(text):
    try:
        from sterbrust_matching.normalization import normalize_text as project_normalize
        return project_normalize(text or '')
    except Exception:
        return re.sub(r'\s+', ' ', (text or '')).strip().lower()


def export_disk_alias_rows(connection):
    import csv
    rules = load(SWEEP / 'DISK_ALIAS_RULES.json')
    patterns = [alias['pattern'] for alias in rules['aliases']]
    compiled = [(pattern, re.compile(pattern, re.I)) for pattern in patterns]
    category_path = (pick_path(connection, ['source_category']) or
                     pick_path(connection, ['category', 'source_category'], nested=True))
    category_sql = ("p.raw_data#>>'{" + ','.join(category_path) + "}'" if category_path else 'NULL')
    description_path = pick_path(connection, ['own_description', 'description_own', 'own_purpose'])
    description_sql = ("COALESCE(p.raw_data#>>'{" + ','.join(description_path) + "}', p.description_text)"
                       if description_path else 'p.description_text')
    rows = connection.execute(
        "SELECT s.code, p.id, p.external_id, p.name, " + category_sql + ", " + description_sql + ","
        " p.raw_data#>>'{enrichment_evidence,product_kind,product_kind}',"
        " p.raw_data#>>'{enrichment_evidence,product_kind,confidence}',"
        " p.raw_data#>>'{enrichment_evidence,product_kind,matched_alias}', o.id"
        " FROM source_products p JOIN suppliers s ON s.id=p.supplier_id"
        " LEFT JOIN LATERAL (SELECT * FROM offers ob WHERE ob.source_product_id=p.id ORDER BY ob.id LIMIT 1) o ON true"
        " ORDER BY p.id").fetchall()
    plan = load(BATCH / 'EFFECTIVE_ROW_PLAN.json')['rows']
    plan_index = {(entry.get('supplier'), str(entry.get('external_id'))): entry.get('index') for entry in plan}
    persisted = []
    disk_ids = []
    for code, pid, external_id, name, category, description, kind, confidence, matched_alias, offer_id in rows:
        hits = set()
        alias_hit = any(regex.search(normalize_text(matched_alias)) for _, regex in compiled) if matched_alias else False
        for value in (name, category, description):
            normalized = normalize_text(value)
            if normalized:
                for pattern, regex in compiled:
                    if regex.search(normalized):
                        hits.add(pattern)
        is_disk = kind == 'disk'
        if is_disk or alias_hit or hits:
            if is_disk:
                disk_ids.append(pid)
            if alias_hit:
                hits.update(pattern for pattern, regex in compiled if regex.search(normalize_text(matched_alias)))
            persisted.append([code, plan_index.get((code, str(external_id)), external_id), external_id, name,
                              category or '', description or '', kind or '', confidence or '',
                              matched_alias or '', ';'.join(sorted(hits)) or ('kind=disk' if is_disk else ''),
                              pid, offer_id or '', 'persisted'])
    corpus = load(SWEEP / 'DISK_ALIAS_CORPUS_HITS.json')
    terminal = []
    corpus_persisted_keys = set()
    for row in corpus['rows']:
        entry = row.get('entry', {})
        key = (entry.get('supplier'), str(entry.get('external_id')))
        if row.get('persisted'):
            corpus_persisted_keys.add(key)
            continue
        kind = row.get('current_kind') or {}
        matched = set()
        for field_hits in (row.get('input_matches') or {}).values():
            for hit in field_hits:
                matched.add(hit.get('pattern'))
        terminal.append([entry.get('supplier'), entry.get('index'), entry.get('external_id'),
                         row.get('title'), row.get('source_category') or '', '', kind.get('product_kind', ''),
                         kind.get('confidence', ''), kind.get('matched_alias', ''),
                         ';'.join(sorted(m for m in matched if m)), '', '', 'terminal'])
    path = OUT / 'DISK_ALIAS_ROWS.csv'
    with path.open('w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle)
        writer.writerow(['supplier', 'row_index', 'stable_id', 'title', 'category',
                         'description_used_by_classifier', 'current_kind', 'confidence', 'matched_alias',
                         'matched_regex', 'source_product_id', 'offer_id', 'terminal_or_persisted'])
        writer.writerows(sorted(persisted, key=lambda r: (r[0], str(r[1]))) + terminal)
    return dict(total_rows=len(persisted) + len(terminal), persisted_rows=len(persisted),
                terminal_rows=len(terminal), disk_kind_ids=disk_ids, sha256=digest(path.read_bytes()),
                category_path=category_path, description_path=description_path or 'column:description_text',
                crosscheck=dict(corpus_persisted_candidates=len(corpus_persisted_keys),
                                sql_persisted_rows=len(persisted)))
def export_cohorts(connection, disk_ids):
    cohorts = {}
    receipt = load(BATCH / 'INVERSE_KIND_97_CORRECTION_RECEIPT.json')
    cohorts['INVERSE97'] = sorted(row['source_product_id'] for row in receipt['rows'])
    protected = load(BATCH / 'PROTECTED_29_AND_106_FRESH_SQL.json')
    for cohort in ('PREVIOUS29', 'OPERATOR106'):
        cohorts[cohort] = sorted(row['source_product_id'] for row in protected['rows'] if row['cohort'] == cohort)
    msk = load(SWEEP / 'MSK324_EVIDENCE.json')
    cohorts['MSK324_BLOCKER'] = sorted({value for value in [msk.get('db_evidence', {}).get('source_product_id')] if value})
    id1712 = [r[0] for r in connection.execute('SELECT id FROM source_products WHERE id=1712')]
    cohorts['BLOCKER_ID1712'] = id1712
    cohorts['CURRENT_DISK'] = sorted(disk_ids)
    union = sorted({value for values in cohorts.values() for value in values})
    cohort_counts = {}
    save(OUT / 'COHORTS_INDEX.json', dict(cohorts={k: v for k, v in cohorts.items()}, union=union,
                                          union_count=len(union),
                                          note='INVERSE97=97 corrected rows; PREVIOUS29=29; OPERATOR106=106;'
                                               ' MSK324_BLOCKER/ID1712=current blockers; CURRENT_DISK=persisted'
                                               ' rows whose current product_kind is disk'))
    for table, name in ((CAPTURES, 'CAPTURES_COHORTS.jsonl'), (OBSERVATIONS, 'OBSERVATIONS_COHORTS.jsonl')):
        from psycopg import sql
        path = OUT / name
        count = 0
        with path.open('w', encoding='utf-8', newline='\n') as handle:
            query = sql.SQL('SELECT row_to_json(t)::text FROM {} AS t WHERE t.source_product_id = ANY(%s) ORDER BY t.id'
                            ).format(sql.Identifier(table))
            for (line,) in connection.execute(query, (union,)):
                handle.write(line + '\n')
                count += 1
        cohort_counts[table] = count
        assert count >= len(union), ('COHORT_EXPORT_MISMATCH', table, count, len(union))
    return dict(cohort_sizes={k: len(v) for k, v in cohorts.items()}, union=len(union),
                capture_rows=cohort_counts[CAPTURES], observation_rows=cohort_counts[OBSERVATIONS],
                note='some source_products have 2 captures, hence cohort rows exceed the id union')


def export_capture_observation_index(connection):
    cap_cols = columns(connection, CAPTURES)
    ts_col = next((c for c in ['observed_at', 'captured_at', 'occurred_at', 'created_at'] if c in cap_cols), None)
    url_col = next((c for c in ['requested_url', 'final_url', 'source_url', 'url'] if c in cap_cols), None)
    ref_col = next((c for c in ['evidence_ref', 'evidence_path', 'raw_artifact_ref'] if c in cap_cols), None)
    select = ['c.id', 'c.source_product_id', 'c.response_sha256', 'c.evidence_sha256', 'p.source_url']
    select += ['c.' + ts_col] if ts_col else ['NULL']
    select += ['c.' + url_col] if url_col else ['NULL']
    select += ['c.' + ref_col] if ref_col else ['NULL']
    rows = connection.execute(
        'SELECT ' + ', '.join(select) + ", (SELECT min(b.id) FROM " + OBSERVATIONS +
        ' b WHERE b.capture_id=c.id), s.code FROM ' + CAPTURES + ' c'
        ' JOIN source_products p ON p.id=c.source_product_id JOIN suppliers s ON s.id=c.supplier_id'
        ' ORDER BY c.id').fetchall()
    path = OUT / 'CAPTURE_OBSERVATION_INDEX.jsonl'
    with path.open('w', encoding='utf-8', newline='\n') as handle:
        for row in rows:
            handle.write(json.dumps(dict(zip(
                ['capture_id', 'source_product_id', 'response_sha256', 'evidence_sha256', 'source_url',
                 'timestamp', 'capture_url', 'raw_artifact_ref', 'observation_id', 'supplier'], row)),
                ensure_ascii=False, default=str) + '\n')
    assert len(rows) == EXPECTED_COUNTS[CAPTURES], ('CAPTURE_INDEX_ROWS', len(rows))
    return dict(rows=len(rows), ts_column=ts_col, url_column=url_col, ref_column=ref_col)


def redact_secrets():
    events = []
    for path in sorted(OUT.glob('*')):
        if path.suffix not in {'.jsonl', '.csv', '.sql'} or path.name in MANIFEST_EXCLUDE:
            continue
        if path.suffix == '.jsonl' and path.name != 'CAPTURE_OBSERVATION_INDEX.jsonl':
            lines = path.read_text('utf-8').splitlines()
            changed = False
            for index, line in enumerate(lines):
                if not SECRET_KEY_RE.search(line):
                    continue
                value = json.loads(line)

                def walk(node):
                    hits = []
                    if isinstance(node, dict):
                        for key, item in node.items():
                            if isinstance(item, str) and SECRET_KEY_RE.search(key):
                                hits.append(key)
                                node[key] = '[REDACTED]'
                            else:
                                hits += walk(item)
                    elif isinstance(node, list):
                        for item in node:
                            hits += walk(item)
                    return hits
                found = walk(value)
                if found:
                    lines[index] = json.dumps(value, ensure_ascii=False)
                    events.append(dict(file=path.name, line=index + 1, keys=sorted(set(found))))
                    changed = True
            if changed:
                path.write_text('\n'.join(lines) + '\n', 'utf-8')
        else:
            content = path.read_text('utf-8')
            if SECRET_KEY_RE.search(content):
                events.append(dict(file=path.name, note='secret-like token present; manual review required'))
    return events
def write_readme(context):
    text = f"""# LTT USS read-only DB snapshot (PG55455) — {OUT.name}

Portable, fully offline export of the isolated LTT/USS cluster for an independent
third-party audit of product_kind / disk semantics. Produced {now()}.

## Guarantees
- Export session ran with `default_transaction_read_only=on`; zero DB writes
  (51-table fingerprint + 40 sequence values identical before/after; see
  SNAPSHOT_BEFORE.json / SNAPSHOT_AFTER.json / EXPORT_RECEIPT.json).
- No supplier HTTP requests were made (no HTTP client involved; SUPPLIER_HTTP=0).
- No matching, selection, FULL QA, or correction replay (correction97 NOT repeated).
- Credentials (PRIVATE/connection.json) are NOT included.

## Contents
| File | Meaning |
|---|---|
| SCHEMA_RELEVANT.sql | pg_dump --schema-only of the 5 relevant tables |
| suppliers.csv | all suppliers (CSV, JSON columns serialized) |
| source_products.jsonl | ALL source_products rows, full row_to_json (includes raw_data) |
| offers.jsonl | ALL offers rows, full row_to_json |
| supplier_http_captures.jsonl | ALL capture rows, full row_to_json (raw blobs NOT included by design) |
| offer_commercial_observations.jsonl | ALL observation rows, full row_to_json |
| CAPTURE_OBSERVATION_INDEX.jsonl | 2533 rows: capture/observation ids, response/evidence sha256, timestamps, urls, raw artifact refs |
| COHORTS_INDEX.json + CAPTURES_COHORTS.jsonl / OBSERVATIONS_COHORTS.jsonl | full cohort subsets (INVERSE97, PREVIOUS29, OPERATOR106, MSK324_BLOCKER, BLOCKER_ID1712, CURRENT_DISK) |
| PRODUCT_KIND_INDEX.csv | one row per persisted source_product (expected 2453): stable_id, current product_kind evidence, offer ref |
| DISK_ALIAS_ROWS.csv | persisted + terminal rows matching current disk alias patterns or kind=disk |
| DATABASE_IDENTITY.json / TABLE_COUNTS.json | cluster identity + live counts (all 51 public tables) |
| SNAPSHOT_BEFORE.json / SNAPSHOT_AFTER.json | 51-table fingerprints + 40 sequence values |
| STARTUP_AND_GATES.json / SHUTDOWN_RECEIPT.json / EXPORT_RECEIPT.json / MANIFEST_SHA256.txt | audit receipts |

## Field provenance
- product_kind evidence: raw_data.enrichment_evidence.product_kind
  (product_kind / confidence / source / matched_alias).
- category: raw_data path {context.get('category_path', 'column lookup')} (first non-null candidate).
- description_used_by_classifier: {context.get('description_path', 'column description_text')};
  the classifier's own_description input — terminal rows leave it empty (offline payloads not in DB).
- Disk alias patterns (from DISK_ALIAS_RULES.json, unchanged since the sealed sweep):
  {'; '.join(context.get('disk_patterns', []))}
- Persisted row_index values map to the sealed EFFECTIVE_ROW_PLAN journal indexes.

## Disk alias row selection
A persisted row is included when kind=disk OR the stored matched_alias matches a disk
pattern OR a lexical disk-pattern hit occurs in title/category/description. Terminal rows
(non-persisted offline candidates) come from DISK_ALIAS_CORPUS_HITS.json and carry their
input_matches patterns in matched_regex. Cross-check counts: {json.dumps(context.get('crosscheck', {}), ensure_ascii=False)}.

## Baselines
{json.dumps(EXPECTED_COUNTS, ensure_ascii=False)} — matched at preflight, at export, and after export.
"""
    (OUT / 'README.md').write_text(text, 'utf-8')


def write_manifest():
    entries = []
    for path in sorted(p for p in OUT.iterdir() if p.is_file() and p.name not in MANIFEST_EXCLUDE
                       and not p.name.endswith('.partial')):
        if path.name in {'MANIFEST_SHA256.txt', ZIP_PATH.name}:
            continue
        entries.append(f"{digest(path.read_bytes())}  {path.name}")
    (OUT / 'MANIFEST_SHA256.txt').write_text('\n'.join(entries) + '\n', 'utf-8')
    return len(entries)


def export_phase():
    assert not (OUT / 'EXPORT_PROGRESS.json').exists(), 'Export already ran; do not repeat blindly'
    connection, info, before, receipts, table_counts = export_core()
    kind_index = export_product_kind_index(connection)
    disk = export_disk_alias_rows(connection)
    cohorts = export_cohorts(connection, disk['disk_kind_ids'])
    capture_index = export_capture_observation_index(connection)
    connection.close()
    redactions = redact_secrets()
    (OUT / 'REDACTIONS.md').write_text(
        '# Redactions\n\n' + (json.dumps(redactions, ensure_ascii=False, indent=2) if redactions
                              else 'No secret-like keys found in exported artifacts; nothing was redacted.') + '\n',
        'utf-8')
    rules = load(SWEEP / 'DISK_ALIAS_RULES.json')
    write_readme(dict(category_path=kind_index['category_path'], description_path=disk['description_path'],
                      disk_patterns=[a['pattern'] for a in rules['aliases']], crosscheck=disk['crosscheck']))
    save(OUT / 'EXPORT_PROGRESS.json', dict(finished_at=now(), receipts=receipts, kind_index=kind_index,
                                            disk={k: v for k, v in disk.items() if k != 'disk_kind_ids'},
                                            cohorts=cohorts, capture_index=capture_index,
                                            redactions=redactions))
    print('EXPORT PHASE DONE', json.dumps(dict(disk_rows=disk['total_rows'], cohorts=cohorts,
                                               kind_rows=kind_index['rows']), ensure_ascii=True), flush=True)
def finish_phase():
    progress = load(OUT / 'EXPORT_PROGRESS.json')
    before_doc = load(OUT / 'SNAPSHOT_BEFORE.json')
    before = before_doc['snapshot']
    connection = connect()
    info = check_identity(identity(connection))
    after = snapshot(connection)
    connection.close()
    save(OUT / 'SNAPSHOT_AFTER.json', dict(captured_at=now(), identity=info, snapshot=after))
    fingerprints_equal = before['fingerprints'] == after['fingerprints']
    sequences_equal = before['sequences'] == after['sequences']
    counts_before = {name: value['count'] for name, value in before['fingerprints'].items()}
    counts_after = {name: value['count'] for name, value in after['fingerprints'].items()}
    relevant_before = {k: counts_before[k] for k in RELEVANT}
    relevant_after = {k: counts_after[k] for k in RELEVANT}
    assert fingerprints_equal and sequences_equal, ('DB_STATE_CHANGED', fingerprints_equal, sequences_equal)
    assert relevant_after == dict(EXPECTED_COUNTS, suppliers=EXPECTED_SUPPLIERS), ('COUNTS_DRIFTED', relevant_after)
    stop = stop_postgres()
    shutdown = shutdown_receipt(stop)
    save(OUT / 'SHUTDOWN_RECEIPT.json', shutdown)
    receipt = dict(export_id=OUT.name, produced_at=now(), database_identity=info,
                   pinned_postgres_runtime=EXPECTED_VERSION, system_identifier=EXPECTED_SYSTEM_ID,
                   port=EXPECTED_PORT,
                   table_counts_before_export=relevant_before, table_counts_after_export=relevant_after,
                   all_public_tables_counted=len(counts_before),
                   fingerprints=dict(tables=len(before['fingerprints']), before_equals_after=fingerprints_equal),
                   sequences=dict(count=len(before['sequences']), before_equals_after=sequences_equal),
                   db_writes=0, supplier_http=0,
                   supplier_http_rationale='Export harness performs no HTTP; raw supplier payloads are'
                                           ' referenced by sha256 and remain on the local disk.',
                   correction97_replayed=False, matching_executed=False, full_qa_executed=False,
                   exported_files=progress, shutdown=shutdown)
    save(OUT / 'EXPORT_RECEIPT.json', receipt)
    manifest_files = write_manifest()
    import zipfile
    with zipfile.ZipFile(ZIP_PATH, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in sorted(p for p in OUT.iterdir() if p.is_file() and p.name not in MANIFEST_EXCLUDE
                           and not p.name.endswith('.partial') and p.name != ZIP_PATH.name):
            archive.write(path, path.name)
    archive_sha = digest(ZIP_PATH.read_bytes())
    (OUT / 'ARCHIVE_SHA256.txt').write_text(archive_sha + '  ' + ZIP_PATH.name + '\n', 'utf-8')
    print('FINISH DONE', json.dumps(dict(fingerprints_equal=fingerprints_equal, sequences_equal=sequences_equal,
                                         db_writes=0, supplier_http=0, manifest_files=manifest_files,
                                         shutdown=shutdown['status'],
                                         safe_to_power_off=shutdown['SAFE_TO_POWER_OFF'],
                                         archive=ZIP_PATH.name, archive_sha256=archive_sha,
                                         archive_bytes=ZIP_PATH.stat().st_size), ensure_ascii=True), flush=True)
    if shutdown['status'] != 'CLEAN_SERVER_STOPPED':
        raise SystemExit('UNCLEAN_SHUTDOWN')


def main():
    if sys.stdout.encoding and sys.stdout.encoding.lower() != 'utf-8':
        sys.stdout.reconfigure(encoding='utf-8')
    phase = sys.argv[1] if len(sys.argv) > 1 else ''
    {'preflight': preflight, 'export': export_phase, 'finish': finish_phase}[phase]()


if __name__ == '__main__':
    main()
