"""Verify complete diagnostic files and optionally publish a versioned panel index.

No HTTP, database writes, live RUN2, ESOL or production operations. Existing
panel files/index are preserved before an explicit atomic local publication.
"""
from __future__ import annotations
import argparse
from collections import Counter
import csv
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import xml.etree.ElementTree as ET
import zipfile

ROOT = Path(__file__).resolve().parents[1]
FILES = (
    'STERBRUST_UPDATE_EXISTING_PROPOSED.xml', 'STERBRUST_UPDATE_EXISTING.xlsx',
    'STERBRUST_CREATE_NEW_CANDIDATES.xml', 'STERBRUST_CREATE_NEW_CANDIDATES.xlsx',
    'STERBRUST_REVIEW.xlsx', 'MATCHING_REPORT.csv',
    'STERBRUST_EXISTING_PRICE_AVAILABILITY.xlsx', 'UNIVERSAL_SUPPLIER_NEUTRAL.xml',
)
NS = {'x': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}


def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
def load(path): return json.loads(path.read_text(encoding='utf-8'))


def workbook_rows(path, expected):
    with zipfile.ZipFile(path) as archive:
        if archive.testzip() is not None: raise ValueError('XLSX checksum failed')
        strings = []
        if 'xl/sharedStrings.xml' in archive.namelist():
            strings = [''.join(si.itertext()) for si in
                       ET.fromstring(archive.read('xl/sharedStrings.xml'))]
        sheet = ET.fromstring(archive.read('xl/worksheets/sheet1.xml'))
        rows = []
        for row in sheet.findall('.//x:sheetData/x:row', NS):
            if int(row.get('r')) < 6: continue
            values = {}
            for cell in row.findall('x:c', NS):
                if cell.find('x:f', NS) is not None: raise ValueError('Unexpected workbook formula')
                value = cell.findtext('x:v', default='', namespaces=NS)
                if cell.get('t') == 's': value = strings[int(value)]
                elif cell.get('t') == 'inlineStr': value = ''.join(cell.find('x:is', NS).itertext())
                if cell.get('t') == 'e': raise ValueError('Workbook error cell')
                values[cell.get('r').rstrip('0123456789')] = value
            if any(values.values()): rows.append(values)
        if expected == 0:
            if len(rows) != 1 or 'Подтверждённых записей' not in rows[0].get('A', ''):
                raise ValueError('Empty workbook placeholder missing')
            return []
        if len(rows) != expected: raise ValueError(f'Workbook row count differs: {path.name}')
        return rows


