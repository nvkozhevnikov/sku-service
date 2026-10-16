"""Offline kind impact inventory only. No repairs, DB access or FULLQA approval."""
from collections import Counter
from dataclasses import asdict
from pathlib import Path
import ast
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from bs4 import BeautifulSoup
from scripts import qa_ltt_uss_latest as q
r,o=q.r,q.o
OUT=r.BASE/'PRODUCT_KIND_IMPACT_SWEEP_2026-10-15'
MACHINE={'machine','lathe','milling_machine','drill_press','grinder','beveling_machine'}
ACCESSORY=re.compile(r'\b(?:пневмобалк\w*|балк\w*|прижим\w*|зажим\w*|насадк\w*|приспособлен\w*|оснастк\w*|держател\w*|патрон\w*|цанг\w*|оправк\w*|переходник\w*|адаптер\w*|контроллер\w*|пульт\w*|рукоят\w*|ручк\w*|губк\w*|кулачк\w*|подставк\w*|стойк\w*|опор\w*|стол\w*|удлинител\w*|модул\w*|блок\w*|узел\w*|устройств\w*|педал\w*|комплект\w*|набор\w*|автоподатчик\w*|ролик\w*|вал\w*|нож\w*|ремн\w*|запчаст\w*|запасн\w*|кожух\w*|шпиндел\w*|каретк\w*|редуктор\w*|двигател\w*|датчик\w*|линейк\w*|систем\w*|фрез(?:а|ы|ой|ами|ах)?|сверл(?:о|а|ами|ах)?|резц\w*|диск\w*)\b',re.I)
HOST_RELATION=re.compile(r'\b(?:для|на|к|под|со)\s+(?:[\w-]+\s+){0,7}(?:стан\w*|оборудован\w*|каретк\w*|шпиндел\w*)',re.I)
INSTALL=re.compile(r'\b(?:устанавлива\w*|крепится|крепят|монтиру\w*|подключа\w*|используется|предназначен\w*)\b.{0,100}?\b(?:на|к|в|для|со)\b.{0,100}?\b(?:стан\w*|оборудован\w*|каретк\w*|шпиндел\w*)',re.I)
COMPLETE=re.compile(r'\b(?:стан(?:ок|ки|ка|ком|ке)|пила|компрессор|пресс|пылеотсос|стружкоотсос|шлифмашин\w*|фаскоснимател\w*|сверлил\w*|гильотин\w*|сверлильн\w*|токарн\w*)\b',re.I)

def text(n):return ' '.join(n.get_text(' ',strip=True).split()) if n is not None else ''

def source_facts(e,row):
    m=row['result'];response=r.load(r.OUT/m['response_record']);raw=(r.OUT/response['raw_path']).read_bytes()
    body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
    assert r.digest(raw)==m['response_sha256']==response['response_sha256']
    assert r.digest(body)==m['evidence_sha256'] and o.sanitise_for_charset(raw,m['charset'])==body
    dom=BeautifulSoup(body.decode('utf8'),'html.parser')
    if e['supplier'] in {'ltt_msk','ltt_sib'}:
        block=dom.select_one('.product');assert block is not None
        for related in block.select('.catalog_item'):related.decompose()
        own=text(block);desc=own.split('Назначение',1)[-1] if 'Назначение' in own else own
        scope='OWN_PRODUCT_CARD_WITH_RELATED_CATALOG_ITEMS_REMOVED'
    elif e['supplier']=='ltt_yug':
        head=dom.h1;block=head.find_parent(lambda a:a.name=='div' and 'colPrevious' in a.get('class',[]));assert block is not None
        desc=' '.join(text(n) for n in dom.select('.wrapperCatalogPage .accordion-tabs .accordion-content'))
        own=text(block)+' '+desc;scope='OWN_TITLE_PRICE_COLUMN_PLUS_OWN_ACCORDION_TABS'
    else:
        block=dom.select_one('#div_print') or dom.select_one('#div_body') or dom.body or dom
        own=text(block);desc=own;scope='STATIC_CONTENT_WITH_ACCESSORY_SUBSECTIONS_RETAINED_AS_CONTEXT'
    desc=desc.split('Другие товары',1)[0]
    return dict(evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],charset=m['charset'],original_observed_at=m['observed_at'],evidence_path=str((r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).relative_to(o.ROOT)),headings=[dict(tag=h.name,text=text(h)) for h in block.select('h1,h2')],source_description=desc,source_excerpt=desc[:1800],own_text=own,scope=scope)

