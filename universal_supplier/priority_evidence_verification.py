"""Targeted saved-page proof. No HTTP, database, fuzzy acceptance or training."""
from copy import deepcopy
import json
import re
from lxml import html
from .offline_canonical_closure import strict_saved_resolution
from .offline_review_resolution import clean_observed_model


def visible_page(body):
    root = html.fromstring(body)
    text = lambda node: ' '.join(' '.join(node.itertext()).split())
    titles = [text(n) for n in root.xpath('//h1') if text(n)]
    props = []
    for n in root.xpath('//*[@id="elTabProp"]//*[contains(concat(" ",normalize-space(@class)," ")," elTabPropName ")]'):
        values = n.xpath('following-sibling::*[1][contains(concat(" ",normalize-space(@class)," ")," elTabPropNum ")]')
        if values and text(n) and text(values[0]):
            props.append({'name':text(n),'value':text(values[0]),'unit':'','provenance':'verified_saved_visible_elTabProp'})
    urls = root.xpath('//link[@rel="canonical"]/@href')
    return {'h1': titles[0] if len(titles)==1 else '', 'canonical_url':urls[0] if len(urls)==1 else '',
            'properties':props}


def merge_observations(primary, saved):
    """Preserve disagreeing values: never deduplicate by label alone."""
    result=[]; seen=set()
    for p in primary + saved:
        key=(p['name'].strip().casefold(),str(p['value']).strip(),p.get('unit') or '')
        if key not in seen:result.append(deepcopy(p));seen.add(key)
    return result


def verify_candidate(row, target, page, mappings, evidence_ref, previous_contradictions=()):
    view=deepcopy(row)
    blockers=[]
    if not page.get('h1') or page.get('canonical_url','').rstrip('/') != row.get('source_url','').rstrip('/'):
        blockers.append('SAVED_PAGE_OWN_IDENTITY_NOT_PROVEN')
    if previous_contradictions: blockers.append('PREVIOUS_TYPED_CONTRADICTION_NOT_DISCHARGED')
    view.update(name=page.get('h1') or row['name'],
        observed_properties=merge_observations(page['properties'],row.get('observed_properties') or []),
        evidence_ref=evidence_ref)
    view['properties']={p['name']:p['value'] for p in view['observed_properties']}
    # Do not alter the stored source identity/model/execution.
    audit=strict_saved_resolution(view,[target],mappings)
    result=audit['evaluations'][0]
    typed=result['evidence'].get('typed_characteristics',{})
    for p in typed.get('agreements',[]):
        if p['key']=='max_processing_length':
            parts=re.findall(r'\d{3,}',clean_observed_model(row)['model'])
            if len(parts)>=2 and p['source']['values'] != [str(int(parts[-1]))]:
                blockers.append('FULL_EXECUTION_PROCESSING_LENGTH_CONTRADICTION')
    # Additional visible axis/options must not disagree despite absent axis-count.
    for label in ('Наличие оси Y','Наличие контршпинделя','Приводной инструмент'):
        def values(props):
            return {str(p['value']).strip().casefold() for p in props
                    if p['name'].strip().casefold().startswith(label.casefold())}
        a,b=values(view['observed_properties']),values(target['observed_properties'])
        if len(a)>1 or len(b)>1 or (a and b and a!=b): blockers.append('VISIBLE_AXIS_OPTION_CONTRADICTION:'+label)
    result=deepcopy(result)
    result['blocking_reasons']=sorted(set(result['blocking_reasons']+blockers))
    result['confirmed']=not result['blocking_reasons']
    return result


def apply_verified(rows, promotions):
    """Immutable predecessor; replay exact no-op; only REVIEW may advance."""
    output=deepcopy(rows)
    bykey={(r['source'],str(r['external_id'])):r for r in output}
    assert len(bykey)==len(output)
    assert len({(p['source'],p['external_id']) for p in promotions})==len(promotions)
    for p in promotions:
        assert p['proof']['confirmed']
        row=bykey[p['source'],p['external_id']]
        if row['classification']=='EXISTING_CONFIRMED':
            assert row['sterbrust_product_id']==p['canonical_id'] and row.get('priority_saved_proof')==p['proof']
            continue
        assert row['classification']=='REVIEW'
        row.update(classification='EXISTING_CONFIRMED',sterbrust_product_id=p['canonical_id'],
                   sterbrust_name=p['canonical_name'],full_model_confirmed=True,
                   priority_saved_proof=p['proof'],priority_evidence_ref=p['evidence_ref'])
    return output
