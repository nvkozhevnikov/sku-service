"""Fresh risk-stratified sample and source packets. Preparation is not approval."""
from collections import Counter
from urllib.parse import unquote, urlparse
import re
from scripts import qa_ltt_uss_final as q
from bs4 import BeautifulSoup
r,o,OUT=q.r,q.o,q.OUT


def text(node):
    return ' '.join(node.get_text(' ',strip=True).split()) if node is not None else ''


def source_packet(entry):
    path=o.ROOT/entry['journal'];assert r.digest(path.read_bytes())==entry['journal_sha256']
    row=r.load(path);m=row['result'];body=(r.OUT/'EVIDENCE'/entry['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
    assert r.digest(body)==m['evidence_sha256']
    dom=BeautifulSoup(body.decode('utf8'),'html.parser');head=dom.find('h1')
    packet=dict(entry=entry,response_sha256=m['response_sha256'],evidence_sha256=m['evidence_sha256'],
        original_observed_at=m['observed_at'],charset=m['charset'],document_title=text(dom.title),
        canonicals=[a.get('href') for a in dom.select('link[rel="canonical"]')],
        headings=[dict(tag=a.name,classes=a.get('class',[]),text=text(a),parent_id=a.parent.get('id')) for a in dom.select('h1,h2')],
        evidence_path=str((r.OUT/'EVIDENCE'/entry['supplier']/(m['evidence_sha256']+'.html')).relative_to(o.ROOT)),semantic_decision=None)
    if entry['supplier']=='uss_stanko':
        tables=[]
        for table in dom.find_all('table'):
            if table.find('table'):continue
            rows=[]
            for tr in table.find_all('tr'):
                cells=tr.find_all(['td','th'],recursive=False)
                if cells:rows.append([text(c) for c in cells])
            semantic=[row for row in rows if any(re.search('модель|номер.*арт|артикул|^цена|^наличие',c,re.I) for c in row)]
            if semantic or 'data' in table.get('class',[]) or table.select('a.bnt_to_cart,a.bnt_zak,a[name^="art"]'):
                tables.append(dict(classes=table.get('class',[]),parent_id=table.parent.get('id'),rows=rows,
                    model_rows=[row for row in rows if row and row[0].lower()=='модель'],
                    article_rows=[row for row in rows if row and re.search('номер.*арт|артикул',row[0],re.I)],
                    price_rows=[row for row in rows if row and row[0].lower().startswith('цена')],
                    stock_rows=[row for row in rows if row and row[0].lower()=='наличие'],
                    article_anchors=[a.get('name') for a in table.select('a[name^="art"]')],
                    controls=[str(a) for a in table.select('a.bnt_to_cart,a.bnt_zak')],html=str(table)))
        block=dom.select_one('#div_print') or dom.select_one('#div_body') or dom.body or dom
        packet.update(tables=tables,content_text=text(block),content_html=str(block),
            total_data_table_count=len(dom.select('table.data')),
            article_anchors=[a.get('name') for a in block.select('a[name^="art"]')],
            purchase_controls=[str(a) for a in block.select('a.bnt_to_cart,a.bnt_zak')])
    elif entry['supplier'] in {'ltt_msk','ltt_sib'}:
        block=dom.select_one('.product')
        assert block is not None
        for related in block.select('.catalog_item'):related.decompose()
        packet.update(own_title=text(block.find('h1')),stable_ids=[a.get('value') for a in block.select('form.ms2_form input[name="id"]')],
            numeric_price_cells=[text(a) for a in block.select('.product_price')],por_cells=[text(a) for a in block.select('.product_price_no_price')],
            old_price_cells=[text(a) for a in block.select('.product_old_price')],
            stock_spans=[text(a) for a in block.select('span[style]') if re.search(r'color\s*:\s*(?:green|red|orange)',a.get('style',''),re.I)],
            content_text=text(block),content_html=str(block))
    else:
        assert head is not None
        block=head.find_parent(lambda a:a.name=='div' and 'colPrevious' in a.get('class',[]))
        assert block is not None
        packet.update(own_title=text(head),stable_ids=[dict(id=a.get('data-product'),label=a.get('data-label-modal')) for a in block.select('[data-product][data-label-modal]')],
            numeric_price_cells=[text(a) for a in block.select('.prices')],stock_spans=[text(a) for a in block.select('.countBlock')],
            content_text=text(block),content_html=str(block))
    return packet


def prepare():
    assert r.load(OUT/'STRUCTURAL_RESULT.json')['status']=='PASS'
    assert not (OUT/'SAMPLE_MANIFEST.json').exists()
    plan=r.load(q.PLAN)['rows'];baseline=r.load(q.BASELINE);unit_ids={a[0] for a in baseline['sib_units']}
    baseline_sha=r.digest(q.BASELINE.read_bytes())
    population={s:[] for s in r.SOURCES}
    for e in plan:
        if e['outcome']!='PERSISTED':continue
        record=OUT/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name;db=r.load(record)
        p=db['parsed_product'];kind=p['enrichment_evidence']['product_kind']['product_kind'];brand=p['source_brand'] or 'UNKNOWN'
        tags={f"price:{p['price']['state']}",f"stock:{p['availability']}",f'kind:{kind}',f'brand:{brand}',f"category:{p['source_category']}",
            'title:short' if len(p['name'])<=40 else ('title:long' if len(p['name'])>=100 else 'title:medium')}
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
            if e['index'] in {23,24}:tags.add('boundary:classless_table');mandatory=True
            if e['index'] in {368,369,374,377,384}:tags.add('boundary:own_h2');mandatory=True
            if p['price']['currency']=='USD':mandatory=True
        rank=r.digest(('FRESH_FULL_QA_FINAL_2026-10-15\0'+baseline_sha+'\0'+e['supplier']+'\0'+e['external_id']).encode())
        population[e['supplier']].append(dict(**e,tags=sorted(tags),mandatory=mandatory,rank=rank,
            persisted_record=str(record.relative_to(OUT)),persisted_record_sha256=r.digest(record.read_bytes())))
    selected=[];coverage={}
    for supplier,pop in population.items():
        freq=Counter(t for a in pop for t in a['tags']);chosen=sorted([a for a in pop if a['mandatory']],key=lambda a:a['rank']);covered={t for a in chosen for t in a['tags']}
        rest=[a for a in pop if not a['mandatory']]
        while len(chosen)<100:
            best=min(rest,key=lambda a:(-sum(1/freq[t] for t in a['tags'] if t not in covered),a['rank']))
            chosen.append(best);rest.remove(best);covered.update(best['tags'])
        selected.extend(chosen);coverage[supplier]=dict(population=len(pop),selected=len(chosen),available=dict(freq),
            selected_cohorts=dict(Counter(t for a in chosen for t in a['tags'])),uncovered_tags=sorted(set(freq)-covered))
    manifest=dict(status='SELECTED_NOT_REVIEWED',selection='Fresh mandatory then inverse-frequency risk cohort coverage; SHA256 tie-break seeded by final baseline',
        baseline_sha256=baseline_sha,total=len(selected),coverage=coverage,rows=selected)
    r.save(OUT/'SAMPLE_MANIFEST.json',manifest)
    with q.q.guard() as network:
      for n,e in enumerate(selected,1):
        packet=source_packet(e);r.save(OUT/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name,packet)
        if n%100==0:print(f'FRESH SAMPLE PACKETS {n}/400 (not approvals)',flush=True)
      terminals=[]
      for e in plan:
        if e['supplier']=='uss_stanko' and e['outcome']!='PERSISTED':
            packet=source_packet(e)
            r.save(OUT/'TERMINAL_PACKETS'/(o.ROOT/e['journal']).name,packet)
            terminals.append(dict(index=e['index'],url=e['url'],outcome=e['outcome'],headings=packet['headings'],
                tables=[{k:t[k] for k in ['classes','model_rows','article_rows','price_rows','stock_rows','article_anchors','controls','parent_id']} for t in packet['tables']],
                packet='TERMINAL_PACKETS/'+(o.ROOT/e['journal']).name,semantic_decision=None))
      assert len(terminals)==188
      r.save(OUT/'TERMINAL_PACKET_INDEX.json',dict(status='PREPARED_NOT_REVIEWED',total=188,rows=terminals,network=network))
    print('Fresh400sample/188terminal packets prepared; no semantic approval implied',flush=True)


if __name__=='__main__':prepare()
