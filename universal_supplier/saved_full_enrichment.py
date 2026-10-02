"""Offline own-card payload extraction; no matching, networking or DB access."""
import copy
import hashlib
import re
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup
from .two_new_readiness import norm, role, anchors, readiness_gate


def text(node):
    return node.get_text(' ', strip=True) if node is not None else ''


def extract_payload(html, scope, proof, observed_at):
    soup = BeautifulSoup(html, 'html.parser')
    base = {'source_url':scope['supplier_url'], 'capture_path':scope['capture'],
            'capture_sha256':scope['capture_sha256'], 'observed_at':observed_at,
            'evidence_level':'PACKAGED-EVIDENCE', 'http_performed':False}
    def field(state, value, selector, reason=None):
        return {'state':state, 'value':value, 'provenance':dict(base, selector=selector), 'reason':reason}
    def observed(node, selector):
        return field('OBSERVED' if text(node) else 'NOT_FOUND' if node is not None else 'UNKNOWN',
                     text(node) or None, selector)
    h1 = soup.find('h1'); main = soup.select_one('.el_Main')
    description = soup.select_one('#elTabDesc'); table = soup.select_one('#elTabProp')
    payload = {'source':scope['source'], 'external_id':scope['external_id'],
               'new_group_id':scope['new_group_id'], 'new_candidate_id':scope['new_candidate_id'],
               'sterbrust_product_id':None, 'provenance':base, 'fields':{}}
    fields = payload['fields']
    fields['name'] = observed(h1, 'h1')
    brand = main.select_one('.el_Brand img[alt^="Бренд:"]') if main else None
    fields['brand'] = field('OBSERVED' if brand else 'UNKNOWN',
                            brand['alt'].split(':',1)[1].strip() if brand else None, '.el_Main .el_Brand img[alt^="Бренд:"]')
    fields['manufacturer'] = field('NOT_FOUND', None, '.el_Main', 'No independent legal manufacturer field; brand not silently substituted')
    fields['model_execution'] = field('OBSERVED' if text(h1) and norm(text(h1)).endswith(norm(proof['model'])) else 'CONFLICT',
                                      proof['model'], 'h1', 'Exact accepted full execution rechecked against visible H1')
    for name in ('supplier_article','manufacturer_article'):
        fields[name] = field('NOT_FOUND' if main else 'UNKNOWN', None, '.el_Main',
                             'No own article/SKU displayed; Bitrix element ID is not manufacturer article')
    # If an actual visible article is added later, do not keep a false NOT_FOUND.
    article = main.select_one('[itemprop="sku"], .el_Article, .product-article') if main else None
    if article and text(article): fields['supplier_article'] = observed(article, '.el_Main own article/SKU')
    fields['category'] = field('OBSERVED', copy.deepcopy(proof['section']), 'accepted exact operator section assignment')
    fields['description'] = observed(description, '#elTabDesc')
    payload['description_html'] = description.decode_contents() if description else None
    rows = []
    if table:
        for i,tr in enumerate(table.select('tr')):
            cells = tr.find_all(['td','th'], recursive=False)
            if len(cells) != 2:
                if text(tr): rows.append({'state':'UNKNOWN','raw_label':text(tr),'reason':'unparsed own table row'})
                continue
            label,value = text(cells[0]),text(cells[1]); label_norm = ' '.join(label.split()).lower()
            canonical = role(label)
            aliases = {'минимальная высота подачи':'minimum_feed_height', 'натяжение пилы':'blade_tension_type',
                       'настройка высоты распила':'height_adjustment_motor_power', 'главный двигатель':'main_drive_power',
                       'скорость подачи':'feed_speed', 'вес':'weight_unspecified', 'масса':'weight_unspecified'}
            canonical = canonical or next((v for k,v in aliases.items() if label_norm.startswith(k)),
                                         'source_property_' + hashlib.sha256(label_norm.encode()).hexdigest()[:12])
            unit = next((u for pat,u in ((r'м/мин','m/min'),(r'квт','kW'),(r'мм','mm'),(r'кг','kg'),(r'\bвт\b','W'))
                         if re.search(pat,label_norm)), None)
            numeric = re.sub(r'\s+','',value).replace(',','.')
            normalized_value = float(numeric) if re.fullmatch(r'\d+(?:\.\d+)?',numeric) else value
            property_role = 'unspecified_weight_basis' if canonical == 'weight_unspecified' else canonical
            if 'gross' in label_norm or 'брутто' in label_norm: property_role = 'gross_weight'
            if 'net' in label_norm or 'нетто' in label_norm: property_role = 'net_weight'
            if re.search(r'continuous|cont\.|s6|max|макс',label_norm) and 'power' in canonical:
                property_role += ':' + label_norm
            rows.append({'state':'OBSERVED' if label and value else 'UNKNOWN', 'raw_label':label,
                         'normalized_property':canonical, 'raw_value':value,'value':normalized_value,
                         'unit':unit,'role':property_role,'configuration':'BASE_TABLE',
                         'normalization':'scalar_decimal_only_ranges_and_options_preserved',
                         'provenance':dict(base,selector=f'#elTabProp tr[{i+1}]')})
    fields['characteristics'] = field('OBSERVED' if table and rows else 'NOT_FOUND' if table else 'UNKNOWN',rows,'#elTabProp')
    # Preserve optional conveyor geometry separately, never as base-machine dimensions.
    optional = []
    if description:
        for block in description.select('.elPropBlock'):
            heading = text(block.find(['h2','h3']))
            if not re.search(r'дополнительн|опци',heading,re.I): continue
            for li in block.select('li'):
                if li.find('ul'): continue
                raw = text(li)
                if re.search(r'\d',raw):
                    parent = li.find_parent('ul').find_parent('li')
                    optional.append({'state':'OBSERVED','raw_label':raw,'raw_value':raw,
                        'normalized_property':'optional_conveyor_' + ('length' if 'длина' in raw.lower() else 'width'),
                        'value':raw,'unit':'mm' if 'мм' in raw else None,'role':'OPTIONAL_CONFIGURATION',
                        'context':text(parent).split(raw)[0] if parent else heading,
                        'provenance':dict(base,selector='#elTabDesc additional equipment li')})
    payload['optional_characteristics'] = optional
    images = []
    for a in soup.select('.el_Main .el_Images a[data-fancybox="elPreview"]'):
        url = urljoin(scope['supplier_url'],a.get('href',''))
        if urlparse(url).scheme not in ('https','http'): continue
        img = a.find('img')
        caption = a.get('data-caption') or a.get('title') or (img.get('alt') if img else '') or ''
        if url not in {x['original_url'] for x in images}:
            images.append({'state':'OBSERVED','original_url':url,
                'caption':field('OBSERVED' if caption else 'NOT_FOUND',caption or None,'own gallery caption'),
                'provenance':dict(base,selector='.el_Main .el_Images a[data-fancybox="elPreview"]'),
                'binary_downloaded':False,'url_live_verified':False})
    gallery = soup.select_one('.el_Main .el_Images')
    fields['images'] = field('OBSERVED' if images else 'NOT_FOUND' if gallery else 'UNKNOWN',images,'.el_Main .el_Images')
    files_tab = soup.select_one('#elTabFiles')
    docs = []
    for container in (files_tab,description):
        if container:
            for a in container.select('a[href]'):
                href = a['href']
                if re.search(r'\.(pdf|docx?|xlsx?|zip|rar)(?:[?#]|$)',href,re.I) or a.has_attr('download'):
                    docs.append({'url':urljoin(scope['supplier_url'],href),'caption':text(a),
                                 'provenance':dict(base,selector='own description/files document link')})
    fields['documents'] = field('OBSERVED' if docs else 'NOT_FOUND' if files_tab else 'UNKNOWN',docs,'#elTabFiles + own description links',text(files_tab))
    price = soup.select_one('#elPrice'); price_text = text(price)
    if re.search(r'цена\s+по\s+запросу',price_text,re.I):
        fields['price'] = field('OBSERVED',None,'#elPrice .elPrice_Text','Explicit price on request; numeric NULL')
        fields['price_state'] = field('OBSERVED','price_on_request','#elPrice .elPrice_Text')
    else:
        fields['price'] = field('UNKNOWN',None,'#elPrice','No verified numeric price extraction')
        fields['price_state'] = field('UNKNOWN',None,'#elPrice')
    # Own explicit metadata observed, not inferred from price or calculator defaults.
    avail = soup.select_one('meta[property="product:availability"]')
    fields['availability'] = field('OBSERVED' if avail and avail.get('content') else 'NOT_FOUND',
                                   avail.get('content') if avail else None,'meta[property="product:availability"]',
                                   'Own source metadata; no user-facing availability found; not live stock verification')
    fields['source_url'] = field('OBSERVED',scope['supplier_url'],'pinned requested URL')
    fields['capture_time'] = field('OBSERVED' if observed_at else 'UNKNOWN',observed_at,'saved capture ledger observed_at')
    fields['capture_sha256'] = field('OBSERVED',scope['capture_sha256'],'pinned HTML SHA-256')
    fields['country'] = observed(main.select_one('.el_Brand span') if main else None,'.el_Main .el_Brand span')
    bonus = main.select_one('.el_Bonus') if main else None
    fields['warranty_and_services'] = observed(bonus,'.el_Main .el_Bonus')
    actual = anchors([(r['raw_label'],r.get('raw_value',''),r.get('unit') or '') for r in rows if r['state']=='OBSERVED'])
    critical = [k for k,v in proof['typed_anchors'].items() if actual.get(k) != set(v)]
    required = ('name','brand','model_execution','category','characteristics','description','images',
                'documents','price','price_state','availability','source_url','capture_time','capture_sha256')
    blockers = ['IDENTITY_PROOF_NOT_PASS'] if not readiness_gate(proof['gates']) else []
    blockers += ['CRITICAL_CHARACTERISTIC_CONFLICT:'+k for k in critical]
    blockers += ['REQUIRED_FIELD_'+fields[k]['state']+':'+k for k in required if fields[k]['state'] in ('UNKNOWN','CONFLICT')]
    blockers += ['UNPARSED_OWN_CHARACTERISTIC'] if any(r['state']!='OBSERVED' for r in rows) else []
    for k in ('name','brand','model_execution','category','description','characteristics','images'):
        if fields[k]['state'] == 'NOT_FOUND': blockers.append('ESSENTIAL_CONTENT_NOT_FOUND:'+k)
    payload['readiness'] = {'state':'READY_TO_CREATE_FULL' if not blockers else 'READY_TO_CREATE_IDENTITY',
                           'full_ready':not blockers,'identity_ready':not critical and readiness_gate(proof['gates']),
                           'blockers':blockers,'scope':'saved_source_content_not_live_media_or_stock_verification'}
    payload['field_statuses'] = {state:[k for k,v in fields.items() if v['state']==state]
                                 for state in ('OBSERVED','NOT_FOUND','UNKNOWN','CONFLICT')}
    return payload
