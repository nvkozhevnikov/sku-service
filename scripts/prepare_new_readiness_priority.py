"""Offline bounded evidence plan. Does not import DB/network/matcher modules."""
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from universal_supplier.new_priority_cohort import select_priority, key

PREVIOUS = ROOT / 'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02'
OUT = ROOT / 'reports/RC_LOCAL/NEW_READINESS_PRIORITY_2026-10-02'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def aliases(model):
    # Retrieval-only: stripping the marketing series must never establish identity.
    return {key(model), key(re.sub(r'^(Laser|Blazer|Feeler|Sprinter)\s+', '', model, flags=re.I))}


def main():
    matching = PREVIOUS / 'ACCEPTED/MATCHING_ACCEPTED.json'
    rows = load(matching)['rows']
    assert len(rows) == 4407
    assert sum(r['classification'] == 'REVIEW' for r in rows) == 3642
    registry = ROOT.parent / 'sku-service-fresh-catalog-refresh-rc1/catalog_refresh/evidence/sterbrust/STERBRUST_REGISTRY_IDENTITY.jsonl'
    assert sha(registry) == '5d2e430bf54eb38dc8581f34318ee78b35cc3c793f8e9bd4a7465d8e414d16f3'
    index = {}
    ids = set()
    for line in registry.open(encoding='utf-8'):
        r = json.loads(line); ids.add(r['sterbrust_product_id'])
        fields = [r.get('model_raw'), r.get('name'), r.get('article_raw'), r.get('supplier_article_raw')]
        fields += [p.get('value_flat') for p in r.get('relevant_properties', [])]
        for value in fields:
            if value:
                index.setdefault(key(value), {})[r['sterbrust_product_id']] = {
                    k: r.get(k) for k in ('sterbrust_product_id', 'name', 'model_raw', 'brand_raw', 'product_url')}
    assert len(ids) == 59500
    pool = select_priority(rows, limit=4407, allow_section_pending=True)
    rejected, remaining = [], []
    for item in pool['priority']:
        tokens = aliases(item['model'])
        found = {}
        for token in tokens:
            for value, candidates in index.items():
                if token and token in value:
                    found.update(candidates)
        item['retrieval_only_model_aliases'] = sorted(tokens)
        item['canonical_alias_hits'] = list(found.values())
        if found:
            item['route'] = 'NOT_NEW_PRIORITY_CANONICAL_CANDIDATE_PRESENT'
            item['old_absence_advisory_superseded_for_priority_only'] = True
            rejected.append(item)
        else:
            item['absence']['verified'] = False
            item['absence']['additional_exact_alias_check_no_hits'] = True
            item['missing_gates'].append('POSITIVE_CANONICAL_ABSENCE_PROOF')
            item['route'] = 'NEW_EVIDENCE_CANDIDATE_NOT_CONFIRMED_NEW'
            remaining.append(item)
    # A table with selectable configurations is not a unique execution.
    deferred, eligible = [], []
    for item in remaining:
        props = item.get('observed_properties') or []
        ambiguous = any('опци' in p.get('name', '').lower() or 'модель' in p.get('name', '').lower()
                        and key(p.get('value')) != key(item['model']) for p in props)
        visible_execution_extra = any(p.get('name', '').lower() == 'технические характеристики'
            and key(p.get('value')).startswith(key(item['model']))
            and key(p.get('value')) != key(item['model']) for p in props)
        if ambiguous or visible_execution_extra or re.search(r'б/у', item['name'], re.I):
            item['missing_gates'].append('FULL_EXECUTION_OR_CONDITION_PROOF')
            deferred.append(item)
        else:
            eligible.append(item)
    batch = eligible[:20]
    ledger_path = ROOT / 'reports/RC_LOCAL/INTERVESP_FULL_2026-10-01/DETAIL_ENRICHMENT.json'
    captures = {r['url']: r for r in load(ledger_path)['rows'] if r.get('http_status') == 200}
    plan, no_get = [], []
    for item in batch:
        detail = captures.get(item['source_url'])
        listing = Path(item['evidence_ref'])
        saved = []
        if listing.is_file():
            saved.append({'kind': 'listing', 'path': str(listing), 'sha256': sha(listing)})
        if detail and Path(detail['evidence_ref']).is_file():
            p = Path(detail['evidence_ref']); saved.append({'kind': 'detail', 'path': str(p), 'sha256': sha(p)})
        wood = 'четырехсторон' in item['name'].lower()
        fields = (['visible full execution', 'spindle count and arrangement', 'max workpiece width/height',
                   'manufacturer article/document tying this exact execution to technical table'] if wood else
                  ['visible full model/execution and manufacturer', 'two independent geometry/kinematic anchors with units and roles',
                   'manufacturer article/document tying this exact execution to technical table'])
        task = {'source': item['source'], 'external_id': item['external_id'], 'supplier_url': item['source_url'],
                'supplier_document': None, 'document_status': 'NO_VERIFIED_DOCUMENT_URL_DO_NOT_INVENT',
                'already_saved_capture': 'YES' if saved else 'NO', 'saved_evidence': saved,
                'already_saved_detail': bool(any(p['kind'] == 'detail' for p in saved)),
                'required_fields': fields, 'missing_gates': item['missing_gates'],
                'section_task': 'Prove exact active section through current confirmed-only mapping; never infer from nearest category',
                'absence_task': 'Saved registry: inspect manufacturer/series/model/article variants and candidate descriptions; alias misses alone insufficient',
                'dedup_task': 'Check all five namespaces using full execution and independent anchors; alias retrieval never equivalence',
                'why_evidence_matters': 'Closes documented independent identity anchors; cannot alone close section/absence/dedup',
                'action': 'OFFLINE_SAVED_DETAIL_FIRST_NO_GET' if detail else 'OFFLINE_LISTING_FIRST_CONDITIONAL_DETAIL_ONLY_IF_FIELD_ABSENT',
                'http_authorized': False, 'expected_route': item['expected_route']}
        task['required_fields'] += ['Verify visible field including units/role: ' + p['name'] + ' = ' + p['value']
                                  for p in (item.get('observed_properties') or [])[:3]]
        plan.append(task)
        if detail:
            no_get.append(item['source_url'])
    OUT.mkdir(exist_ok=True)
    def save(name, value):
        (OUT/name).write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    save('NEW_READINESS_PRIORITY.json', batch)
    save('GROK_EXACT_EVIDENCE_PLAN.json', plan)
    save('MINIMAL_GROK_OFFLINE_BATCH.json', [r for r in plan if r['already_saved_detail']])
    save('NO_REPEAT_GET.json', no_get)
    save('CANONICAL_ALIAS_EXCLUSIONS.json', rejected)
    save('DEFERRED_EXECUTION_CONDITION.json', deferred)
    save('POOL_EXCLUSIONS.json', pool['excluded'])
    save('INPUT_SHA256.json', {str(p): sha(p) for p in [matching, registry, ledger_path, PREVIOUS/'CHECKPOINT.json']})
    summary = dict(source_rows=4407, Existing=506, READY_TO_CREATE_IDENTITY=0, Review=3642, Conflict=259,
                   current_review_screened=3642, scoped_absence_machine_pool=pool['eligible_count'],
                   canonical_alias_exclusions=len(rejected), eligible_evidence_candidates=len(eligible),
                   bounded_batch=len(batch), strict_near_new_with_absence_and_safe_section=0,
                   one_gate=0, two_gates=0, missing_three_or_more=len(batch),
                   saved_detail_reuse=len(no_get), conditional_detail_upper_bound=len(batch)-len(no_get),
                   minimal_grok_offline_batch=len(no_get), recommended_http_batch=0,
                   HTTP=0, SQL=0, global_matching=0, actual_promotions=0)
    save('SUMMARY.json', summary)
    save('CHECKPOINT.json', dict(status='NEW_PRIORITY_OFFLINE_PROPOSAL',
         resume_from='reports/RC_LOCAL/NEW_READINESS_PRIORITY_2026-10-02/CHECKPOINT.json',
         authoritative_matching=str(matching), authoritative_sha256=sha(matching), summary=summary,
         next_action='Grok offline saved-evidence extraction on exact bounded batch; no GET before specific missing-field proof',
         completed_do_not_repeat=['current Review priority screening', '59500 scoped alias exclusion'],
         matching_unchanged=True, final_publication=False))
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == '__main__':
    main()
