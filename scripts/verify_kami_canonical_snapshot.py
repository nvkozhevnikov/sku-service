"""Read-only verification of completed fresh GET-only registry; hashes only output."""
import csv
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'reports/KAMI_INTEGRATION_2026-10-05/CANONICAL_CURRENT'


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def csv_ids(path):
    with path.open(encoding='utf-8-sig', newline='') as stream:
        values = [r['sterbrust_product_id'] for r in csv.DictReader(stream)]
    if any(not v.isdecimal() for v in values) or len(set(values)) != len(values):
        raise RuntimeError('CSV registry identity failure')
    return set(values)


def main():
    metadata = json.loads((OUT / 'sterbrust_snapshot_metadata.json').read_bytes())
    if (metadata['snapshot_qa'] != 'PASS' or metadata['api_pagination_qa'] != 'PASS'
            or metadata['write_methods_used'] != 0 or metadata['duplicate_product_ids'] != 0
            or any(v for k,v in metadata['http_methods'].items() if k != 'GET')):
        raise RuntimeError('Complete GET-only snapshot required')
    ids, count = set(), 0
    with (OUT / 'STERBRUST_REGISTRY.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            row = json.loads(line)
            key = row['sterbrust_product_id']
            if not key.isdecimal() or key in ids:
                raise RuntimeError('JSONL identity failure')
            cleaned = json.dumps(row['raw_data'], ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if hashlib.sha256(cleaned.encode()).hexdigest() != row['snapshot_hash']:
                raise RuntimeError('Canonical raw row hash mismatch')
            ids.add(key); count += 1
    if ids != csv_ids(OUT / 'STERBRUST_REGISTRY.csv') or count != metadata['unique_product_ids']:
        raise RuntimeError('CSV/JSONL/metadata identity mismatch')
    pinned = csv_ids(ROOT / 'reports/STERBRUST_REGISTRY.csv')
    paths = ['STERBRUST_REGISTRY.csv', 'STERBRUST_REGISTRY.jsonl', 'sterbrust_snapshot_metadata.json',
             'rest/STERBRUST_PRODUCTS_RAW.jsonl', 'rest/STERBRUST_PROPERTIES_RAW.json',
             'rest/STERBRUST_SECTIONS_RAW.json', 'rest/STERBRUST_BRANDS_RAW.json']
    report = {'status': 'VERIFIED_FRESH_GET_ONLY_CANONICAL_SNAPSHOT', 'products': count,
        'captured_at': metadata['captured_at'], 'sections': metadata['sections_total'],
        'properties': metadata['properties_total'], 'http_methods': metadata['http_methods'],
        'identity_delta_added': sorted(ids - pinned, key=int),
        'identity_delta_removed': sorted(pinned - ids, key=int),
        'prior_registry_not_modified': True,
        'sha256': {p:file_sha(OUT / p) for p in paths}}
    (OUT / 'VERIFIED.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False))


if __name__ == '__main__':
    main()
