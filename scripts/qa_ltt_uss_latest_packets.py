"""New deterministic risk sample; packet preparation is never semantic PASS."""
from collections import Counter
from urllib.parse import unquote,urlparse
from bs4 import BeautifulSoup
import re
from scripts import qa_ltt_uss_latest as q
from scripts.qa_ltt_uss_final_packets import source_packet
r,o,OUT=q.r,q.o,q.OUT

def prepare():
    assert r.load(OUT/'STRUCTURAL_AUDIT.json')['status']=='PASS'
    assert not (OUT/'FRESH_400_SAMPLE_MANIFEST.json').exists()
    baseline=r.load(q.BASELINE);seed='FRESH_FINAL_QA_TERMINAL_BATCH_2026-10-15_V1';baseline_sha=r.digest(q.BASELINE.read_bytes());unit_ids={x[0] for x in baseline['sib_units']}
    population={s:[] for s in r.SOURCES}
    for e in r.load(q.PLAN)['rows']:
        if e['outcome']!='PERSISTED':continue
        record=OUT/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name;db=r.load(record);p=db['parsed_product']
        tags={f"price:{p['price']['state']}",f"stock:{p['availability']}",f"kind:{p['enrichment_evidence']['product_kind']['product_kind']}",f"brand:{p['source_brand'] or 'UNKNOWN'}",f"category:{p['source_category']}",'title:short' if len(p['name'])<=40 else 'title:long' if len(p['name'])>=100 else 'title:medium'}
        parts=unquote(urlparse(e['url']).path).strip('/').split('/')
        if len(parts)>1:tags.add('url_section:'+'/'.join(parts[:-1][:2]))
        manufacturer=p['enrichment_evidence'].get('manufacturer_raw')
        if manufacturer:tags.add('explicit_manufacturer:'+manufacturer)
        if any(ord(c)>127 for c in unquote(e['url'])):tags.add('boundary:non_ascii_url')
        if p['price']['currency']=='USD':tags.add('boundary:explicit_USD')
        if '/' in p['price']['raw_text']:tags.add('boundary:unit_price')
        if 'uss_discontinued_wording_overrides_stock' in p['diagnostics']:tags.add('boundary:discontinued')
        mandatory=(e['supplier']=='ltt_sib' and e['external_id'] in unit_ids) or (e['supplier']=='ltt_yug' and e['external_id'] in {'312','297'})
        if e['supplier']=='uss_stanko':
            if e['index'] in q.FIXED:mandatory=True;tags.add('boundary:historical_corrected')
            if e['index'] in {53,61,64,65,297,305}:tags.add('boundary:column_header')
            if e['index']==20:tags.add('boundary:main_with_accessory_shared_table')
            if e['index']==405:tags.add('boundary:local_main_section_accessories')
            if p['price']['currency']=='USD':mandatory=True
        rank=r.digest((seed+'\0'+baseline_sha+'\0'+e['supplier']+'\0'+e['external_id']).encode())
        population[e['supplier']].append(dict(**e,tags=sorted(tags),mandatory=mandatory,rank=rank,persisted_record=str(record.relative_to(OUT)),persisted_record_sha256=r.digest(record.read_bytes())))
    selected=[];coverage={}
    for supplier,pop in population.items():
        freq=Counter(t for a in pop for t in a['tags']);chosen=sorted([a for a in pop if a['mandatory']],key=lambda a:a['rank']);covered={t for a in chosen for t in a['tags']};rest=[a for a in pop if not a['mandatory']]
        while len(chosen)<100:
            best=min(rest,key=lambda a:(-sum(1/freq[t] for t in a['tags'] if t not in covered),a['rank']));chosen.append(best);rest.remove(best);covered.update(best['tags'])
        selected.extend(chosen);coverage[supplier]=dict(population=len(pop),selected=len(chosen),available=dict(freq),selected_cohorts=dict(Counter(t for a in chosen for t in a['tags'])),uncovered_tags=sorted(set(freq)-covered))
    manifest=dict(status='FRESH_SELECTED_NOT_REVIEWED',seed=seed,baseline_sha256=baseline_sha,active_seal='TERMINAL_BATCH_FIX_CODE_SEAL',selection='Mandatory current special cases then inverse frequency risk cover; SHA256 tie break',total=len(selected),coverage=coverage,rows=selected,prior_semantic_passes_used=False)
    assert len(selected)==400
    r.save(OUT/'FRESH_400_SAMPLE_MANIFEST.json',manifest)
    with q.guard.guard() as network:
      index=[]
      for n,e in enumerate(selected,1):
        packet=source_packet(e);dest=OUT/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name;r.save(dest,packet)
        index.append(dict(supplier=e['supplier'],index=e['index'],packet=str(dest.relative_to(OUT)),sha256=r.digest(dest.read_bytes()),persisted_record=e['persisted_record'],semantic_decision=None))
        if n%50==0:print(f'NEW SEMANTIC SOURCE PACKETS {n}/400 NOT APPROVALS',flush=True)
      r.save(OUT/'FRESH_SEMANTIC_PACKET_INDEX.json',index)
      terminal=[]
      for e in r.load(q.PLAN)['rows']:
        if e['supplier']!='uss_stanko' or e['outcome']=='PERSISTED':continue
        packet=source_packet(e);dest=OUT/'TERMINAL_PACKETS'/(o.ROOT/e['journal']).name;r.save(dest,packet)
        terminal.append(dict(index=e['index'],outcome=e['outcome'],url=e['url'],evidence_sha256=packet['evidence_sha256'],packet=str(dest.relative_to(OUT)),sha256=r.digest(dest.read_bytes()),semantic_approval=False))
      assert len(terminal)==180;r.save(OUT/'TERMINAL_PACKET_INDEX.json',terminal)
    print('Fresh400+180terminal packets ready; no semantic decisions yet',flush=True)

if __name__=='__main__':prepare()
