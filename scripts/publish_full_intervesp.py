"""Read-only RC snapshot -> complete Intervesp matching and neutral proposals.

No crawl, SQL mutation or selection activation. Refuses incomplete identity closure;
keeps earlier final outputs intact in their own versioned evidence directory.
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
import sys
from lxml import html, etree

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.ingest_intervesp_listing_archive import saved_pairs, digest
from scripts.intervesp_full_preflight import read_source_state
from scripts.generate_proposed_matching import read_qa_rows, EXPECTED_REGISTRY_SHA256
from scripts.build_characteristic_evidence import section_evidence
from universal_supplier.postgres import PostgresConfig
from universal_supplier.proposed_matching import classify_persisted_sources, _card
from universal_supplier.matching_service import load_registry_from_csv, _source, _candidates
from universal_supplier.intervesp_final_proposals import (
    assert_matching_identity_complete, remaining_detail_buckets,
    apply_detail_quarantine, readiness_proposals, final_counts, terminal_bucket,
    preserve_unresolved_reviews,
)
from universal_supplier.characteristic_evidence import build_evidence, equipment_scope, unit_from_label
from universal_supplier.supplier_neutral_xml import neutral_snapshot_xml, neutral_new_groups_xml
from universal_supplier.intervesp_selective_enrichment import post_matching_detail_plan
from universal_supplier.proposal_export import build_proposal_tables
from sterbrust_matching.product_identity import classify_model_role, classify_product_kind
from sterbrust_matching.normalization import normalize_model

BASE = ROOT / 'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01'
EVIDENCE = ROOT / 'reports/RC_LOCAL/CHARACTERISTIC_EVIDENCE_2026-10-01_VERIFIED'


def load(path): return json.loads(path.read_text(encoding='utf-8'))
def save(path, value): path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding='utf-8')


def saved_review_titles(source):
    """Observed names only; multiple H1s remain multiple, never pick one."""
    try:
        return [' '.join(node.text_content().split()) for node in html.fromstring(source).xpath('//h1')]
    except (ValueError, etree.ParserError):
        return []


def attach_source_evidence(rows, records, registry):
    source = {(r['supplier_code'], str(r['external_id'])): r for r in records}
    for row in rows:
        for key in ('match_conflicts','match_warnings'):
            if isinstance(row.get(key),str):
                try: row[key]=json.loads(row[key])
                except ValueError: row[key]=['unparsed_matching_diagnostics:'+row[key]]
        record = source[(row['source'], str(row['external_id']))]
        raw = record.get('raw_data') or {}
        content = raw.get('source_content') or {}
        card = _card(record)
        capture = raw.get('commercial_capture') or {}
        row.update(brand=card.brand, source_category=card.categories[0].name if card.categories else '',
                   properties={p.name: p.value for p in card.properties},
                   observed_properties=[{'name': p.name, 'value': p.value, 'unit': unit_from_label(p.name)}
                                        for p in card.properties],
                   model_role=classify_model_role(card.name, row.get('source_category') or '', row['model']).role,
                   product_kind=classify_product_kind(card.name, row.get('source_category') or '').product_kind,
                   evidence_ref=capture.get('evidence_ref') or '',
                   sterbrust_name=registry.products.get(row.get('sterbrust_product_id'), {}).get('name', ''),
                   enrichment={**content, 'enrichment_evidence': raw.get('enrichment_evidence') or {},
                               'evidence_ref': capture.get('evidence_ref'),
                               'price_state': row.get('price_state'), 'availability': row.get('availability')})
        if row.get('detail_review_evidence'):
            row['observed_detail_titles'] = saved_review_titles(
                Path(row['detail_review_evidence']).read_text(encoding='utf-8'))
    return rows


def complete_canonical_properties(registry, records, build_proof):
    target_ids = {candidate['sterbrust_product_id'] for record in records
                  for candidate in _candidates(_source(_card(record)), registry)}
    target_ids.update(str(r['sterbrust_product_id']) for r in
                      load(ROOT / 'reports/RC_LOCAL/MATCHING_FINAL.json')['rows']
                      if r['classification'] == 'EXISTING_CONFIRMED')
    canonical, seen, checksum = {}, set(), hashlib.sha256()
    with (ROOT / 'reports/STERBRUST_REGISTRY.jsonl').open('rb') as handle:
        for line in handle:
            checksum.update(line)
            record = json.loads(line)
            sid = str(record['sterbrust_product_id'])
            if sid in seen: raise ValueError('Duplicate canonical snapshot identity')
            seen.add(sid)
            if sid in target_ids:
                canonical[sid] = record
                registry.products[sid]['properties'] = {
                    p['property_name']: p['value_flat'] for p in record.get('properties', [])
                    if p.get('property_name') and p.get('value_flat') not in ('', None)}
    if checksum.hexdigest() != build_proof['registry_jsonl_sha256'] or len(seen) != 59500:
        raise ValueError('Canonical snapshot completeness/SHA changed')
    return canonical


def learned_from_confirmed(rows, canonical):
    sections = {str(r['id']): r for r in load(ROOT / 'reports/rest/STERBRUST_SECTIONS_RAW.json')}
    pairs = []
    for row in rows:
        if row['classification'] != 'EXISTING_CONFIRMED' or not row.get('full_model_confirmed'): continue
        target = canonical.get(str(row['sterbrust_product_id']))
        if target is None: raise ValueError('Confirmed canonical property evidence missing')
        pairs.append({**row, 'equipment_type': equipment_scope(row['name'], row['source_category']),
            'source_properties': row.get('observed_properties') or
                                 [{'name': k, 'value': v, 'unit': unit_from_label(k)}
                                  for k, v in row['properties'].items()],
            'sterbrust_properties': [{'name': p['property_name'], 'value': p['value_flat'], 'unit': p.get('unit', '')}
                                    for p in target.get('properties', []) if p.get('value_flat') not in (None, '', 'N')],
            'sections': section_evidence(target.get('category_ids') or [target['category_id']], sections)})
    return build_evidence(pairs), pairs


def read_all_sources():
    """Historical offer prices remain explicitly historical, not public prices."""
    import psycopg
    with psycopg.connect(host='127.0.0.1', port=55449, dbname='universal_supplier_server', user='rc_readonly',
                         connect_timeout=5, options='-c default_transaction_read_only=on', autocommit=True) as conn:
        identity = conn.execute('SELECT current_database(),current_user,system_identifier FROM pg_control_system()').fetchone()
        if identity != ('universal_supplier_server', 'rc_readonly', 7691270601420084116):
            raise ValueError('All-source snapshot identity mismatch')
        cur = conn.execute("""SELECT s.code AS supplier_code,sp.id AS source_product_id,sp.external_id,sp.name,sp.source_url,sp.canonical_url,
            sp.brand_raw,sp.manufacturer_raw,sp.description_text,
            sp.raw_data,o.availability_normalized,co.price_state,co.price,co.currency,co.observed_at,
            o.price AS historical_offer_price,o.currency AS historical_offer_currency,o.last_success_at
            FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
            JOIN offers o ON o.supplier_id=s.id AND o.source_product_id=sp.id AND o.offer_kind='default'
            LEFT JOIN LATERAL (SELECT co.* FROM offer_commercial_observations co
                WHERE co.source_product_id=sp.id AND co.supplier_id=s.id
                AND COALESCE(co.region_code,'')=COALESCE(sp.raw_data->>'_commercial_projection_region','')
                ORDER BY co.observed_at DESC,co.id DESC LIMIT 1) co ON true ORDER BY s.code,sp.external_id""")
        rows = [dict(zip([c.name for c in cur.description], values)) for values in cur.fetchall()]
        by_id = {r['source_product_id']: r for r in rows}
        props, media, categories = {}, {}, {}
        for sid, name, value in conn.execute('SELECT source_product_id,raw_name,raw_value FROM product_properties '
                                             'WHERE offer_id IS NULL ORDER BY source_product_id,position,id'):
            props.setdefault(sid, []).append([name, value])
        for sid, kind, url, title in conn.execute('SELECT source_product_id,media_type,source_url,title FROM product_media '
                                                 'WHERE offer_id IS NULL ORDER BY source_product_id,position,id'):
            media.setdefault(sid, []).append((kind, url, title))
        for sid, name in conn.execute('SELECT pc.source_product_id,c.name FROM source_product_categories pc '
                                      'JOIN supplier_categories c ON c.id=pc.category_id AND c.supplier_id=pc.supplier_id '
                                      'ORDER BY pc.source_product_id,c.depth,c.id'):
            categories.setdefault(sid, []).append(name)
        for sid, row in by_id.items():
            raw = dict(row.get('raw_data') or {})
            content = dict(raw.get('source_content') or {})
            fallback = {'description_text': row.get('description_text'),
                        'technical_properties': props.get(sid, []),
                        'source_images': [url for kind,url,_ in media.get(sid, []) if kind=='image'],
                        'source_documents': [{'url':url,'title':title or ''} for kind,url,title in media.get(sid, []) if kind=='document'],
                        'source_category': ' / '.join(categories.get(sid, []))}
            for key, value in fallback.items():
                if not content.get(key) and value: content[key] = value
            raw['source_content'] = content
            row['raw_data'] = raw
    for row in rows:
        if row['supplier_code'] in ('partner_st', 'optimum'):
            row.update(price_state='historical_snapshot_not_current_commercial', price=None, currency=None,
                       source_freshness='RESTORED_HISTORICAL_SNAPSHOT_NOT_LIVE')
    return rows


def write_csv(path, rows):
    columns = ['source', 'external_id', 'classification', 'readiness_state', 'sterbrust_product_id',
               'sterbrust_name', 'new_candidate_id', 'new_group_id', 'name', 'model', 'execution',
               'proposed_section_id', 'price', 'price_state', 'currency', 'availability', 'source_url',
               'match_conflicts', 'identity_blockers', 'enrichment_blockers', 'evidence_ref',
               'observed_detail_titles', 'detail_review_reason', 'detail_review_evidence']
    with path.open('w', encoding='utf-8-sig', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            fields = {**row, 'readiness_state': terminal_bucket(row), **{
                key: row.get('readiness', {}).get(key, []) for key in ('identity_blockers', 'enrichment_blockers')}}
            values = {}
            for key in columns:
                value = fields.get(key)
                if isinstance(value, (dict, list)): value = json.dumps(value, ensure_ascii=False)
                if isinstance(value, str) and value[:1] in ('=', '+', '-', '@'): value = "'" + value
                values[key] = value
            writer.writerow(values)


def publish(output):
    if output.exists(): raise ValueError('Output exists; preserve previous final proposal')
    saved = saved_pairs()
    by_url = {row['product_url']: row for row, _, _ in saved}
    report = load(BASE / 'DETAIL_ENRICHMENT.json')
    detail_plan = [{'url': r['product_url'], 'reason': r['detail_get_reason']}
                   for r in by_url.values() if r['detail_get_reason'] != 'none']
    detail = assert_matching_identity_complete(report, detail_plan)
    for row in detail.values():
        if row['status']=='REVIEW_HTTP_TIMEOUT': continue  # saved listing provenance remains pinned
        if digest(Path(row['evidence_ref'])) != row['capture']['evidence_sha256']:
            raise ValueError('Detail evidence hash changed')
    before = read_source_state()
    os.environ['PGPASSFILE'] = str(Path(os.environ['LOCALAPPDATA']) / 'UniversalSupplier/rc-local/pgpass.conf')
    _, records = read_qa_rows(PostgresConfig('127.0.0.1', 55449, 'universal_supplier_server', 'rc_readonly', None, 'disable'),
                              expected_system_identifier=7691270601420084116)
    intervesp = [r for r in records if r['supplier_code'] == 'intervesp']
    if len(intervesp) != 1642 or {r['source_url'] for r in intervesp} != set(by_url):
        raise ValueError('Persisted source coverage differs from full manifest')
    csv_path = ROOT / 'reports/STERBRUST_REGISTRY.csv'
    if digest(csv_path) != EXPECTED_REGISTRY_SHA256: raise ValueError('Canonical registry SHA changed')
    proof = load(EVIDENCE / 'BUILD_VERIFIED.json')
    for relative, sha in proof['input_sha256'].items():
        if digest(ROOT / relative) != sha:
            raise ValueError('Confirmed-pair input/section snapshot changed: ' + relative)
    for name, sha in proof['output_sha256'].items():
        if digest(EVIDENCE / name) != sha: raise ValueError('Typed evidence/index changed')
    registry = load_registry_from_csv(csv_path)
    canonical = complete_canonical_properties(registry, records, proof)
    rows = classify_persisted_sources(records, registry)
    rows = attach_source_evidence(apply_detail_quarantine(rows, detail), records, registry)
    # Preserve old confirmed decisions only without new identity evidence.
    prior = {(r['source'], str(r['external_id'])): r for r in load(ROOT / 'reports/RC_LOCAL/MATCHING_FINAL.json')['rows']}
    rows = preserve_unresolved_reviews(rows, prior)
    for row in rows:
        old = prior.get((row['source'], str(row['external_id'])))
        if (old and old['classification'] in ('EXISTING_CONFIRMED', 'CONFLICT')
                and row['source_url'] not in detail
                and normalize_model(old.get('model')) == normalize_model(row.get('model'))
                and not row.get('axis_execution_evidence',{}).get('blocked')
                and not row.get('condition_execution_evidence',{}).get('blocked')
                and (old['classification'] == 'CONFLICT' or not row.get('match_conflicts'))):
            row.update(classification=old['classification'], sterbrust_product_id=old.get('sterbrust_product_id', ''),
                       full_model_confirmed=old.get('full_model_confirmed', False),
                       prior_confirmed_decision_retained=True)
            if old['classification'] == 'CONFLICT':
                row['match_conflicts'] = old.get('match_conflicts') or ['prior_confirmed_conflict_retained']
            row['sterbrust_name'] = registry.products.get(row['sterbrust_product_id'], {}).get('name', '')
    for row in rows:
        listing = by_url.get(row['source_url'])
        if listing and row['source_url'] not in detail and listing['matching_evidence'] != 'SUFFICIENT':
            row.update(classification='REVIEW', sterbrust_product_id='', new_candidate_id='',
                       listing_identity_reason='listing_matching_evidence_insufficient')
    learned, pairs = learned_from_confirmed(rows, canonical)
    absence = load(EVIDENCE / 'CANONICAL_ABSENCE_INDEX.json')
    rows, groups, audit = readiness_proposals(rows, learned, absence,
                                             absence_review_ref=str(output / 'CANONICAL_ABSENCE_AUDIT.json'))
    final_intervesp = [r for r in rows if r['source'] == 'intervesp']
    if sum(final_counts(final_intervesp).values()) != 1642: raise ValueError('Final counts do not partition manifest')
    all_sources = read_all_sources()
    after = read_source_state()
    if before['table_counts'] != after['table_counts']: raise ValueError('Concurrent data changed during publication')
    generated = datetime.now(timezone.utc).isoformat()
    output.mkdir(parents=True)
    save(output / 'MATCHING_FINAL.json', {'generated_at': generated, 'diagnostic_only': True,
         'registry_sha256': EXPECTED_REGISTRY_SHA256, 'manifest_sha256': report['manifest_sha256'],
         'summary': {'intervesp': final_counts(final_intervesp)}, 'rows': rows})
    save(output / 'SCOPED_CHARACTERISTIC_EVIDENCE.json', learned)
    save(output / 'CONFIRMED_PAIR_CHARACTERISTICS.json', pairs)
    save(output / 'NEW_GROUP_PROPOSALS.json', groups)
    save(output / 'CANONICAL_ABSENCE_AUDIT.json', audit)
    selective = post_matching_detail_plan(rows, detail_plan, report)
    selective.update(checkpoint_sha256=digest(BASE/'DETAIL_ENRICHMENT.json'),
                     matching_sha256=digest(output/'MATCHING_FINAL.json'),
                     manifest_sha256=report['manifest_sha256'])
    save(output / 'POST_MATCHING_DETAIL_PLAN.json', selective)
    write_csv(output / 'MATCHING_REPORT.csv', rows)
    full_content = all(all((r.get('raw_data') or {}).get('enrichment_evidence', {}).get(field + '_checked')
                           for field in ('description', 'characteristics', 'images', 'documents'))
                       for r in intervesp)
    neutral = neutral_snapshot_xml(all_sources, matching=rows, generated_at=generated, enrichment_complete=full_content)
    (output / 'UNIVERSAL_SUPPLIER_NEUTRAL.xml').write_bytes(neutral)
    existing_keys = {(r['source'], str(r['external_id'])) for r in rows if r['classification'] == 'EXISTING_CONFIRMED'}
    existing_records = [r for r in all_sources if (r['supplier_code'], str(r['external_id'])) in existing_keys]
    (output / 'STERBRUST_UPDATE_EXISTING_PROPOSED.xml').write_bytes(neutral_snapshot_xml(
        existing_records, matching=rows, generated_at=generated))
    (output / 'STERBRUST_CREATE_NEW_CANDIDATES.xml').write_bytes(neutral_new_groups_xml(
        all_sources, matching=rows, generated_at=generated))
    tables = build_proposal_tables([r for r in rows if r['classification'] == 'EXISTING_CONFIRMED'], registry.products)
    tables['existing_price_availability'] = [r for r in rows if r['classification'] == 'EXISTING_CONFIRMED']
    tables['review'] = [{**r, 'export_status': terminal_bucket(r), 'export_reason': '; '.join(
        [r['detail_review_reason']] if r.get('detail_review_reason') else
        [r['axis_execution_evidence']['reason']] if r.get('axis_execution_evidence', {}).get('blocked') else
        [r['condition_execution_evidence']['reason']] if r.get('condition_execution_evidence', {}).get('blocked') else
        r.get('grouping_reasons') or r.get('readiness', {}).get('identity_blockers') or r.get('match_conflicts') or [])}
        for r in rows if r['classification'] != 'EXISTING_CONFIRMED' and not r.get('readiness', {}).get('identity_ready')]
    ready_ids = {r['new_group_id'] for r in rows if r.get('readiness', {}).get('identity_ready')}
    tables['new'] = []
    for group in groups['groups']:
        if group['new_group_id'] not in ready_ids: continue
        members = [r for r in rows if r.get('new_group_id') == group['new_group_id']]
        if not all(r.get('readiness', {}).get('identity_ready') for r in members): continue
        section_ids = {r['proposed_section_id'] for r in members}
        if len(section_ids) != 1 or None in section_ids: continue
        representative = min(members, key=lambda r: (not r['readiness']['full_ready'], r['source'], r['external_id']))
        tables['new'].append({**representative, 'proposed_name': representative['name'],
             'section_id': representative['proposed_section_id'], 'source_offers': members,
             'section_path': representative.get('proposed_section_path'),
             'readiness_state': representative['readiness']['state'],
             'enrichment_blockers': representative['readiness']['enrichment_blockers'],
             'characteristic_count': len(representative.get('enrichment', {}).get('technical_properties') or []),
             'image_count': len(representative.get('enrichment', {}).get('source_images') or []),
             'document_count': len(representative.get('enrichment', {}).get('source_documents') or []),
             'sterbrust_product_id': None, 'absence_evidence': str(output / 'CANONICAL_ABSENCE_AUDIT.json')})
    save(output / 'PROPOSAL_TABLES.json', {'diagnostic_only': True, 'esol_payload': False,
         'manifest_sha256': report['manifest_sha256'], 'registry_sha256': EXPECTED_REGISTRY_SHA256,
         'readiness_counts': final_counts(final_intervesp), 'tables': tables})
    summary = {'generated_at': generated, 'DONE': 'full listing matching with identity-critical detail closure; optional content deferred',
        'APPLIED': 'local diagnostic files only; this publisher performs zero SQL mutations',
        'VERIFIED': 'pinned coverage, snapshot identities/hashes, unique source records and read-only counts',
        'intervesp_counts': final_counts(final_intervesp), 'detail_gets': report['detail_gets'],
        'rate_test_detail_gets': 2, 'robots_gets': 1, 'reused_rate_captures': report['reused_rate_captures'],
        'detail_http_statuses': dict(Counter(str(r['http_status']) for r in report['rows'])),
        'detail_review': sum(r['status'] in ('REVIEW','REVIEW_HTTP_TIMEOUT') for r in report['rows']),
        'detail_coverage_basis': 'identity-critical-closure; nonidentity GETs deferred by operator',
        'deferred_detail_buckets': {key: len(value) for key, value in remaining_detail_buckets(detail_plan,report).items()},
        'http_timeouts': sum(r['status']=='REVIEW_HTTP_TIMEOUT' for r in report['rows']),
        'full_live_RUN2': 'DEFERRED_BY_OPERATOR_STRATEGY', 'sql_before': before['table_counts'], 'sql_after': after['table_counts'],
        'source_counts': dict(Counter(r['supplier_code'] for r in all_sources)),
        'section_snapshot_sha256': proof['input_sha256']['reports\\rest\\STERBRUST_SECTIONS_RAW.json'],
        'historical_content_origin': 'persisted product_properties/media/categories; restored snapshot, not refreshed',
        'safe_section_mappings': sum(r['verdict'] == 'SAFE' for r in learned['section_mappings']),
        'usable_existing_numeric_RUB': sum(r['price_state'] == 'numeric_public' and r['currency'] == 'RUB'
                                          for r in final_intervesp if r['classification'] == 'EXISTING_CONFIRMED'),
        'observed_existing_availability': sum(r['availability'] != 'unknown' for r in final_intervesp
                                             if r['classification'] == 'EXISTING_CONFIRMED'),
        'output_sha256': {p.name: digest(p) for p in sorted(output.iterdir()) if p.is_file()}}
    save(output / 'FINAL_OUTPUTS_VERIFIED.json', summary)
    print(json.dumps(summary, ensure_ascii=False, default=str), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    publish(parser.parse_args().output.resolve())