def candidate(item,facts):
    kind=item['current_product_kind'];title=item['title'];signals=[];spans=[]
    if kind not in MACHINE:return signals,spans
    for label,pattern,haystack in [('ACCESSORY_OR_COMPONENT_WORD_IN_SOLD_TITLE',ACCESSORY,title),('HOST_RELATION_IN_SOLD_TITLE',HOST_RELATION,title),('INSTALLATION_OR_APPLICATION_CONTEXT',INSTALL,facts['source_description'])]:
        matches=list(pattern.finditer(haystack))
        if matches:
            signals.append(label);spans.extend(dict(signal=label,text=haystack[max(0,m.start()-90):m.end()+140]) for m in matches[:10])
    sold=re.split(r'\s+(?:для|на|к|под|со)\s+',title,maxsplit=1,flags=re.I)[0]
    if not COMPLETE.search(sold):signals.append('MACHINE_FAMILY_WITHOUT_EXPLICIT_OWN_MACHINE_HEAD')
    return signals,spans

def architecture():
    calls=[]
    for dirname in ['sterbrust_matching','universal_supplier','scripts','tests']:
      for p in (o.ROOT/dirname).rglob('*.py'):
        if '__pycache__' in p.parts:continue
        try:tree=ast.parse(p.read_text(encoding='utf-8-sig'))
        except (SyntaxError,UnicodeDecodeError):continue
        aliases={'classify_product_kind'}
        for n in ast.walk(tree):
            if isinstance(n,ast.ImportFrom):
                for a in n.names:
                    if a.name=='classify_product_kind':aliases.add(a.asname or a.name)
        for n in ast.walk(tree):
            if isinstance(n,ast.Call) and ((isinstance(n.func,ast.Name) and n.func.id in aliases) or isinstance(n.func,ast.Attribute) and n.func.attr=='classify_product_kind'):
                calls.append(dict(path=str(p.relative_to(o.ROOT)),line=n.lineno,call=ast.unparse(n),file_sha256=r.digest(p.read_bytes()),test_or_report_only=dirname in {'scripts','tests'}))
    files=['sterbrust_matching/product_identity.py','sterbrust_matching/normalization.py','sterbrust_matching/matching.py','universal_supplier/adapters/ltt_uss_html.py','universal_supplier/adapters/ltt.py','universal_supplier/adapters/ltt_yug.py','universal_supplier/adapters/uss_stanko.py','universal_supplier/commercial_persistence.py','universal_supplier/ltt_uss_identity.py','universal_supplier/offer_selection.py','universal_supplier/identity_service.py','universal_supplier/characteristic_evidence.py']
    r.save(OUT/'CLASSIFIER_ARCHITECTURE.json',dict(status='REPOSITORY_VERIFIED',classifier='sterbrust_matching.product_identity.classify_product_kind',shared_across_all_four_sources=True,source_call_chain=['ltt.py:219 MSK/SIB -> source_evidence(name,category,properties,availability_raw)','ltt_yug.py:176 YUG -> same helper','uss_stanko.py:477 USS -> same helper','ltt_uss_html.py:74-87 -> classify_product_kind(name,category) plus sold-title spare/tooling overrides'],classifier_inputs=['name','category'],description_used_by_classifier=False,other_enrichment_inputs=['properties for manufacturer/model','availability_raw','brand'],ingestion_effect='product_kind copied into ProductCard.raw_data.enrichment_evidence; raw metadata copied to source_products and offers. It is not the source external-ID key or a numeric price/availability selector.',downstream_effect='The same classifier is called by matching/model-role/identity gate, characteristic profiles and enrichment/routing modules. Therefore its repository scope is broader than ingestion metadata. These pipelines were inspected, not executed.',offer_selection_direct_kind_reference=False,current_kind_is_inference_not_identity_authority=True,call_sites=calls,files={p:r.digest((o.ROOT/p).read_bytes()) for p in files}))

