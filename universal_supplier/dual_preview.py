"""Two local preview artifacts over existing identity, selection and XML contracts.

No import/write/matching, no ESOL format claim. Every exclusion is accounted for.
"""
from collections import Counter, defaultdict
from copy import deepcopy
from dataclasses import replace
from datetime import datetime,timezone
from decimal import Decimal
import hashlib,json,os,re
from pathlib import Path
from xml.etree import ElementTree as ET
from sterbrust_matching.normalization import normalize_brand,normalize_text
from .matching_policy_v2 import normalize_identity_model
from .offer_selection import OfferSelectionPolicy,evaluate_offers
from .commercial_freshness import assess
from .control_plane.supplier_automation import settings_for,digest
from .xml_export import _append,_product_element,FALSE_MATCH_GUARDS

NOTICE='LOCAL PREVIEW ONLY / DIRECT IMPORT FORBIDDEN / BLOCKED_IMPORT_CONTRACT_EVIDENCE'
NAMES={'updates':'sterbrust_updates.preview.xml','new_products':'sterbrust_new_products.preview.xml'}


def identity(row):
    return (normalize_brand(row.get('brand_raw')),normalize_identity_model(row.get('own_model'),row.get('brand_raw')),
            str(row.get('product_kind') or ''))


def build(snapshot, *, as_of=None, baseline=None):
    now=as_of or snapshot['as_of']
    if now.tzinfo is None:raise ValueError('PREVIEW_AS_OF_REQUIRES_TIMEZONE')
    if snapshot.get('complete') is not True:raise ValueError('PREVIEW_SNAPSHOT_INCOMPLETE')
    suppliers={r['code']:r for r in snapshot['suppliers']}
    cfg={code:settings_for(s) for code,s in suppliers.items()}
    records=sorted(snapshot['records'],key=lambda r:(r['supplier_code'],str(r['external_id']),r['offer_id'] or 0))
    byoffer={r['offer_id']:r for r in records if r['offer_id'] is not None}
    bysource=defaultdict(list)
    for r in records:bysource[r['source_product_id']].append(r)
    freshness={r['offer_id']:assess(r,cfg[r['supplier_code']],now) for r in records if r['offer_id'] is not None}
    policy=OfferSelectionPolicy.load(Path(__file__).resolve().parents[1]/'config/offer_selection.json')
    # A committed per-card capture is the preview freshness boundary. Legacy Stage5
    # full-crawl rules are unchanged; this explicitly named selection profile does
    # not make a receipt wait for every phase of an unrelated batch to complete.
    policy=replace(policy,version=policy.version+'/receipt-preview-v2',
        freshness={**policy.freshness,'require_latest_successful_full_crawl':False})
    from .preview_coverage import trace,summarize,funnels,regression
    from sterbrust_matching.product_identity import ACCESSORY_FAMILIES,TAXON_BY_KIND,REFERENCE_MODEL,classify_model_role
    rejected={'updates':[],'new_products':[]};updates=[];new=[];traces=[]
    audit={'updates':[],'new_products':[]};update_by_offer={};new_by_source={}
    def reject(kind,item,reason,category=None):
        if reason not in item['reasons']:item['reasons'].append(reason)
        item['primary_reason']=item['reasons'][0]
        if category:item['category']=category
    false={(s,e) for s,e,_ in FALSE_MATCH_GUARDS}|{('optimum','305')}
    confirmed_ids={str(r['sterbrust_product_id']) for r in snapshot['registry']}
    registry_keys={(normalize_brand(r.get('brand_raw')),normalize_identity_model(r.get('model_raw'),r.get('brand_raw')))
                   for r in snapshot['registry'] if r.get('model_raw')}
    emitted=set();selection_candidates={c.offer_id:c for cs in snapshot['offer_candidates'].values() for c in cs}
    eligible_by_catalog=defaultdict(list)
    for row in records:
        reasons=[];warnings=[];code=row['supplier_code'];candidate=selection_candidates.get(row['offer_id'])
        f=freshness.get(row['offer_id'],{})
        price=f.get('price',{}).get('state')=='fresh';stock=f.get('availability',{}).get('state')=='fresh'
        if code=='beka_mak_tr':reasons.append('REFERENCE_ONLY')
        if (code,str(row['external_id'])) in false:reasons.append('KNOWN_FALSE_MATCH')
        if row.get('identity_confirmed') is not True or not row.get('confirmed_sterbrust_id'):reasons.append('IDENTITY_NOT_CONFIRMED')
        if candidate is None:reasons.append('NO_CURRENT_CONFIRMED_SELECTION_CANDIDATE')
        if row.get('blocking_quarantine'):reasons.append('UNRESOLVED_SOURCE_QUARANTINE')
        if (row.get('blocking_review') or row.get('match_conflicts') or row.get('conflicts')
                or row.get('match_status') in ('REVIEW','CONFLICT')):reasons.append('CURRENT_REVIEW_OR_CONFLICT')
        # Effective accepted/manual identity is durable authority, not a TTL field.
        if row.get('matching_current_for_capture') is not True:warnings.append('MATCHING_AUDIT_OLDER_THAN_OBSERVATION')
        if not price:warnings.append('PRICE_'+f.get('price',{}).get('state','unknown').upper())
        if not stock:warnings.append('AVAILABILITY_'+f.get('availability',{}).get('state','unknown').upper())
        if not price and not stock:reasons.append('NO_FRESH_COMMERCIAL_FIELD')
        if candidate is not None:
            projected=replace(candidate,price=Decimal(str(row['observed_price'])) if price else None,
                currency=row.get('observed_currency') if price else None,
                availability=row['observed_availability'] if stock else 'unknown',
                availability_raw=row.get('observed_availability_raw') if stock else None,
                # Never fabricate stock from missing quantity. A real contradictory
                # current observation still participates in the native review rule.
                quantity=row.get('observed_quantity',candidate.quantity) if stock else None)
            single=evaluate_offers(candidate.catalog_product_id,[projected],policy)
            for native in single.evidence['candidate_offers'][0]['exclusion_reasons']:
                reasons.append('SELECTION_'+native.upper())
            if single.selection_status=='review':reasons.append('SELECTION_'+single.evidence['selection_reason'].upper())
        item=trace(row,reasons,warnings=warnings);item['selection_eligible']=not item['reasons']
        update_by_offer[row['offer_id']]=item;audit['updates'].append(item)
        if not reasons:eligible_by_catalog[candidate.catalog_product_id].append(projected)
    for catalog_id,eligible in sorted(eligible_by_catalog.items()):
        decision=evaluate_offers(catalog_id,eligible,policy)
        if decision.selection_status!='selected':
            for c in eligible:reject('updates',update_by_offer[c.offer_id],'SELECTION_'+decision.evidence['selection_reason'].upper())
            continue
        row=byoffer[decision.selected_offer_id];stb=str(row.get('confirmed_sterbrust_id') or '')
        if not stb or stb not in confirmed_ids:raise ValueError('PREVIEW_CANONICAL_ID_NOT_IN_REGISTRY')
        if stb in emitted:raise ValueError('PREVIEW_DUPLICATE_STERBRUST_ID')
        emitted.add(stb)
        f=freshness[row['offer_id']];values={'sterbrust_product_id':stb}
        if f['price']['state']=='fresh':values.update(price=row['observed_price'],currency=row['observed_currency'])
        if f['availability']['state']=='fresh':values['availability']=row['observed_availability']
        updates.append(values);update_by_offer[row['offer_id']]['selected']=True
        traces.append({'sterbrust_product_id':stb,'supplier':row['supplier_code'],'external_id':row['external_id'],
            'capture_id':row['capture_id'],'fingerprint':row['capture_fingerprint'],
            'selection':decision.evidence,'selection_scope':'READ_ONLY_RECEIPT_PLAN_NOT_PERSISTED',
            'persisted_selection_at':row.get('last_selection_at'),
            'fields_emitted':sorted(set(values)-{'sterbrust_product_id'}),'freshness':f})
        for c in eligible:
            if c.offer_id!=decision.selected_offer_id:reject('updates',update_by_offer[c.offer_id],'LOWER_RANKED_ELIGIBLE_OFFER')
    candidate_groups=defaultdict(list);model_properties=defaultdict(set)
    for source_id,rows in sorted(bysource.items()):
        row=rows[0];code=row['supplier_code'];key=identity(row)
        reasons=[];warnings=[]
        if code=='beka_mak_tr':reasons.append('REFERENCE_ONLY')
        if (code,str(row['external_id'])) in false:reasons.append('KNOWN_FALSE_MATCH')
        if row.get('blocking_quarantine'):reasons.append('UNRESOLVED_SOURCE_QUARANTINE')
        if row.get('catalog_product_id') is not None or row.get('confirmed_sterbrust_id'):reasons.append('EXISTING_CONFIRMED_OR_PHYSICAL_LINK')
        if row.get('decision')!='SAFE_NEW_PRODUCT_CANDIDATE' or row.get('match_status')!='NEW_CANDIDATE':reasons.append('NOT_SAFE_NEW_MATCHING_DECISION')
        if row.get('matching_current_for_capture') is not True:reasons.append('MATCHING_PENDING_OR_STALE')
        # A NEW staging case is not a REVIEW_EXISTING verdict. It stays open and
        # import-forbidden; no human decision is changed by a local candidate preview.
        staging=False
        if (row.get('review_automatic_decision')=='SAFE_NEW_PRODUCT_CANDIDATE'
                and row.get('review_lifecycle') in ('OPEN','ASSIGNED')
                and row.get('latest_manual_decision') is None):
            from .control_plane.admin_store import identity_snapshot,canonical_fingerprint
            current=identity_snapshot({**row,'supplier':code})
            staging=canonical_fingerprint(current)==row.get('review_identity_fingerprint')
        if ((row.get('blocking_review') and not staging) or row.get('match_conflicts') or row.get('conflicts')):reasons.append('REVIEW_OR_CONFLICT')
        if row.get('latest_manual_decision') not in (None,'MANUAL_CONFIRMED_NEW','MARKED_REVIEWED'):
            reasons.append('OPERATOR_DECISION_REQUIRES_REVIEW')
        if staging:warnings.append('NEW_STAGING_CASE_REMAINS_OPEN')
        if not suppliers[code]['enabled']:warnings.append('COLLECTION_DISABLED_NOT_ACTIVATED')
        family=TAXON_BY_KIND.get(key[2]);role=classify_model_role(row.get('name') or '',row.get('category') or '',row.get('own_model') or '')
        sufficient=bool(row.get('external_id_is_stable') and all(key) and key[2] not in ('unknown','accessory')
            and not (family and family.family in ACCESSORY_FAMILIES) and role.role!=REFERENCE_MODEL)
        if not sufficient:reasons.append('UNPROVEN_SOURCE_IDENTITY')
        if (row.get('extraction_evidence') or {}).get('variant_or_group') is True:reasons.append('MULTI_VARIANT_SOURCE_REQUIRES_REVIEW')
        if key[:2] in registry_keys:reasons.append('REGISTRY_IDENTITY_EXISTS')
        if len(rows)!=1:reasons.append('MULTIPLE_OFFERS_REQUIRE_REVIEW')
        f=freshness.get(row['offer_id'],{})
        if f.get('content',{}).get('state')!='fresh':reasons.append('STALE_OR_UNVERIFIED_CONTENT')
        if not row.get('category'):warnings.append('MISSING_CATEGORY_NEEDS_ENRICHMENT')
        if f.get('price',{}).get('state')!='fresh':warnings.append('STALE_OR_UNKNOWN_PRICE_OMITTED')
        if f.get('availability',{}).get('state')!='fresh':warnings.append('STALE_OR_UNKNOWN_AVAILABILITY_OMITTED')
        category=('CONFLICT' if row.get('match_status')=='CONFLICT' else
            'REVIEW' if row.get('match_status')=='REVIEW' or 'REVIEW_OR_CONFLICT' in reasons else
            'duplicate' if 'EXISTING_CONFIRMED_OR_PHYSICAL_LINK' in reasons or 'REGISTRY_IDENTITY_EXISTS' in reasons else
            'needs_enrichment' if not sufficient else 'excluded' if reasons else 'eligible_new_preview')
        item=trace(row,reasons,warnings=warnings,category=category);item['identity_sufficient']=sufficient
        audit['new_products'].append(item);new_by_source[source_id]=item
        if reasons:continue
        characteristics=row.get('identity_characteristics') or {}
        props=digest({k:v for k,v in characteristics.items() if k not in
            ('category','candidate_sterbrust_ids','candidate_evidence','matching_evidence')})
        model_properties[key].add(props);candidate_groups[(key,props)].append(row)
    for (key,props),rows in sorted(candidate_groups.items()):
        if len(model_properties[key])>1:
            for row in rows:reject('new_products',new_by_source[row['source_product_id']],'CROSS_SOURCE_PROPERTIES_REQUIRE_REVIEW','REVIEW')
            continue
        if len({normalize_text(r.get('name') or '') for r in rows})>1:
            # A persisted model token may omit an execution printed in the full
            # title. Do not merge aliases on that token alone (e.g. base vs NC).
            for row in rows:reject('new_products',new_by_source[row['source_product_id']],
                'DIFFERENT_SOURCE_TITLES_REQUIRE_EXECUTION_REVIEW','REVIEW')
            continue
        rows.sort(key=lambda r:(cfg[r['supplier_code']]['priority'],r['supplier_code'],str(r['external_id'])))
        row=deepcopy(rows[0]);f=freshness[row['offer_id']]
        row.update(price=row['observed_price'] if f['price']['state']=='fresh' else None,
                   currency=row['observed_currency'] if f['price']['state']=='fresh' else None,
                   availability_normalized=row['observed_availability'] if f['availability']['state']=='fresh' else None,
                   availability_raw=row.get('observed_availability_raw') if f['availability']['state']=='fresh' else None,
                   old_price=None,quantity=None,price_raw=None,
                   price_type=row.get('observed_price_type') if f['price']['state']=='fresh' else None,
                   price_source=row.get('observed_price_source',row.get('price_source')) if f['price']['state']=='fresh' else None)
        candidate_id='NEWC-'+digest([key,props])[:24].upper()
        new.append({'id':candidate_id,'row':row,'aliases':[{'supplier':r['supplier_code'],'external_id':r['external_id']} for r in rows]})
        for alias in rows:
            item=new_by_source[alias['source_product_id']];item['selected']=True;item['candidate_id']=candidate_id
            if alias['source_product_id']!=row['source_product_id']:item['category']='deduplicated_alias'
    roots={kind:ET.Element('sterbrust_preview',{'kind':kind,'import_allowed':'false',
        'generated_at':now.isoformat(),'contract':'BLOCKED_IMPORT_CONTRACT_EVIDENCE',
        'status':('PREVIEW_ONLY' if (updates if kind=='updates' else new) else 'NOT_READY')}) for kind in NAMES}
    for root in roots.values():root.append(ET.Comment(NOTICE))
    for row in sorted(updates,key=lambda r:r['sterbrust_product_id']):
        node=ET.SubElement(roots['updates'],'product',{'sterbrust_product_id':row['sterbrust_product_id']})
        for key in ('price','currency','availability'):
            if key in row:_append(node,key,row[key])
    for item in new:
        row=item['row'];f=freshness[row['offer_id']];details=snapshot.get('details',{}).get(row['source_product_id'],{})
        node=_product_element(row,details);node.set('candidate_id',item['id'])
        needs_enrichment=not row.get('category') or f['price']['state']!='fresh' or f['availability']['state']!='fresh'
        ET.SubElement(node,'preview_readiness',{'status':'needs_enrichment' if needs_enrichment else 'eligible_new_preview','import_allowed':'false',
            'requires_operator_review':'true','collection_enabled':str(bool(suppliers[row['supplier_code']]['enabled'])).lower()})
        commercial=node.find('commercial')
        for child in list(commercial):
            if child.text is None or child.tag in ('old_price','quantity','price_raw'):
                commercial.remove(child)
        # Price-type metadata from an old projection is not a current observation.
        if f['price']['state']!='fresh':
            for tag in ('price_type','price_source'):
                child=commercial.find(tag)
                if child is not None:commercial.remove(child)
        _append(node,'supplier_aliases',item['aliases']);roots['new_products'].append(node)
    data={kind:ET.tostring(root,encoding='utf-8',xml_declaration=True) for kind,root in roots.items()}
    for kind,raw in data.items():
        parsed=ET.fromstring(raw)
        if parsed.attrib.get('import_allowed')!='false':raise ValueError('PREVIEW_IMPORT_GATE_LOST')
        if kind=='updates' and any(c.tag not in ('price','currency','availability') for n in parsed for c in n):
            raise ValueError('PREVIEW_NONCOMMERCIAL_UPDATE_FIELD')
    for kind,items in audit.items():
        rejected[kind]=[{**item,'reason':item['primary_reason']} for item in items if item['reasons']]
    manifest={'generated_at':now.isoformat(),'contract':NOTICE,'import_allowed':False,
        'counts':{'updates':len(updates),'new_products':len(new)},'considered_sources':len(suppliers),
        'considered_products':len(bysource),'considered_offers':len(records),
        'excluded':rejected,'exclusion_counts':{k:dict(Counter(r['reason'] for r in v)) for k,v in rejected.items()},
        'freshness':freshness,'selection_trace':traces,'coverage':snapshot.get('coverage',{}),
        'record_trace':audit,'coverage_by_supplier':{kind:summarize(items,suppliers) for kind,items in audit.items()},
        'funnels_by_supplier':funnels(records,suppliers,cfg,now,audit['updates'],audit['new_products']),
        'selection_plan':{'scope':'READ_ONLY_NOT_PERSISTED','policy_version':policy.version,
            'global_batch_completion_required':False,'canonical_groups':len(eligible_by_catalog)},
        'included_sources':{kind:sorted(r['source_product_id'] for r in rows if r['selected']) for kind,rows in audit.items()},
        'checksums':{k:hashlib.sha256(v).hexdigest() for k,v in data.items()}}
    manifest['quality_gate']=regression(baseline,manifest,audit)
    manifest['trusted_current_for_import']=False  # Contract evidence is still blocked.
    manifest['new_readiness_counts']=dict(Counter(node.find('preview_readiness').get('status')
        for node in roots['new_products'] if node.tag=='product'))
    manifest['version']=digest(manifest)
    return data,manifest


