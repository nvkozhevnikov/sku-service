"""Record read-only isolated DB/accounting receipts and stop only our own cluster."""
from pathlib import Path
import hashlib
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET
import psycopg
from psycopg import sql
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from audit_ltt_uss_sources import slug_for

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/LTT_USS_INTEGRATION_2026-10-13'


def main():
    current = OUT / 'CHECKPOINT.json'
    if current.exists() and json.loads(current.read_text('utf-8')).get('acceptance') == 'slug dedupe NOT_ACCEPTED':
        raise RuntimeError('Historical blocker recorder is superseded by the accepted stable-ID policy; do not overwrite current checkpoint')
    config = json.loads((OUT / 'PRIVATE/connection.json').read_text('utf-8'))['runtime']
    receipt = json.loads((OUT / 'DB_RECEIPT.json').read_text('utf-8'))
    assert (config['host'], config['port'], config['dbname']) == ('127.0.0.1', 55455, 'universal_supplier_ltt_uss')
    with psycopg.connect(**config, autocommit=True) as connection:
        connection.execute('SET default_transaction_read_only=on')
        role = connection.execute('SELECT current_user, rolsuper FROM pg_roles WHERE rolname=current_user').fetchone()
        assert role == ('ltt_uss_runtime', False)
        names = [row[0] for row in connection.execute("SELECT tablename FROM pg_tables WHERE schemaname='public' ORDER BY tablename")]
        counts = {name: connection.execute(sql.SQL('SELECT count(*) FROM {}').format(sql.Identifier(name))).fetchone()[0] for name in names}
        for name in ('source_products', 'offers', 'product_matches', 'offer_selections', 'supplier_http_captures', 'offer_commercial_observations'):
            if name in counts:
                assert counts[name] == 0
    (OUT / 'DB_BLOCKED_STATE.json').write_text(json.dumps(dict(evidence_class='LIVE-VERIFIED', runtime_role=role,
                                                             target={k:receipt[k] for k in ('host','port','dbname','system_identifier')},
                                                             table_counts=counts), indent=2) + '\n', 'utf-8')
    audit = json.loads((OUT / 'AUDIT/SOURCE_AUDIT_SUMMARY.json').read_text('utf-8'))
    verified = []
    for entry in audit['ledger']:
        if entry['label'] == 'robots_txt':
            name = entry['source'] + '_robots.txt'
        elif entry['label'].startswith('sitemap:'):
            name = slug_for(entry['url']) + '.xml'
        else:
            name = entry['source'] + '__' + entry['label'].split(':', 1)[1] + '.html'
        assert hashlib.sha256((OUT / 'AUDIT/RAW' / name).read_bytes()).hexdigest() == entry['sha256']
        verified.append(name)
    (OUT / 'AUDIT_HASH_VERIFICATION.json').write_text(json.dumps(dict(status='PASS', retained_responses=len(verified),
                                                                    historical_audit_gets=audit['http_gets'], blocked=audit['blocked'],
                                                                    filenames=verified), indent=2) + '\n', 'utf-8')
    tests = ET.parse(OUT / 'OFFLINE_TESTS.xml').getroot()
    suites = list(tests.iter('testsuite'))
    totals = {key:sum(int(s.get(key, 0)) for s in suites) for key in ('tests','failures','errors','skipped')}
    branch = subprocess.check_output(['git','branch','--show-current'],cwd=ROOT,text=True).strip()
    sha = subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
    manifests = json.loads((OUT / 'MANIFESTS/SHA256.json').read_text('utf-8'))
    twin = json.loads((OUT / 'TWIN_PREFLIGHT/RESULT.json').read_text('utf-8'))
    checkpoint = dict(status='LTT_USS_SOURCE_INTEGRATION_BLOCKED', blocker='DISTINCT_SOURCE_IDS_NOT_DUPLICATES', source='ltt_yug',
                      branch=branch, head=sha, uncommitted_changes_preserved=True, manifests=manifests,
                      tests=totals, historical_audit_gets=32, continuation_supplier_gets=twin['http_gets'],
                      canary='NOT_STARTED', run1='NOT_STARTED', run2='NOT_STARTED', qa='NOT_STARTED', matching_run='NO',
                      matching_sql_writes=0, catalog_link_writes=0, offer_selection_writes=0, sterbrust_writes=0, esol_writes=0,
                      isolated_supplier_business_writes=0, production_changed=False, develop_changed=False,
                      commit=False, push=False, stage6='BLOCKED_IMPORT_CONTRACT_EVIDENCE',
                      next='Resolve YUG suffix-dedupe conflict before canary. Do not overwrite retained cluster or audit.')
    bin_dir = Path(os.environ['LOCALAPPDATA']) / 'Temp/universal_supplier_stage4_pg17_runtime/bin'
    data = (OUT / 'PRIVATE/pg17').resolve()
    assert data.is_relative_to(OUT.resolve())
    subprocess.run([str(bin_dir / 'pg_ctl.exe'), 'stop', '-D', str(data), '-m', 'fast', '-w', '-t', '30'], check=True)
    assert not (data / 'postmaster.pid').exists()
    checkpoint['isolated_db_runtime'] = 'CLEANLY_STOPPED; cluster/schema retained'
    (OUT / 'CHECKPOINT.json').write_text(json.dumps(checkpoint, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print(json.dumps(dict(status=checkpoint['status'], tests=totals, supplier_http=twin['http_gets'], db_runtime=checkpoint['isolated_db_runtime'])))


if __name__ == '__main__':
    main()