def architecture_rules():
    path=o.ROOT/'sterbrust_matching/product_identity.py';source=path.read_text(encoding='utf-8-sig');tree=ast.parse(source)
    rules=r.load(OUT/'CLASSIFIER_ARCHITECTURE.json');taxonomy=[]
    for node in tree.body:
        if isinstance(node,ast.AnnAssign) and getattr(node.target,'id',None)=='PRODUCT_TAXONOMY':
            taxonomy=[dict(kind=ast.literal_eval(a.args[0]),aliases=ast.literal_eval(a.args[1]),family=ast.literal_eval(a.args[2])) for a in node.value.elts]
        if isinstance(node,ast.Assign) and any(getattr(t,'id',None)=='COMPATIBILITY_MARKERS' for t in node.targets):rules['compatibility_markers']=ast.literal_eval(node.value)
    rules.update(ordered_taxonomy=taxonomy,machine_aliases=[a for a in taxonomy if a['family']=='machine'],accessory_tool_fixture_markers=[a for a in taxonomy if a['family'] in {'accessory','tooling','consumable'}],classifier_function=next(ast.get_source_segment(source,n) for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='classify_product_kind'),local_source_overrides=dict(spare='ролик*/запчаст*/ремень* in head before для',tooling='фреза*/сверло*/резец*/патрон* in same head, veto if literal станок present'))
    r.save(OUT/'CLASSIFIER_ARCHITECTURE.json',rules)

