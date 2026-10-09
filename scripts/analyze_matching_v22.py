"""Pure frozen-input inventory and V2.1 cohort discovery, never persistence."""
import argparse,hashlib,json,re
from collections import Counter,defaultdict
from pathlib import Path
from universal_supplier.matching_policy_v2 import PolicyV2Index,canonical_view_v2,identity_model,normalize_identity_model
from sterbrust_matching.normalization import normalize_brand,extract_model
from sterbrust_matching.product_identity import classify_product_kind

ROOT=Path(__file__).resolve().parents[1];OUT=ROOT/'reports/MATCHING_V2_2_AI_REVIEW'
KAMI=ROOT.parent/'sku-service-kami/reports'
REGISTRY=KAMI/'KAMI_INTEGRATION_2026-10-05/CANONICAL_CURRENT/STERBRUST_REGISTRY.jsonl'
REGISTRY_SHA='40e4f2276c7d58754cfc755ca08453994193419cba6843db3170ec640007fd65'

def save(n,v):
    (OUT/n).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str)+'\n',encoding='utf-8')

def read_lines(name):
    with (OUT/name).open(encoding='utf-8') as f:
        for line in f:yield json.loads(line)

def source_views():
    properties=defaultdict(list)
    for p in read_lines('SOURCE_PROPERTIES.jsonl'):properties[p['source_product_id']].append(p)
    # Original pinned KAMI own-card views retain exact model/execution and role
    # extraction, not hidden SKU/slug guesses. Current SQL source facts are checked.
    frozen=json.loads((KAMI/'KAMI_MATCHING_POLICY_V2_2026-10-08/V2_1_QA_INPUTS.json').read_bytes())['sources']
    rows=[]
    for r in read_lines('SOURCE_ROWS.jsonl'):
        raw=r['raw_data'];s=r['supplier'];ext=r['external_id']
        if s=='kami':
            view=dict(frozen[ext]);assert view['name']==r['name']
            assert raw.get('full_model','')==view['model'] or raw.get('card_kind')=='AMBIGUOUS'
        else:
            identity=raw.get('source_identity',{});content=raw.get('source_content',{})
            refresh=raw.get('_catalog_refresh',{});current=refresh.get('current',{})
            groups=raw.get('_stage3b',{}).get('group_values',{});given=groups.get('identity',{})
            props=[]
            for p in properties[r['id']]:
                props.append(dict(name=p['raw_name'],value=p['raw_value'],unit=p.get('raw_unit') or '',
                    role=(p.get('raw_metadata') or {}).get('role',''),position=p['position'],provenance='frozen_source_SQL_property'))
            # Passive commercial own-card characteristics live in preserved
            # source_content, not necessarily in the normalized SQL detail table.
            seen={(p['name'],p['value'],p.get('role','')) for p in props}
            for item in content.get('technical_properties',[]):
                if isinstance(item,(list,tuple)) and len(item)==2:
                    prop=dict(name=str(item[0]),value=str(item[1]),unit='',role='',provenance='saved_supplier_own_technical_table')
                elif isinstance(item,dict):
                    prop=dict(name=item.get('name') or item.get('raw_name') or '',value=item.get('value') or item.get('raw_value') or '',
                              unit=item.get('unit') or '',role=item.get('role') or '',provenance='saved_supplier_own_technical_table')
                else:continue
                key=(prop['name'],prop['value'],prop['role'])
                if prop['name'] and key not in seen:props.append(prop);seen.add(key)
            source_category=content.get('source_category') or current.get('category_name') or ''
            if not source_category:
                source_category=' / '.join(c['name'] for c in groups.get('categories',[]) if c.get('name'))
            brand=r.get('brand_raw') or given.get('brand') or identity.get('brand') or ''
            model=identity.get('raw_model') or ''
            if not brand:
                for p in props:
                    if re.fullmatch(r'бренд|марка|производитель|brand|manufacturer',p['name'].strip(),re.I) and p['value']:
                        brand=p['value'];break
            # Proven manufacturer site namespace, not a global arbitrary alias.
            if not brand and s in ('beka_mak','beka_mak_tr'):brand='Beka-Mak'
            if not model:
                own=[p['value'] for p in props if re.fullmatch(r'модель(?: станка)?|model',p['name'].strip(),re.I) and p['value']]
                model=own[0] if len(set(own))==1 else ''
            if not model:
                # Source-only visible title extraction. Never borrow target model
                # or use metadata article/URL slug to overwrite sold execution.
                title=r['name']
                if brand:title=re.sub(re.escape(brand),' ',title,flags=re.I)
                model=extract_model(title)
            evidence_ref=refresh.get('evidence_ref') or (raw.get('commercial_capture') or {}).get('evidence_ref') or 'frozen_SQL_source:'+str(r['id'])
            groups_by_name=defaultdict(list)
            for prop in props:groups_by_name[prop['name']].append(prop['value'])
            view=dict(source=s,external_id=ext,name=r['name'],brand=brand,model=model,execution=model,
                source_category=source_category,source_url=r['source_url'],observed_properties=props,
                properties={name:values[0] for name,values in groups_by_name.items() if len(set(values))==1},evidence_ref=evidence_ref,
                product_kind=classify_product_kind(r['name'],source_category).product_kind)
        view.update(source_product_id=r['id'],article=r['sku'],raw_source_identity=raw.get('source_identity',{}),
            persisted_classification=r['current_match_status'] or 'NOT_MATCHED',
            persisted_auto_accepted=r.get('auto_accepted') is True,
            current_candidate_id=r.get('proposed_sterbrust_product_id') or r.get('best_sterbrust_id'),
            current_candidate_name=r.get('proposed_name'),current_candidate_score=r.get('confidence_score'),
            current_reasons=r.get('decision_reason'),current_match_evidence=r.get('match_evidence'),
            current_match_conflicts=r.get('match_conflicts'),linked_sterbrust_product_id=r.get('linked_sterbrust_product_id'),
            raw_model=raw.get('full_model') or (raw.get('source_identity') or {}).get('raw_model') or '')
        rows.append(view)
    assert len(rows)==9671 and not any(r['source']=='vekprom' for r in rows)
    return rows

