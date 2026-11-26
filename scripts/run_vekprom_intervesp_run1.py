"""Resumable live RUN1 for the authorized vekprom/intervesp ingestion.

One paced public GET per sitemap candidate (20 s pause, 20 s timeout, one
attempt, strict same-host validation), engine adapters, then the narrow
passive-commercial persistence transaction. Checkpoint after every card;
NETWORK_ERROR rows are retried on resume, 403/429 stops the whole run. No
matching, no scheduler, no catalog writes, no external services.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.adapters.intervesp import parse_intervesp_detail  # noqa: E402
from universal_supplier.adapters.vekprom import parse_vekprom_detail  # noqa: E402
from universal_supplier.commercial_discovery import normalise_model  # noqa: E402
from universal_supplier.full_supplier_collection import (  # noqa: E402
    FullManifest, PublicPacer, _SourceClient, _safe_source_url)
from universal_supplier.http_capture import (  # noqa: E402
    CaptureStatus, FilesystemEvidenceStore, UrllibPublicHttpClient, capture_public_html,
    _validate_public_url)
from urllib.parse import urlparse  # noqa: E402


class VekpromDetailClient:
    """Entry URLs keep the strict 4-segment sitemap form; redirect hops only
    pass the generic public-URL safety gate.  The runner then classifies the
    final URL (same / canonical same-slug / stale funnel) explicitly."""

    def __init__(self, inner: UrllibPublicHttpClient) -> None:
        self.inner = inner

    def get(self, url: str, *, timeout_seconds: float):
        _safe_source_url('vekprom', url)
        response = self.inner.get_validated(url, timeout_seconds=timeout_seconds,
                                            validator=_validate_public_url)
        _validate_public_url(response.final_url)
        for redirect in response.redirects:
            _validate_public_url(redirect)
        return response


def vekprom_final_state(requested: str, final: str) -> str:
    if final.rstrip('/') == requested.rstrip('/'):
        return 'SAME'
    parsed, req = urlparse(final), urlparse(requested)
    if (parsed.scheme == 'https' and parsed.hostname in {'vekprom.ru', 'www.vekprom.ru'}
            and not parsed.query and not parsed.fragment):
        parts = parsed.path.rstrip('/').split('/')
        req_parts = req.path.rstrip('/').split('/')
        if len(parts) == 3 and parts[1] == 'catalog' and len(req_parts) == 4 and parts[2] == req_parts[3]:
            return 'CANONICAL'
    return 'OTHER'
from universal_supplier.postgres import PostgresConfig, PostgresRepository  # noqa: E402

REPORTS = ROOT / 'reports/VEKPROM_INTERVESP_INTEGRATION_2026-10-08'
PRIVATE = Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/vekprom-intervesp-20261008'
DB_IDENTITY = ('universal_supplier_vekprom_intervesp', '127.0.0.1', 55452, '17.11',
               'vekprom_intervesp_app', False, '7694223408076967768')
PARSERS = {'vekprom': parse_vekprom_detail, 'intervesp': parse_intervesp_detail}
RETRYABLE = {'NETWORK_ERROR'}
# Operator-authorized 2026-10-09: random continuous pacing for the live vekprom
# RUN1 only.  Every other supplier keeps the default fixed 20 s PublicPacer.
VEKPROM_PACING_RANGE = (3.0, 7.0)


def build_pacer(supplier: str) -> PublicPacer:
    if supplier == 'vekprom':
        return PublicPacer.with_jitter_range(*VEKPROM_PACING_RANGE)
    return PublicPacer()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def load_runtime_env() -> None:
    config = json.loads((PRIVATE / 'runtime.json').read_text(encoding='utf-8'))
    os.environ.update({key: config[key] for key in
                       ('DB_HOST', 'DB_PORT', 'DB_NAME', 'DB_USER', 'DB_PASSWORD', 'DB_SSLMODE')})


def verify_identity(repository: PostgresRepository) -> None:
    with repository.connection.cursor() as cursor:
        cursor.execute("""SELECT current_database(),host(inet_server_addr()),inet_server_port(),
                   current_setting('server_version'),current_user,pg_is_in_recovery(),
                   system_identifier::text FROM pg_control_system()""")
        identity = cursor.fetchone()
        cursor.execute('SELECT rolsuper FROM pg_roles WHERE rolname=current_user')
        superuser = cursor.fetchone()[0]
    if identity != DB_IDENTITY or superuser:
        raise RuntimeError(f'RUN1 database identity mismatch: {identity} superuser={superuser}')


def write_atomic(path: Path, payload: dict) -> None:
    pending = path.with_suffix(path.suffix + '.tmp')
    pending.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding='utf-8')
    pending.replace(path)


def capture_row(supplier: str, item, capture) -> dict:
    row = {'url': item.url, 'expected_model': item.expected_model,
           'http_status': capture.http_status, 'status': capture.status.value,
           'final_url': capture.final_url, 'diagnostics': list(capture.diagnostics)}
    if capture.status is CaptureStatus.SUCCESS and capture.capture and capture.evidence_body:
        try:
            product = PARSERS[supplier](capture.evidence_body.decode('utf-8', 'replace'),
                                        source_url=capture.final_url)
            if supplier == 'vekprom':
                if not product.site_internal_id or not product.name:
                    raise ValueError('stable site ID or product name not proven')
            else:
                if not product.site_internal_id or not product.supplier_model:
                    raise ValueError('stable site ID or primary model not proven')
                if normalise_model(product.supplier_model) != item.expected_model:
                    raise ValueError('sitemap model and product card disagree')
            row.update({'external_id': product.site_internal_id, 'name': product.name,
                        'model': product.supplier_model, 'price_state': product.price.state.value,
                        'price': None if product.price.current_price is None else str(product.price.current_price),
                        'currency': product.price.currency, 'availability': product.availability or 'unknown',
                        'source_category': product.source_category, '_product': product})
        except ValueError as error:
            row['status'] = 'REVIEW'
            row['diagnostics'].append(str(error))
    return row


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--supplier', choices=('vekprom', 'intervesp'), required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--max-seconds', type=int, default=None)
    parser.add_argument('--stop-file', type=Path, default=None)
    parser.add_argument('--out-dir', type=Path, default=None)
    args = parser.parse_args()
    out_dir = args.out_dir or REPORTS / f'{args.supplier.upper()}_RUN1'
    out_dir.mkdir(parents=True, exist_ok=True)
    checkpoint_path = out_dir / 'CHECKPOINT.json'
    manifest = FullManifest.from_jsonable(json.loads(args.manifest.read_text(encoding='utf-8')))
    candidates = [item for item in manifest.candidates if item.source == args.supplier]
    if not candidates:
        raise SystemExit(f'no {args.supplier} candidates in manifest')
    checkpoint = {'supplier': args.supplier, 'manifest_sha256': manifest.sha256,
                  'manifest_path': str(args.manifest), 'status': 'RUNNING',
                  'started_at': now_iso(), 'updated_at': now_iso(), 'http_gets': 0,
                  'applied': {}}
    if checkpoint_path.is_file():
        previous = json.loads(checkpoint_path.read_text(encoding='utf-8'))
        if previous.get('manifest_sha256') == manifest.sha256 and previous.get('supplier') == args.supplier:
            checkpoint['started_at'] = previous.get('started_at', checkpoint['started_at'])
            checkpoint['http_gets'] = previous.get('http_gets', 0)
            checkpoint['applied'] = {url: row for url, row in previous.get('applied', {}).items()
                                     if row.get('status') not in RETRYABLE}
    if args.supplier == 'vekprom':
        low, high = VEKPROM_PACING_RANGE
        checkpoint['pacing'] = {'policy': 'random_uniform', 'min': low, 'max': high,
                                'authorized': 'operator 2026-10-09 vekprom random-pacing canary',
                                'process_started_at': now_iso(), 'delays': []}
    load_runtime_env()
    repository = PostgresRepository(PostgresConfig.from_env())
    client = (VekpromDetailClient(UrllibPublicHttpClient()) if args.supplier == 'vekprom'
              else _SourceClient(args.supplier, UrllibPublicHttpClient()))
    pacer = build_pacer(args.supplier)
    store = FilesystemEvidenceStore()
    evidence_dir = out_dir / 'EVIDENCE' / args.supplier
    started = time.monotonic()
    terminal = {'COMPLETED', 'SOURCE_BLOCKED', 'DB_ERROR'}
    try:
        verify_identity(repository)
        write_atomic(checkpoint_path, checkpoint)
        seen_ids: dict[tuple[str, str], str] = {
            (args.supplier, row['external_id']): url
            for url, row in checkpoint['applied'].items() if row.get('external_id')}
        for item in candidates:
            if checkpoint['status'] in terminal:
                break
            if item.url in checkpoint['applied']:
                continue
            if args.stop_file and args.stop_file.is_file():
                checkpoint['status'] = 'STOPPED_BY_FILE'
                break
            if args.max_seconds is not None and time.monotonic() - started >= args.max_seconds:
                checkpoint['status'] = 'BUDGET_STOPPED'
                break
            delay = pacer.before_get()
            if 'pacing' in checkpoint and delay:
                checkpoint['pacing']['delays'].append(round(delay, 3))
            checkpoint['http_gets'] += 1
            capture = capture_public_html(item.url, evidence_dir=evidence_dir, client=client,
                                          timeout_seconds=20, max_attempts=1,
                                          expected_model=item.expected_model or None,
                                          evidence_store=store)
            row = capture_row(args.supplier, item, capture)
            if (args.supplier == 'vekprom' and capture.final_url
                    and vekprom_final_state(item.url, capture.final_url) == 'OTHER'):
                row.pop('_product', None)
                row['status'] = 'SITEMAP_STALE_MISS'
                row['diagnostics'].append('redirect_funnel_not_a_detail_page')
            if capture.status is CaptureStatus.BLOCKED:
                row['diagnostics'].append('source_blocked_stop')
                checkpoint['applied'][item.url] = row
                checkpoint['status'] = 'SOURCE_BLOCKED'
                checkpoint['updated_at'] = now_iso()
                write_atomic(checkpoint_path, checkpoint)
                break
            if capture.status is CaptureStatus.SUCCESS and capture.capture and capture.evidence_body:
                product = row.pop('_product', None)
                if product is not None:
                    identity_key = (args.supplier, row['external_id'])
                    if identity_key in seen_ids and seen_ids[identity_key] != item.url:
                        row['status'] = 'REVIEW'
                        row['diagnostics'].append('site internal ID reused by another URL in this run')
                    else:
                        seen_ids[identity_key] = item.url
                        try:
                            result = repository.persist_commercial_observation(product, capture.capture)
                        except ValueError as error:
                            # Card-level rejection (e.g. intervesp identity collision):
                            # a REVIEW row, never a whole-run database stop.
                            row['status'] = 'REVIEW'
                            row['diagnostics'].append(f'persistence_rejected:{error}')
                            checkpoint['applied'][item.url] = row
                            checkpoint['updated_at'] = now_iso()
                            write_atomic(checkpoint_path, checkpoint)
                            continue
                        except Exception as error:
                            row['status'] = 'DB_ERROR'
                            row['diagnostics'].append(f'database_error:{type(error).__name__}')
                            checkpoint['applied'][item.url] = row
                            checkpoint['status'] = 'DB_ERROR'
                            checkpoint['updated_at'] = now_iso()
                            write_atomic(checkpoint_path, checkpoint)
                            break
                        row.update({'status': 'PERSISTED',
                                    'observation_created': bool(result['observation_created']),
                                    'exact_noop': bool(result['exact_noop']),
                                    'projection_updated': bool(result['projection_updated']),
                                    'source_product_id': result.get('source_product_id'),
                                    'offer_id': result.get('offer_id'),
                                    'capture_id': result.get('capture_id'),
                                    'observation_id': result.get('observation_id'),
                                    'ingest_counts': asdict(result['ingest_counts']),
                                    'observed_at': capture.capture.observed_at.isoformat(),
                                    'content_type': capture.content_type,
                                    'response_sha256': capture.capture.response_sha256,
                                    'evidence_sha256': capture.capture.evidence_sha256,
                                    'evidence_ref': capture.capture.evidence_ref})
            checkpoint['applied'][item.url] = row
            checkpoint['updated_at'] = now_iso()
            write_atomic(checkpoint_path, checkpoint)
        else:
            if checkpoint['status'] == 'RUNNING':
                checkpoint['status'] = 'COMPLETED'
        checkpoint['applied_count'] = len(checkpoint['applied'])
        checkpoint['candidate_count'] = len(candidates)
        checkpoint['updated_at'] = now_iso()
        write_atomic(checkpoint_path, checkpoint)
        print(json.dumps({'supplier': args.supplier, 'status': checkpoint['status'],
                          'applied': checkpoint['applied_count'], 'candidates': len(candidates),
                          'http_gets': checkpoint['http_gets']}, ensure_ascii=False), flush=True)
    finally:
        repository.close()


if __name__ == '__main__':
    main()
