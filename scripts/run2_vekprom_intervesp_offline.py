"""Offline RUN2: exact-noop replay of RUN1 captures for vekprom/intervesp.

No HTTP client is constructed anywhere in this script (http_gets is asserted
zero).  Every PERSISTED RUN1 row is replayed from its stored sanitised
evidence file: the SHA-256 is verified, the adapter re-parses the bytes, the
semantic fields are compared with the RUN1 checkpoint, and the reconstructed
HttpCapture must persist as an exact no-op.  Any deviation is a DEFECT with
an exact blocker; nothing is retried or repaired here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.adapters.intervesp import parse_intervesp_detail  # noqa: E402
from universal_supplier.adapters.vekprom import parse_vekprom_detail  # noqa: E402
from universal_supplier.commercial_persistence import HttpCapture  # noqa: E402
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
DB_IDENTITY = ('universal_supplier_vekprom_intervesp', '127.0.0.1', 55452, '17.11',
               'vekprom_intervesp_app', False, '7694223408076967768')
PARSERS = {'vekprom': parse_vekprom_detail, 'intervesp': parse_intervesp_detail}


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_evidence(run1_dir: Path, supplier: str, ref) -> Path:
    text = str(ref)
    if text.startswith('capture:'):
        text = text[len('capture:'):]
    text = text.lstrip('/\\')
    path = Path(text)
    if not path.is_absolute():
        path = run1_dir / 'EVIDENCE' / supplier / path
    return path


def _evidence_bytes(run1_dir: Path, supplier: str, row: dict) -> bytes:
    return resolve_evidence(run1_dir, supplier, row['evidence_ref']).read_bytes()


def semantic_fields(product) -> dict:
    return {'external_id': product.site_internal_id, 'name': product.name,
            'price_state': product.price.state.value,
            'price': None if product.price.current_price is None else str(product.price.current_price),
            'currency': product.price.currency, 'availability': product.availability or 'unknown'}


def replay_one(supplier: str, url: str, row: dict) -> tuple[dict, list[str]]:
    errors: list[str] = []
    evidence_path = resolve_evidence(REPORTS / f'{supplier.upper()}_RUN1', supplier, row['evidence_ref'])
    if not evidence_path.is_file():
        return {}, [f'evidence_file_missing:{evidence_path}']
    body = evidence_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != row['evidence_sha256']:
        return {}, ['evidence_sha256_mismatch']
    product = PARSERS[supplier](body.decode('utf-8', 'replace'), source_url=row['final_url'])
    parsed = semantic_fields(product)
    expected = {key: row.get(key) for key in parsed}
    if parsed != expected:
        errors.append(f'semantic_delta:{json.dumps({"parsed": parsed, "run1": expected}, ensure_ascii=False)}')
    capture = HttpCapture(requested_url=url, final_url=row['final_url'], http_status=row['http_status'],
                          content_type=row.get('content_type'),
                          observed_at=datetime.fromisoformat(row['observed_at']),
                          response_sha256=row['response_sha256'], evidence_sha256=row['evidence_sha256'],
                          evidence_ref=row['evidence_ref'])
    return parsed, errors


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--supplier', choices=('vekprom', 'intervesp'), required=True)
    args = parser.parse_args()
    run1_dir = REPORTS / f'{args.supplier.upper()}_RUN1'
    out_dir = REPORTS / f'{args.supplier.upper()}_RUN2'
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / 'RUN2_CHECKPOINT.json'
    run1 = json.loads((run1_dir / 'CHECKPOINT.json').read_text(encoding='utf-8'))
    if run1.get('supplier') != args.supplier or run1.get('status') != 'COMPLETED':
        raise SystemExit(f'RUN1 for {args.supplier} is not COMPLETED: {run1.get("status")}')
    persisted = {url: row for url, row in run1['applied'].items() if row.get('status') == 'PERSISTED'}
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})
    repository = PostgresRepository(PostgresConfig.from_env())
    receipt = {'supplier': args.supplier, 'run1_manifest_sha256': run1.get('manifest_sha256'),
               'run1_status': run1['status'], 'recorded_at': now_iso(), 'http_gets': 0,
               'replayed': 0, 'exact_noop': 0, 'semantic_delta': 0, 'unexpected_rows': 0,
               'defects': [], 'status': 'RUN2_RUNNING'}
    try:
        with repository.connection.cursor() as cursor:
            cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
                       current_setting('server_version'),current_user,pg_is_in_recovery(),
                       system_identifier::text FROM pg_control_system()""")
            identity = cursor.fetchone()
            if identity != DB_IDENTITY:
                raise SystemExit(f'RUN2 database identity mismatch: {identity}')
            cursor.execute("""SELECT count(*) FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
                              WHERE s.code=%s""", (args.supplier,))
            captures_before = cursor.fetchone()[0]
            cursor.execute("""SELECT count(*) FROM offer_commercial_observations o
                              JOIN suppliers s ON s.id=o.supplier_id WHERE s.code=%s""", (args.supplier,))
            observations_before = cursor.fetchone()[0]
        for url, row in sorted(persisted.items()):
            parsed, errors = replay_one(args.supplier, url, row)
            receipt['replayed'] += 1
            if any(error.startswith('semantic_delta:') for error in errors):
                receipt['semantic_delta'] += 1
            receipt['defects'].extend({'url': url, 'error': error[:500]} for error in errors)
            structural = [error for error in errors if not error.startswith('semantic_delta:')]
            if structural:
                continue
            product = PARSERS[args.supplier](_evidence_bytes(run1_dir, args.supplier, row)
                                             .decode('utf-8', 'replace'), source_url=row['final_url'])
            capture = HttpCapture(requested_url=url, final_url=row['final_url'], http_status=row['http_status'],
                                  content_type=row.get('content_type'),
                                  observed_at=datetime.fromisoformat(row['observed_at']),
                                  response_sha256=row['response_sha256'], evidence_sha256=row['evidence_sha256'],
                                  evidence_ref=row['evidence_ref'])
            try:
                result = repository.persist_commercial_observation(product, capture)
            except Exception as error:  # noqa: BLE001 - recorded as DEFECT
                receipt['defects'].append({'url': url, 'error': f'replay_error:{type(error).__name__}:{error}'[:500]})
                continue
            if result['exact_noop'] and not result['observation_created']:
                receipt['exact_noop'] += 1
            else:
                receipt['defects'].append({'url': url, 'error': f'not_exact_noop:{json.dumps(result, default=str)}'})
        with repository.connection.cursor() as cursor:
            cursor.execute("""SELECT count(*) FROM supplier_http_captures c JOIN suppliers s ON s.id=c.supplier_id
                              WHERE s.code=%s""", (args.supplier,))
            receipt['captures_before'] = captures_before
            receipt['captures_after'] = cursor.fetchone()[0]
            cursor.execute("""SELECT count(*) FROM offer_commercial_observations o
                              JOIN suppliers s ON s.id=o.supplier_id WHERE s.code=%s""", (args.supplier,))
            receipt['observations_before'] = observations_before
            receipt['observations_after'] = cursor.fetchone()[0]
        receipt['unexpected_rows'] = ((receipt['captures_after'] - receipt['captures_before'])
                                      + (receipt['observations_after'] - receipt['observations_before']))
        ok = (receipt['unexpected_rows'] == 0 and receipt['semantic_delta'] == 0
              and receipt['exact_noop'] == receipt['replayed'] == len(persisted)
              and not receipt['defects'])
        receipt['status'] = 'RUN2_EXACT_NOOP_VERIFIED' if ok else 'RUN2_DEFECT'
        receipt['finished_at'] = now_iso()
        checkpoint_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps({k: receipt[k] for k in ('status', 'replayed', 'exact_noop', 'semantic_delta',
                                                  'unexpected_rows', 'http_gets')}, ensure_ascii=False), flush=True)
        if not ok:
            raise SystemExit(4)
    finally:
        repository.close()


if __name__ == '__main__':
    main()