def targets_and_offsets():
    targets={};offsets={};digest=hashlib.sha256()
    with REGISTRY.open('rb') as stream:
        while True:
            offset=stream.tell();line=stream.readline()
            if not line:break
            digest.update(line);r=json.loads(line);pid=str(r['sterbrust_product_id'])
            assert pid not in targets
            targets[pid]=dict(sterbrust_product_id=pid,name=r['name'],model=r.get('model_raw') or '',
                brand=r.get('brand_raw') or '',manufacturer=r.get('manufacturer_raw') or '',active=r['active'],
                source_category=r.get('category_path') or '',observed_properties=[],properties={},
                source_url=r.get('product_url'),snapshot_hash=r.get('snapshot_hash'))
            offsets[pid]=offset
    assert len(targets)==59500 and digest.hexdigest()==REGISTRY_SHA
    return targets,offsets

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--reuse-canonical-views',action='store_true');args=parser.parse_args()
    rows=source_views()
    if args.reuse_canonical_views:
        targets=json.loads((OUT/'CANONICAL_VIEWS.json').read_bytes());assert len(targets)==59500
        offsets=None
    else:targets,offsets=targets_and_offsets()
    index=PolicyV2Index(targets)
    needed={pid for r in rows for pid in index.retrieve(r)}
    needed.update(str(r['current_candidate_id']) for r in rows if r.get('current_candidate_id') in targets)
    # Source-only model-family retrieval enriches future cohort diagnostics,
    # never acceptance. Candidate space retains full execution/suffixes.
    bymodel=defaultdict(set)
    for pid,t in targets.items():
        if t['active']=='Y':bymodel[identity_model(t)].add(pid)
    for r in rows:
        ids=bymodel.get(identity_model(r),set())
        if len(ids)<=12:needed.update(ids)
    if offsets is not None:
        with REGISTRY.open('rb') as stream:
            for pid in sorted(needed):
                stream.seek(offsets[pid]);record=json.loads(stream.readline())
                rich=canonical_view_v2(record);rich.update(manufacturer=record.get('manufacturer_raw') or '',source_url=record.get('product_url'))
                targets[pid]=rich
    index=PolicyV2Index(targets);summary=defaultdict(Counter);cohorts=Counter();output=[]
    for r in rows:
        audit=index.evaluate(r)
        classification='EXISTING_CONFIRMED' if audit['confirmed'] else 'REVIEW'
        # Historical source factual conflicts remain explicit at proposal layer;
        # they are never erased by failure to retrieve an exact candidate.
        if r['persisted_classification']=='CONFLICT':classification='CONFLICT'
        result={**r,'classification_v21':classification,'audit_v21':audit}
        output.append(result);summary[r['source']]['Total']+=1;summary[r['source']][classification]+=1
        if classification!='EXISTING_CONFIRMED':
            summary[r['source']]['with_candidate' if audit['retrieval_ids'] or r.get('current_candidate_id') else 'without_candidate']+=1
            cohorts.update(audit['blocking_reasons'])
    save('SOURCE_VIEWS.json',rows)
    save('CANONICAL_VIEWS.json',targets)
    save('V21_BEFORE.json',{'rows':output,'summary':{s:dict(c) for s,c in summary.items()},'cohorts':dict(cohorts),
        'full_registry_count':59500,'full_registry_sha256':REGISTRY_SHA,'canonical_rich_cards':len(needed),
        'SQL_writes':0,'HTTP':0,'note':'Common-policy V2.1 dry-run baseline, separate from persisted operational classifications'})
    save('CHECKPOINT.json',{'status':'V21_OFFLINE_COHORTS_READY','resume_from':'V21_BEFORE.json; mine actual cohorts, no reexport/reparse',
        'DB_writes':0,'HTTP':0,'active_55452_access':0,'Vekprom_manipulation':0})
    print(json.dumps({'summary':{s:dict(c) for s,c in summary.items()},'cohorts':dict(cohorts),'rich':len(needed)}))

if __name__=='__main__':main()
