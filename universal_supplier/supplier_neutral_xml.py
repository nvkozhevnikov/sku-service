"""Supplier-neutral local snapshot, NOT a Bitrix/ESOL import contract."""
from decimal import Decimal
import xml.etree.ElementTree as ET


def neutral_snapshot_xml(records, *, matching=None, generated_at='', enrichment_complete=False):
    decisions={(r['source'],str(r['external_id'])):r for r in (matching or [])}
    if len(decisions)!=len(matching or []): raise ValueError('Duplicate proposal source identity')
    root=ET.Element('supplier_catalog',{'schema':'universal-supplier-neutral-v1',
        'generated_at':generated_at,'enrichment_complete':str(bool(enrichment_complete)).lower(),
        'esol_import_payload':'false'})
    sources=ET.SubElement(root,'source_records')
    seen=set()
    for record in sorted(records,key=lambda r:(r['supplier_code'],str(r['external_id']))):
        identity=(record['supplier_code'],str(record['external_id']))
        if identity in seen: raise ValueError('Duplicate supplier-scoped source identity')
        seen.add(identity)
        decision=decisions.get(identity,{})
        raw=record.get('raw_data') or {}
        content=raw.get('source_content') or {}
        state=record.get('price_state') or 'missing'
        node=ET.SubElement(sources,'source_product',{'supplier':identity[0],'external_id':identity[1],
            'classification':decision.get('classification','NOT_MATCHED'),
            'readiness':decision.get('readiness',{}).get('state','NOT_EVALUATED')})
        for key,value in (('name',record.get('name')),('url',record.get('canonical_url') or record.get('source_url')),
                          ('model',raw.get('source_identity',{}).get('raw_model')),
                          ('brand',raw.get('source_identity',{}).get('brand') or record.get('brand_raw')),
                          ('manufacturer',record.get('manufacturer_raw')),
                          ('source_category',content.get('source_category'))):
            if value: ET.SubElement(node,key).text=str(value)
        if decision.get('classification')=='EXISTING_CONFIRMED':
            product_id=str(decision.get('sterbrust_product_id') or '')
            if not product_id.isdigit(): raise ValueError('Existing requires real numeric Sterbrust Product ID')
            ET.SubElement(node,'sterbrust_product_id').text=product_id
            if decision.get('sterbrust_name'):
                ET.SubElement(node,'sterbrust_name').text=str(decision['sterbrust_name'])
        # NEW identifiers live in a separate namespace; never export them as a
        # Sterbrust Product ID, XML_ID or importer update key.
        for key in ('new_candidate_id','new_group_id'):
            value=decision.get(key)
            if value: ET.SubElement(node,key).text=str(value)
        if decision.get('readiness',{}).get('identity_ready'):
            section_id=str(decision.get('proposed_section_id') or '')
            if not section_id.isdigit(): raise ValueError('Ready NEW requires actual proposed SECTION_ID')
            ET.SubElement(node,'proposed_section',{'section_id':section_id,
                'path':str(decision.get('proposed_section_path') or ''),
                'scope':'verified_saved_section_evidence_not_applied'})
        commercial=ET.SubElement(node,'commercial',{'price_state':state,
            'availability':record.get('availability_normalized') or 'unknown',
            'price_role':'supplier_observation_not_sterbrust_sale_price'})
        price=record.get('price')
        if state=='numeric_public':
            if price is None or Decimal(str(price))<=0 or not record.get('currency'):
                raise ValueError('Numeric public price lacks positive value/currency')
            ET.SubElement(commercial,'price',{'currency':str(record['currency'])}).text=str(price)
        elif price is not None: raise ValueError('Non-numeric state must have NULL price')
        if record.get('source_freshness'):
            commercial.set('source_freshness', str(record['source_freshness']))
        if record.get('historical_offer_price') is not None:
            ET.SubElement(commercial, 'historical_offer_price', {
                'currency': str(record.get('historical_offer_currency') or ''),
                'price_role': 'historical_supplier_snapshot_unverified_current_role',
                'last_success_at': str(record.get('last_success_at') or '')
            }).text = str(record['historical_offer_price'])
        if not content.get('description_text') and record.get('description_text'):
            ET.SubElement(node,'description').text=str(record['description_text'])
        if content.get('description_text'): ET.SubElement(node,'description').text=content['description_text']
        props=ET.SubElement(node,'properties')
        for name,value in content.get('technical_properties',[]):
            ET.SubElement(props,'property',{'name':str(name)}).text=str(value)
        images=ET.SubElement(node,'images')
        for value in content.get('source_images',[]): ET.SubElement(images,'image',{'url':str(value)})
        docs=ET.SubElement(node,'documents')
        for doc in content.get('source_documents',[]):
            ET.SubElement(docs,'document',{'url':str(doc['url']),'title':str(doc.get('title') or '')})
        proof = raw.get('enrichment_evidence') or {}
        ET.SubElement(node, 'enrichment_checks', {
            key: str(bool(proof.get(key + '_checked'))).lower()
            for key in ('description', 'characteristics', 'images', 'documents')})
        provenance=raw.get('commercial_capture') or {}
        ET.SubElement(node,'provenance',{'observed_at':str(record.get('observed_at') or ''),
            'evidence_ref':str(provenance.get('evidence_ref') or ''),
            'evidence_sha256':str(provenance.get('evidence_sha256') or ''),
            'capture_scope':str(provenance.get('capture_scope') or 'detail'),
            'response_hash_basis':str(provenance.get('response_hash_basis') or 'raw_response')})
    root.set('source_product_count',str(len(seen)))
    # The full registry also describes ready canonical-new groups once, with
    # references to independent source records rather than duplicate cards.
    grouped={}
    for identity in sorted(seen):
        decision=decisions.get(identity,{})
        if decision.get('new_group_id'):
            grouped.setdefault(decision['new_group_id'],[]).append((identity,decision))
    group_nodes=ET.SubElement(root,'canonical_new_groups')
    for group_id,members in sorted(grouped.items()):
        if not all(d.get('readiness',{}).get('identity_ready') and
                   d.get('classification')=='NEW_CANDIDATE' for _,d in members): continue
        sections={str(d.get('proposed_section_id') or '') for _,d in members}
        if len(sections)!=1 or not next(iter(sections)).isdigit():
            raise ValueError('Ready canonical-new group section disagreement')
        group=ET.SubElement(group_nodes,'canonical_new_group',{'new_group_id':group_id,
            'section_id':next(iter(sections)), 'readiness':('READY_TO_CREATE_FULL' if
            any(d['readiness'].get('full_ready') for _,d in members) else 'READY_TO_CREATE_IDENTITY')})
        for (supplier,external),_ in members:
            ET.SubElement(group,'source_ref',{'supplier':supplier,'external_id':external})
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)


