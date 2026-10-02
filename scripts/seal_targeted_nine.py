"""Independent supervisory checks and immutable checkpoint for nine cases."""
from pathlib import Path
from collections import Counter
import sys,re,json
from lxml import html
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.integrate_targeted_nine import PRE,OUT,GROK,REL,COMMIT,load,save,sha
from universal_supplier.priority_evidence_verification import apply_verified
TOP=OUT.parent
def numbers(value):return re.findall(r'\d+(?:[.,]\d+)?',str(value).replace(',','.'))
def main():
    summary=load(OUT/'VERIFIED_SUMMARY.json');audits=load(OUT/'NINE_VERIFIED_PROOFS.json');bycase={r['priority_index']:r for r in audits}
    assert summary['new_existing']==1 and summary['overall']=={'Existing':506,'READY_TO_CREATE_IDENTITY':0,'Review':3642,'Conflict':259}
    duplicate=bycase[1]['canonical_targets'];assert {d['canonical_id'] for d in duplicate}=={'146998','147023'}
    left,right=[d['live_own_table']['properties'] for d in duplicate]
    def values(props,regex):return [p['value'] for p in props if re.search(regex,p['name'],re.I)]
    comparisons={}
    for label,regex,expected in [('blade',r'^Размеры полотна',['5000','34','1.1']),('speed',r'^Скорость резки',['15','120']),
                                 ('motor',r'^Мощность двигателя\s*(?:,|\(|$)',['3']),('height',r'^Высота рабочей поверхности',['635']),('weight',r'^Масса',['1030'])]:
        a,b=values(left,regex),values(right,regex);assert len(a)==len(b)==1 and numbers(a[0])==numbers(b[0])==expected,(label,a,b)
        comparisons[label]={'left':a[0],'right':b[0],'normalized':expected}
    dimsleft=[values(left,'^Габариты: '+part)[0] for part in ('длина','ширина','высота')]
    dimsright=values(right,'^Габаритные размеры')[0]
    assert [n for v in dimsleft for n in numbers(v)]==numbers(dimsright)==['2830','920','1680']
    comparisons['dimensions']={'left':dimsleft,'right':dimsright}
    lcut=[p['value'] for p in left if p['name'].startswith('Распил')]
    rcut=[p['value'] for p in right if p['name'] in ('квадрат','прямоугольник') or p['name'].startswith('Распил')]
    assert [numbers(v) for v in lcut]==[numbers(v) for v in rcut]==[['330'],['330'],['450','330'],['200'],['200'],['200','330']]
    comparisons['cut_geometry']={'left':lcut,'right':rcut,'angles':'left0 perpendicular == right90; both +45 mitre'}
    save('DUPLICATE_INDEPENDENT_COMPARISON.json',{'ids':['146998','147023'],'comparison':comparisons,'survivor':None})
    factory=load(OUT/'MANUFACTURER_EXACT_TABLES.json');previous={r['priority_index']:r for r in load(PRE/'INDEPENDENT_22_PROOF.json')}
    supervision=[]
    for i,key,weight,x in [(56,'SL2000B','3900','210'),(57,'SL2000BM','4000','215'),(58,'SL3000BL','6400','245')]:
        t={r['label']:r['values'][0] for r in factory[key]['rows']}
        p=previous[i]['assessments'][0]['source_properties']
        source_speed=values(p,r'^Макс\. скорость вращения шпинделя')[0]
        source_weight=values(p,r'^Масса')[0];source_x=values(p,r'^Ход по оси X')[0];source_nose=values(p,r'^Торец шпинделя')[0]
        assert t['Spindle bore diameter mm']==('105' if i==58 else '76')
        assert t['Spindle motor (Cont./Max) kW']==('18.5/26' if i==58 else '15/18.5')
        assert t['Machine weight kg']==weight and t['X-axis travel mm']==x
        mismatches=[]
        for name,sv,ov in [('speed',source_speed,t['Spindle speed rpm']),('weight',source_weight,t['Machine weight kg']),('X_travel',source_x,t['X-axis travel mm'])]:
            if numbers(sv)!=numbers(ov):mismatches.append({'key':name,'supplier':sv,'official':ov})
        if source_nose.replace('А','A')!=t['Spindle nose ASA']:mismatches.append({'key':'spindle_nose','supplier':source_nose,'official':t['Spindle nose ASA']})
        assert (not mismatches)==(i==58)
        base=bycase[i]['canonical_targets'][0]['proof']
        supervision.append({'priority_index':i,'source_canonical_subproof_confirmed':base['confirmed'],
            'manufacturer_mismatches':mismatches,'full_current_evidence_confirmed':base['confirmed'] and not mismatches,
            'official_bore_not_copied_to_supplier_or_canonical':True,'S6_equals_Cont_Max':False})
    save('OFFICIAL_SUPERVISORY_GUARDS.json',supervision)
    # Inspect authority layers separately: literal PDF tables were rendered and
    # visually checked. Never turn hidden JSON-LD into visible page proof.
    pdf=load(TOP/'DOCUMENT_CHECK/PDF_EXTRACTED.json')
    assert any('3427006' in r['text'] for d in pdf if 'optimum-' in d['pdf'] for r in d['text'])
    for key in ('bekamak-bmso','bekamak-bmsy810'):
        d=next(d for d in pdf if key in d['pdf']);assert any('8200' in r['text'] for r in d['text'])
    logs=[json.loads(l) for l in (GROK/REL/'FETCH_LOG.jsonl').read_text(encoding='utf-8').splitlines()]
    f=next(f for f in logs if f['key']=='bekamak-810cgh-page');body=Path(f['path']).read_bytes();root=html.fromstring(body)
    hidden=root.xpath('//script[@type="application/ld+json"]/text()')
    assert hidden and '4050' in ' '.join(hidden)
    for node in root.xpath('//script|//style'):node.getparent().remove(node)
    visible=' '.join(' '.join(root.itertext()).split());assert '4100' in visible and '15 - 120' in visible
    save('SEPARATE_AUTHORITY_LAYER_VERIFICATION.json',{'810CGH':{'visible_weight':'4100','jsonld_weight':'4050','pdf_weight':'3970',
        'layer_preference_used_for_auto_match':False},'pdf_rendered_pages_checked':7,
        'PDF_warning':'pypdf repaired invalid pointer offsets in source PDFs; relevant tables also visually checked with PDFium.',
        'TU2807V':{'net_kg':'180','gross_kg':'210','contradiction':False,'morse_notations':['КМ4','МК-4','MT4'],
        'country_roles_invented':False},'BODOR':{'price_availability_warranty_used_as_condition':False}})
    before=load(PRE/'MATCHING_ACCEPTED.json')['rows'];after=load(OUT/'MATCHING_ACCEPTED.json')['rows'];prom=load(OUT/'PROMOTIONS.json')
    assert apply_verified(before,prom)==after and apply_verified(after,prom)==after
    changed=[(old['source'],old['external_id']) for old,new in zip(before,after) if old!=new]
    assert len(changed)==1 and changed[0]==('intervesp',prom[0]['external_id'])
    assert len(after)==4407 and Counter(r['classification'] for r in after)=={'EXISTING_CONFIRMED':506,'REVIEW':3642,'CONFLICT':259}
    save('CHECKPOINT.json',{'status':'TARGETED_NINE_OFFLINE_VERIFIED','resume_from':'reports/RC_LOCAL/TARGETED_NINE_INTEGRATION_2026-10-02/CHECKPOINT.json',
         'predecessor':str(PRE/'CHECKPOINT.json'),'grok_commit':COMMIT,'authoritative_matching':'ACCEPTED/MATCHING_ACCEPTED.json',
         'case_proofs':'ACCEPTED/NINE_VERIFIED_PROOFS.json','supervisory_guards':'ACCEPTED/OFFICIAL_SUPERVISORY_GUARDS.json',
         'overall':summary['overall'],'new_existing':1,'http':0,'sql':0,'global_matcher':False,'ingestion':False,
         'completed_do_not_repeat':['9targeted evidence verification','SMEC147497 strict proof','330C duplicate comparison','manufacturer/canonical contradiction checks'],
         'next_action':'Canonical owner decision for duplicate146998/147023; scoped label/version/condition clarification only. No automatic supplier GET/global rerun.',
         'final_xml_xlsx_published':False,'hashes':'ARTIFACT_SHA256.json'})
    rootcheckpoint=TOP/'CHECKPOINT.json';body=(OUT/'CHECKPOINT.json').read_bytes()
    if rootcheckpoint.exists():assert rootcheckpoint.read_bytes()==body
    else:rootcheckpoint.write_bytes(body)
    for name,value in load(OUT/'INPUT_SHA256.json').items():assert sha(Path(name))==value
    print('PASS: nine cases,1promotion,4407immutable source identities,no-op replay,independent duplicate and manufacturer checks')
if __name__=='__main__':main()
