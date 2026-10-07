"""Read-only closure evidence for the operator's bounded KAMI work window."""
import hashlib
import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import psycopg
from scripts.persist_kami_full_run1 import preflight
from scripts.verify_kami_canary import fingerprint
from universal_supplier.kami_checkpoint import write_checkpoint


def main():
    root = Path(__file__).resolve().parents[1]
    base = root / 'reports/KAMI_INTEGRATION_2026-10-05'
    out = base / 'SHORT_WINDOW_RUN1'
    out.mkdir(exist_ok=True)
    private = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/kami-rc-20261005'
    marker = json.loads((base / 'DATABASE/RESTORE_VERIFICATION.json').read_bytes())
    password = (private / 'pgpass.conf').read_text().strip().split(':', 4)[4]
    ledger = json.loads((base / 'FULL_RUN1_CHECKPOINT.json').read_bytes())
    if ledger['status'] != 'LIVE_VERIFIED_FULL_RUN1_SAVED_CAPTURE_REPLAY':
        raise RuntimeError('Full RUN1 verification required for this closure')
    with psycopg.connect(host='127.0.0.1', port=55451, dbname='universal_supplier_kami_rc',
                        user='kami_admin', password=password, connect_timeout=5, autocommit=True) as conn:
        preflight(conn, marker)
        with conn.transaction():
            conn.execute('SET TRANSACTION READ ONLY')
            baseline = json.loads((base / 'VARIANT_CANARY_VERIFIED.json').read_bytes())['baseline_hashes']
            assert fingerprint(conn, True) == baseline
            counts = {s:n for s,n in conn.execute('SELECT s.code,count(p.id) FROM suppliers s LEFT JOIN source_products p ON p.supplier_id=s.id GROUP BY s.code ORDER BY s.code')}
            tables = {t:conn.execute('SELECT count(*) FROM '+t).fetchone()[0] for t in
                      ('suppliers','source_products','offers','catalog_products','product_matches',
                       'supplier_http_captures','offer_commercial_observations','product_properties','product_media')}
            flags = conn.execute("SELECT s.enabled,count(o.id),count(*) FILTER (WHERE o.active) FROM suppliers s JOIN offers o ON o.supplier_id=s.id WHERE s.code='kami' GROUP BY s.enabled").fetchone()
            assert counts['kami'] == 5264 and flags == (False,5264,0)
    git = lambda *args: subprocess.check_output(['git',*args],cwd=root,text=True).strip()
    assert git('branch','--show-current') == 'feature/universal-supplier-kami-rc1'
    assert git('rev-parse','develop') == 'deefb595868d19ba471b9b943ff779194e231c26'
    assert git('rev-parse','production') == '36924836bb02965e857173773cdb70f302914643'
    paths = ['scripts/persist_kami_full_run1.py','scripts/checkpoint_kami_short_window.py',
             'universal_supplier/postgres.py','tests/test_kami_final_gates.py','tests/test_kami_persistence.py',
             'reports/KAMI_INTEGRATION_2026-10-05/FULL_RUN1_CHECKPOINT.json',
             'reports/KAMI_INTEGRATION_2026-10-05/RECONCILED/FINAL_DISCOVERY_VERIFIED.json',
             'reports/KAMI_INTEGRATION_2026-10-05/RECONCILED/CARDS.jsonl',
             'reports/KAMI_INTEGRATION_2026-10-05/SHORT_WINDOW_FOCUSED.xml']
    hashes = {p:hashlib.sha256((root/p).read_bytes()).hexdigest() for p in paths}
    command = "Set-Location '"+str(root)+"'; $env:PYTHONPATH=(Get-Location).Path; & '..\\sku-service\\.venv-audit\\Scripts\\python.exe' -X utf8 scripts/run2_kami_products.py"
    state = dict(status='LIVE_VERIFIED_FULL_RUN1_SAFE_STOP',recorded_at=datetime.now(timezone.utc).isoformat(),
                 resume_from=str(out/'CHECKPOINT.json'),next_action='Fresh isolated DB preflight, then live RUN2 only in an ample operator-approved time window; never repeat discovery or RUN1',
                 resume_command=command,git_head=git('rev-parse','HEAD'),branch=git('branch','--show-current'),
                 working_tree=git('status','--short'),hashes=hashes,database=marker['database'],port=55451,
                 system_identifier=marker['system_identifier'],data_directory=marker['data_directory'],
                 database_counts=tables,source_counts=counts,applied=dict(kami_source_products=5264,new_rows_this_window=5260,
                 supplier_enabled=False,offers_active=False,canonical_links=0),
                 verified=dict(old_five_namespace_hashes_preserved=True,full_saved_replay_exact_noop=True,
                 full_saved_replay_rows=5264,duplicates=0,focused_tests='17 PASS + 2 subtests'),
                 not_started=['live RUN2','matching/dedup','exports','full suite','dump','restore rehearsal','panel QA'],
                 HTTP_this_window=0,protected_branches_unchanged=True,
                 frozen_old_accepted=dict(Existing=506,FULL_NEW=2,Review=3640,Conflict=259),
                 incident='Initial saved-content transaction rolled back on out-of-order category timestamps; fixed observed interval with LEAST/GREATEST. PostgreSQL sequences may consume values on rollback; no promise of contiguous PKs.')
    write_checkpoint(out/'CHECKPOINT.json',state)
    (out/'REPORT.md').write_text('# KAMI bounded window — FULL RUN1 verified\n\nDONE: no crawl/discovery repeat; bounded RUN1 runner and timestamp regression fix.\n\nAPPLIED: 5260 new KAMI rows; 5264 source products/offers total on isolated localhost55451 only. Supplier disabled, offers inactive, canonical links zero.\n\nLIVE-VERIFIED: exact whole saved-capture replay 5264; all persisted table/sequence hashes unchanged by replay; no duplicate source identities; old five namespace hashes preserved.\n\nREPOSITORY-VERIFIED: 17 focused tests + 2 subtests PASS. Initial sandbox test failures were temporary-file access failures; one argv compatibility regression was corrected before passing rerun. No full suite.\n\nRUN2 intentionally NOT STARTED: 5264 sequential live requests cannot safely fit the shutdown window. No HTTP, matching, exports, dump, restore, deployment or production/ESOL operations.\n\nResume strictly from CHECKPOINT.json after fresh approved DB preflight, in a longer time window. Exact command and Git HEAD/code hashes are in the checkpoint.\n',encoding='utf-8')
    print(json.dumps(state,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    main()