def inventory():
    resume=len(sys.argv)>1 and sys.argv[1]=='resume'
    assert resume==OUT.exists(),'Use explicit resume only for prepared audit facts'
    q.history()
    if resume:assert r.code_hashes()==r.load(OUT/'PREFLIGHT.json')['code_hashes']
    prior=r.load(q.OUT/'BLOCKER_ROOT_CAUSE.json')
    for a in prior['files']:assert r.digest((o.ROOT/a['path']).read_bytes())==a['sha256']
    architecture();items=[];risks=[];terminals=[];terminal_risks=[];counter=Counter()
    acceptedseal=r.load(q.accepted.P/'TERMINAL_BATCH_FIX_CODE_SEAL.json');assert r.code_hashes()==acceptedseal
    r.save(OUT/'PREFLIGHT.json',dict(status='PASS',code_hashes=acceptedseal,qa_artifact_seal_sha256=r.digest((q.OUT/'FINAL_ARTIFACT_SEAL.json').read_bytes()),baseline_sha256=r.digest(q.BASELINE.read_bytes()),plan_sha256=r.digest(q.PLAN.read_bytes()),db_started=False,qa_continuation=False,checked_at=r.now()))
    oldseal=r.load(q.OUT/'FINAL_ARTIFACT_SEAL.json')
    for key,sha in oldseal.items():assert r.digest((q.OUT/key).read_bytes())==sha
    with q.guard.guard() as network:
      for e in r.load(q.PLAN)['rows']:
        if e['outcome']=='NON_PRODUCT_CATEGORY_PAGE':continue
        path=o.ROOT/e['journal'];assert r.digest(path.read_bytes())==e['journal_sha256'];row=r.load(path)
        cached=OUT/('OWN_SOURCE_FACTS' if e['outcome']=='PERSISTED' else 'TERMINAL_SOURCE_FACTS')/e['supplier']/f"{e['index']:06d}.json"
        if cached.exists():
            facts=r.load(cached);assert facts['evidence_sha256']==row['result']['evidence_sha256'] and facts['inventory']['journal_sha256']==e['journal_sha256']
            facts={k:v for k,v in facts.items() if k!='inventory'}
        else:facts=source_facts(e,row)
        persisted=e['outcome']=='PERSISTED'
        if persisted:
            record=q.OUT/'PERSISTED_ROWS'/e['supplier']/path.name;db=r.load(record);p=db['parsed_product'];trace=p['enrichment_evidence']['product_kind']
            assert trace==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
            raw_evidence=p['enrichment_evidence'];title=p['name'];sourceid=e['external_id']
            dbref=dict(evidence_class='PACKAGED-EVIDENCE_FRESH_PRIOR_READONLY_QA',record=str(record.relative_to(o.ROOT)),sha256=r.digest(record.read_bytes()),source_product_id=db['product']['id'],offer_id=db['offer']['id'],capture_id=db['capture']['id'],observation_id=db['observation']['id'])
        else:
            product=r.parse(e['supplier'],(o.ROOT/facts['evidence_path']).read_bytes(),row['result']['final_url']);p=asdict(product);raw_evidence=p['enrichment_evidence'];trace=raw_evidence['product_kind'];title=p['name'];sourceid=product.site_internal_id
            dbref=dict(persisted=False,evidence_class='FROZEN_PARSER_ONLY_NO_DB_ENTITY')
        item=dict(supplier=e['supplier'],index=e['index'],external_id=sourceid,url=e['url'],outcome=e['outcome'],title=title,current_product_kind=trace['product_kind'],current_confidence=trace['confidence'],matched_alias=trace['matched_alias'],classifier_reason=trace['source'],raw_enrichment_evidence=raw_evidence,source_description_excerpt=facts['source_excerpt'],evidence_sha256=facts['evidence_sha256'],response_sha256=facts['response_sha256'],original_observed_at=facts['original_observed_at'],price=p['price'],availability=p['availability'],journal=e['journal'],journal_sha256=e['journal_sha256'],db_evidence=dbref)
        facts_path=OUT/('OWN_SOURCE_FACTS' if persisted else 'TERMINAL_SOURCE_FACTS')/e['supplier']/f"{e['index']:06d}.json";r.save(facts_path,dict(**facts,inventory=item))
        item.update(facts=str(facts_path.relative_to(OUT)),facts_sha256=r.digest(facts_path.read_bytes()))
        signals,spans=candidate(item,facts)
        if signals:
            c=dict(**item,risk_signals=signals,signal_evidence=spans,semantic_decision=None)
            (risks if persisted else terminal_risks).append(c)
        (items if persisted else terminals).append(item);counter[e['supplier']]+=1
        if (len(items)+len(terminals))%250==0:print(f'KIND INVENTORY {len(items)}persisted/{len(terminals)}terminal',flush=True)
    assert len(items)==2453 and len(terminals)==180 and len({(x['supplier'],x['index']) for x in items})==2453
    assert any(x['supplier']=='ltt_msk' and x['index']==129 for x in risks)
    policy=dict(scope='All machine-family classified rows, every confidence level; not only literal machine/HIGH',machine_family=sorted(MACHINE),title_accessory_pattern=ACCESSORY.pattern,title_host_relation_pattern=HOST_RELATION.pattern,description_installation_pattern=INSTALL.pattern,head_without_own_machine_noun='Split before first для/на/к/под/со, require own full-machine noun or include for review',candidate_is_not_defect=True,descriptions_not_classifier_inputs=True)
    r.save(OUT/'PRODUCT_KIND_INVENTORY.json',dict(total=2453,by_supplier=dict(Counter(x['supplier'] for x in items)),group_counts=[dict(supplier=s,product_kind=k,confidence=c,classifier_reason=why,count=n) for (s,k,c,why),n in sorted(Counter((a['supplier'],a['current_product_kind'],a['current_confidence'],a['classifier_reason']) for a in items).items())],rows=items))
    r.save(OUT/'PRODUCT_KIND_RISK_CANDIDATES.json',dict(policy=policy,persisted_count=len(risks),by_supplier=dict(Counter(x['supplier'] for x in risks)),rows=risks,semantic_review_status='PENDING'))
    r.save(OUT/'TERMINAL_KIND_INVENTORY.json',dict(total_computed=180,category_yug_not_computed=1,rows=terminals))
    r.save(OUT/'TERMINAL_KIND_RISK_CANDIDATES.json',dict(count=len(terminal_risks),rows=terminal_risks,terminal_decisions_unchanged=True,semantic_review_status='PENDING'))
    r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,db_started=False,db_writes=0,classifier_changes=0,parser_changes=0,full_qa_continued=False,run2=False))
    print(dict(inventoried=2453,risks=len(risks),by_supplier=dict(Counter(x['supplier'] for x in risks)),terminals=180,terminal_risks=len(terminal_risks)),flush=True)

if __name__=='__main__':inventory()
