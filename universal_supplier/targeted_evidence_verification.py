"""Label-role preservation for targeted manufacturer evidence, no assignments."""
import re
from lxml import html

def canonical_visible_table(body):
    root=html.fromstring(body);out=[]
    blocks=root.xpath('//*[contains(@class,"js-detail-description")]')
    if len(blocks)!=1:raise ValueError('Own canonical description block not unique')
    for tr in blocks[0].xpath('.//tr'):
        cells=tr.xpath('./td|./th')
        if len(cells)!=2 or tr.xpath('.//a|.//table|.//script'):continue
        name,value=[' '.join(' '.join(c.itertext()).split()) for c in cells]
        if name and value:out.append({'name':name,'value':value,'unit':'','provenance':'saved_live_canonical_own_description'})
    return {'h1':' '.join(root.xpath('//h1')[0].itertext()).strip(),'properties':out}

def smec_table(body, exact_heading):
    root=html.fromstring(body)
    headings=root.xpath('//th[contains(@class,"model_name")]')
    matching=[h for h in headings if ' '.join(' '.join(h.itertext()).split())==exact_heading]
    if len(matching)!=1:raise ValueError('Manufacturer execution table not unique')
    table=matching[0].xpath('ancestor::table[1]')[0];rows=[]
    for tr in table.xpath('.//tr'):
        cells=tr.xpath('./td')
        if len(cells)<2:continue
        values=[' '.join(' '.join(c.itertext()).split()) for c in cells]
        rows.append({'label':values[0],'values':values[1:]})
    return {'heading':exact_heading,'rows':rows}

def labelled_power_pair(value, role):
    """Slash values are labelled pairs, never a scalar or automatic S6 alias."""
    parts=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*/\s*(\d+(?:[.,]\d+)?)\s*',str(value))
    if not parts or role!='Cont./Max':return {'usable':False,'role':role,'raw':value}
    return {'usable':True,'role':role,'continuous_kw':parts[1].replace(',','.'),
            'maximum_kw':parts[2].replace(',','.'),'scalar':False,'s6_equivalent':False}

def preserve_target_hold(strict_proof, external_bore, canonical_own_bore):
    """A manufacturer value is not independent evidence on both other sides."""
    if strict_proof['confirmed']:return strict_proof
    return {**strict_proof,'manufacturer_main_bore_mm':external_bore,
            'canonical_own_bore_observed':canonical_own_bore,
            'external_value_copied_to_both_sides':False}