def neutral_new_groups_xml(records, *, matching, generated_at=''):
    """One creation proposal per proven group, independent supplier offers.

    A group is emitted only when every source member passed identity gates and
    they agree on a verified SECTION_ID. Never exports a Sterbrust Product ID.
    """
    from collections import defaultdict
    by_source={(r['supplier_code'],str(r['external_id'])):r for r in records}
    if len(by_source) != len(records): raise ValueError('Duplicate source identity')
    groups=defaultdict(list)
    seen=set()
    for row in matching:
        if not row.get('new_group_id'): continue
        identity=row['source'],str(row['external_id'])
        if identity in seen: raise ValueError('Source occurs in more than one NEW group')
        seen.add(identity)
        groups[row['new_group_id']].append(row)
    root=ET.Element('supplier_new_proposals',{'schema':'universal-supplier-neutral-new-v1',
        'generated_at':generated_at,'esol_import_payload':'false'})
    emitted=0
    for group_id,members in sorted(groups.items()):
        if not all(r.get('readiness',{}).get('identity_ready') and
                   r['classification']=='NEW_CANDIDATE' for r in members): continue
        if any(r.get('sterbrust_product_id') for r in members):
            raise ValueError('NEW group must not contain a Sterbrust Product ID')
        sections={r.get('proposed_section_id') for r in members}
        if len(sections)!=1 or None in sections: raise ValueError('Ready group section disagreement')
        records_for_group=[by_source[(r['source'],str(r['external_id']))] for r in members]
        node=ET.SubElement(root,'canonical_new_group',{'new_group_id':group_id,
            'section_id':str(next(iter(sections))),
            'readiness':('READY_TO_CREATE_FULL' if any(r['readiness'].get('full_ready') for r in members)
                         else 'READY_TO_CREATE_IDENTITY')})
        # Source records are offers/content evidence, not extra creation cards.
        snapshot=ET.fromstring(neutral_snapshot_xml(records_for_group,matching=members,generated_at=generated_at))
        node.append(snapshot.find('source_records'))
        emitted+=1
    root.set('canonical_new_group_count',str(emitted))
    ET.indent(root,space='  ')
    return ET.tostring(root,encoding='utf-8',xml_declaration=True)