def verify(final, replay_path):
    replay = load(replay_path)
    if (replay.get('verified') is not True or replay.get('stage') != 'FULL_SAVED_REPLAY_VERIFIED'
            or replay.get('http_gets') != 0 or replay.get('listing_records') != 1642
            or replay.get('before') != replay.get('after')):
        raise ValueError('Complete exact-noop saved replay proof required')
    previous = load(final / 'FINAL_OUTPUTS_VERIFIED.json')
    for name, expected in previous['output_sha256'].items():
        if sha(final / name) != expected: raise ValueError('Previously checked proposal changed: ' + name)
    for name in FILES:
        if not (final / name).is_file() or not (final / name).stat().st_size:
            raise ValueError('Missing final output: ' + name)
    payload = load(final / 'PROPOSAL_TABLES.json')
    matching = load(final / 'MATCHING_FINAL.json')
    if payload.get('diagnostic_only') is not True or payload.get('esol_payload') is not False:
        raise ValueError('Non-diagnostic proposal contract')
    if payload['manifest_sha256'] != matching['manifest_sha256']:
        raise ValueError('Manifest guard mismatch')
    rows = matching['rows']
    identities = {(r['source'], str(r['external_id'])) for r in rows}
    if len(identities) != len(rows): raise ValueError('Duplicate matching source identity')
    if sum(r['source'] == 'intervesp' for r in rows) != 1642:
        raise ValueError('Incomplete Intervesp matching')
    if sum(payload['readiness_counts'].values()) != 1642:
        raise ValueError('Readiness counts do not partition full manifest')
    tables = payload['tables']
    confirmed = {(r['source'], str(r['external_id'])) for r in rows
                 if r['classification'] == 'EXISTING_CONFIRMED'}
    existing = ET.fromstring((final / FILES[0]).read_bytes().decode('utf-8', errors='strict'))
    neutral = ET.fromstring((final / 'UNIVERSAL_SUPPLIER_NEUTRAL.xml').read_bytes().decode('utf-8', errors='strict'))
    for root in (existing, neutral):
        if root.tag != 'supplier_catalog' or root.get('esol_import_payload') != 'false':
            raise ValueError('Invalid supplier-neutral XML contract')
        nodes = root.findall('./source_records/source_product')
        keys = {(n.get('supplier'), n.get('external_id')) for n in nodes}
        if len(keys) != len(nodes) or int(root.get('source_product_count')) != len(nodes):
            raise ValueError('Duplicate/inconsistent XML source identities')
        for node in nodes:
            product_id = node.findtext('sterbrust_product_id')
            if product_id and (node.get('classification') != 'EXISTING_CONFIRMED' or not product_id.isdigit()):
                raise ValueError('Unconfirmed/invented Sterbrust Product ID')
            commercial = node.find('commercial')
            price = commercial.find('price')
            if commercial.get('price_state') != 'numeric_public' and price is not None:
                raise ValueError('Request/missing price became numeric')
            if price is not None and (float(price.text) <= 0 or not price.get('currency')):
                raise ValueError('Invalid numeric price')
    if {(n.get('supplier'), n.get('external_id')) for n in existing.findall('./source_records/source_product')} != confirmed:
        raise ValueError('Existing XML coverage differs from confirmed matching')
    expected_sources = previous['source_counts']
    if dict(Counter(n.get('supplier') for n in neutral.findall('./source_records/source_product'))) != expected_sources:
        raise ValueError('Full XML does not cover all persisted suppliers')
    new = ET.fromstring((final / 'STERBRUST_CREATE_NEW_CANDIDATES.xml').read_bytes().decode('utf-8', errors='strict'))
    if new.tag != 'supplier_new_proposals' or new.get('esol_import_payload') != 'false':
        raise ValueError('Invalid NEW diagnostic XML contract')
    groups = new.findall('./canonical_new_group')
    group_ids = [n.get('new_group_id') for n in groups]
    neutral_groups = neutral.findall('./canonical_new_groups/canonical_new_group')
    neutral_group_ids = [node.get('new_group_id') for node in neutral_groups]
    if len(set(neutral_group_ids))!=len(neutral_groups) or set(neutral_group_ids)!=set(group_ids):
        raise ValueError('Full neutral NEW group references differ from creation proposals')
    neutral_memberships = set()
    for group in neutral_groups:
        for member in group.findall('./source_ref'):
            key = member.get('supplier'), member.get('external_id')
            if key in neutral_memberships or key not in identities:
                raise ValueError('Duplicate/unknown source reference in full NEW groups')
            neutral_memberships.add(key)
    if (len(set(group_ids)) != len(groups) or new.find('.//sterbrust_product_id') is not None
            or set(group_ids) != {r['new_group_id'] for r in tables['new']}):
        raise ValueError('NEW group deduplication/XML coverage failure')
    memberships = set()
    sections_path = ROOT / 'reports/rest/STERBRUST_SECTIONS_RAW.json'
    if sha(sections_path) != previous['section_snapshot_sha256']:
        raise ValueError('Sterbrust SECTION snapshot changed')
    sections = {str(section['id']) for section in load(sections_path)}
    for group in groups:
        if group.get('section_id') not in sections: raise ValueError('Missing real SECTION_ID')
        for node in group.findall('./source_records/source_product'):
            key = node.get('supplier'), node.get('external_id')
            if key in memberships or node.get('classification') != 'NEW_CANDIDATE':
                raise ValueError('Duplicate/REVIEW source in NEW proposal')
            memberships.add(key)
            if node.get('readiness') not in ('READY_TO_CREATE_IDENTITY', 'READY_TO_CREATE_FULL'):
                raise ValueError('Unready source in creation proposal')
    checks = {}
    for name, key in [('STERBRUST_UPDATE_EXISTING.xlsx', 'existing'),
                      ('STERBRUST_CREATE_NEW_CANDIDATES.xlsx', 'new'),
                      ('STERBRUST_REVIEW.xlsx', 'review'),
                      ('STERBRUST_EXISTING_PRICE_AVAILABILITY.xlsx', 'existing_price_availability')]:
        data = workbook_rows(final / name, len(tables[key]))
        if key in ('existing', 'existing_price_availability') and any(not r.get('A', '').isdigit() for r in data):
            raise ValueError('Existing workbook Product ID is not real numeric identity')
        checks[name] = len(data)
    with (final / 'MATCHING_REPORT.csv').open(encoding='utf-8-sig', newline='') as stream:
        csv_rows = list(csv.DictReader(stream))
    if len(csv_rows) != len(rows): raise ValueError('Matching CSV coverage differs')
    return {**previous, 'verified_at': datetime.now(timezone.utc).isoformat(),
            'saved_replay_proof': {'path': str(replay_path), 'sha256': sha(replay_path)},
            'file_validation': {'utf8_xml': 'PASS', 'supplier_scoped_uniqueness': 'PASS',
                'new_group_uniqueness': 'PASS', 'null_request_missing_price': 'PASS',
                'xlsx_rows': checks, 'csv_rows': len(csv_rows)},
            'output_sha256': {name: sha(final / name) for name in FILES},
            'browser_qa': 'NOT_RUN_TOOL_SECURITY_POLICY', 'full_live_RUN2': 'DEFERRED_BY_OPERATOR_STRATEGY'}