def publish(folder, data, manifest):
    """Validated immutable version directory + atomic latest pointer; no DB writes."""
    from .control_plane.source_hourly import atomic_text
    root=Path(folder);version=manifest['version']
    if (root/'latest.json').exists():
        previous=latest(root)
        for kind in NAMES:
            if previous['counts'][kind]>0 and manifest['counts'][kind]==0:
                explained=any(r['kind']==kind and r['all_previous_records_explained'] for r in
                    manifest.get('quality_gate',{}).get('regressions',[]))
                if not explained:raise ValueError('PREVIEW_COVERAGE_REGRESSION_REQUIRES_EXPLANATION')
    if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('PREVIEW_VERSION_INVALID')
    if digest({k:v for k,v in manifest.items() if k!='version'})!=version:raise ValueError('PREVIEW_MANIFEST_VERSION_CHANGED')
    # Keep Windows paths bounded; the full immutable ID remains in the manifest.
    # A prefix collision is rejected, never overwritten or silently aliased.
    directory=version[:16];target=root/'versions'/directory;target.mkdir(parents=True,exist_ok=True)
    if (target/'manifest.json').exists() and json.loads((target/'manifest.json').read_text(encoding='utf-8'))['version']!=version:
        raise ValueError('PREVIEW_VERSION_DIRECTORY_COLLISION')
    for kind,name in NAMES.items():
        raw=data[kind]
        ET.fromstring(raw)
        if hashlib.sha256(raw).hexdigest()!=manifest['checksums'][kind]:raise ValueError('PREVIEW_CHECKSUM_MISMATCH')
        path=target/name
        if path.exists() and path.read_bytes()!=raw:raise ValueError('PREVIEW_IMMUTABLE_VERSION_CHANGED')
        if not path.exists():atomic_text(path,raw.decode('utf-8'))
    atomic_text(target/'manifest.json',json.dumps(manifest,ensure_ascii=False,indent=2,default=str))
    atomic_text(root/'latest.json',json.dumps({'version':version,'directory':directory,'manifest_sha256':hashlib.sha256((target/'manifest.json').read_bytes()).hexdigest()}))
    return target


def latest(folder, kind=None):
    root=Path(folder);pointer=json.loads((root/'latest.json').read_text(encoding='utf-8'))
    version=pointer['version']
    if not re.fullmatch('[a-f0-9]{64}',version):raise ValueError('PREVIEW_VERSION_INVALID')
    directory=pointer.get('directory',version)
    if directory not in (version,version[:16]):raise ValueError('PREVIEW_DIRECTORY_INVALID')
    target=root/'versions'/directory;raw=(target/'manifest.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=pointer['manifest_sha256']:raise ValueError('PREVIEW_MANIFEST_CHANGED')
    manifest=json.loads(raw)
    if manifest['version']!=version:raise ValueError('PREVIEW_VERSION_CHANGED')
    if kind is None:return manifest
    if kind not in NAMES:raise ValueError('PREVIEW_KIND_INVALID')
    content=(target/NAMES[kind]).read_bytes()
    if hashlib.sha256(content).hexdigest()!=manifest['checksums'][kind]:raise ValueError('PREVIEW_CHECKSUM_MISMATCH')
    return content