def publish_panel(final, proof, public):
    index = load(final / 'PROPOSAL_TABLES.json')
    name = 'files-' + sha(final / 'PROPOSAL_TABLES.json')[:32]
    version = public / name
    pending = public / ('.' + name + '.pending')
    public.mkdir(parents=True, exist_ok=True)
    if version.exists() or pending.exists(): raise ValueError('Panel version already exists; inspect, do not overwrite')
    pending.mkdir()
    for filename in FILES:
        shutil.copy2(final / filename, pending / filename)
        if sha(pending / filename) != proof['output_sha256'][filename]:
            raise ValueError('Panel copy checksum mismatch')
    os.replace(pending, version)
    current = public / 'PROPOSAL_TABLES.json'
    if current.exists():
        backup = public / ('PROPOSAL_TABLES.before-' + sha(current) + '.json')
        if not backup.exists(): shutil.copy2(current, backup)
        if sha(backup) != sha(current): raise ValueError('Prior panel index preservation failed')
    index.update(artifact_dir=name, verification={'diagnostic_only': True,
        'full_live_run2': 'DEFERRED', 'full_saved_replay': 'VERIFIED',
        'output_sha256': proof['output_sha256']})
    temporary = public / '.PROPOSAL_TABLES.json.pending'
    if temporary.exists(): raise ValueError('Unfinished panel index exists')
    temporary.write_text(json.dumps(index, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temporary, current)
    return {'index': str(current), 'artifact_dir': name, 'sha256': sha(current)}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--final', type=Path, required=True)
    parser.add_argument('--replay', type=Path, required=True)
    parser.add_argument('--publish-panel', action='store_true')
    args = parser.parse_args()
    result = args.final / 'FINAL_FILES_VERIFIED.json'
    if result.exists(): raise ValueError('Verification report already exists')
    proof = verify(args.final.resolve(), args.replay.resolve())
    if args.publish_panel:
        proof['panel_publication'] = publish_panel(args.final.resolve(), proof, ROOT / 'reports/RC_LOCAL/proposals')
    result.write_text(json.dumps(proof, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(proof, ensure_ascii=False))
