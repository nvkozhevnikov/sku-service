"""Offline inverse kind inventory. Never repairs source code or persisted state."""
from pathlib import Path
import sys,re,ast
from dataclasses import asdict
from collections import Counter
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts import qa_product_kind29_fresh as prior
from scripts.audit_ltt_uss_product_kind import source_facts
from sterbrust_matching import product_identity as identity
r,o,q=prior.r,prior.o,prior.q
P=prior.P;QA=prior.OUT
OUT=r.BASE/'INVERSE_KIND_IMPACT_SWEEP_2026-10-16'
MACHINE={k for k,t in identity.TAXON_BY_KIND.items() if t.family=='machine'}
MACHINE_WORDS=re.compile(r'\b(?:стан(?:ок|ки|ка|ком|ке|ков)|пила|пилы|пил(?:ьн|ящ)\w*|компрессор\w*|пресс\w*|гильотин\w*|рейсмус\w*|фуган\w*|строгальн\w*|фрезер\w*|гравер\w*|шлифмашин\w*|шлифовал\w*|сверлил\w*|заточн\w*|полировал\w*|стружкоотсос\w*|пылеотсос\w*|пылеулавлива\w*|аспирац\w*|пылесос\w*|дробил\w*|измельчит\w*|сушил\w*|упаковоч\w*|кромкооблицов\w*|вытяжн\w*|вентилятор\w*|таль|электротельфер\w*|кран\w*|насос\w*|чиллер\w*|автоподатчик\w*|подрезател\w*)\b',re.I)
SPEC_GROUPS=[r'мощност|квт|электродвигател',r'масса|вес',r'габарит|длина|ширина|высота',r'обработк|рабоч\w*\s+(?:стол|поле)|заготовк|реза|производительност',r'напряжен|электропитан']
SUPPLEMENTAL_EQUIPMENT_WORDS=re.compile(r'\b(?:центр\w*|установк\w*|машин\w*|камер\w*|погрузчик\w*|стреппинг\w*|тал(?:ь|и|ей)|электротельфер\w*|домкрат\w*|шасси|тележк\w*|лебедк\w*|кантовател\w*|позиционер\w*|траверс\w*|рампа|рампы)\b',re.I)

def unchanged():
    prior.history()
    for n,sha in r.load(QA/'FINAL_ARTIFACT_SEAL.json').items():assert r.digest((QA/n).read_bytes())==sha,('QA_EVIDENCE_DRIFT',n)

def preflight():
    assert not OUT.exists(),'New inventory must not overwrite prior evidence'
    unchanged();assert r.load(QA/'FINAL_CHECKPOINT.json')['status']=='LTT_USS_FULL_QA_BLOCKED'
    assert not (r.BASE/'PRIVATE/pg17/postmaster.pid').exists()
    baseline=r.load(prior.BASELINE)
    assert baseline['totals']==dict(source_products=2453,offers=2453,supplier_http_captures=2533,offer_commercial_observations=2533)
    r.save(OUT/'PREFLIGHT.json',dict(status='PASS',code_seal_sha256=r.digest((P/'PRODUCT_KIND_CODE_SEAL.json').read_bytes()),baseline_sha256=r.digest(prior.BASELINE.read_bytes()),parent_qa_seal_sha256=r.digest((QA/'FINAL_ARTIFACT_SEAL.json').read_bytes()),git=q.git(),database_started=False,sql_connections=0,source_class='PACKAGED-EVIDENCE',checked_at=r.now()))

def candidates(item,facts):
    signals=[];spans=[]
    for field,haystack in [('TITLE',item['title']),('CATEGORY',item['source_category'])]:
        matches=list(MACHINE_WORDS.finditer(haystack))
        if matches:signals.append('COMPLETE_MACHINE_OR_HOST_WORD_IN_'+field);spans.extend(dict(field=field,text=m.group(0),start=m.start(),end=m.end()) for m in matches)
    description=facts['source_description'];features=[p for p in SPEC_GROUPS if re.search(p,facts['own_text'],re.I)]
    if len(features)>=3 and re.search(r'\bстан(?:ок|ки|ка|ком|ке)|\b(?:машин|оборудован)\w*\s+предназнач',description,re.I):signals.append('OWN_MACHINE_DESCRIPTION_AND_MULTIPLE_SPEC_FAMILIES')
    if item['current_product_kind'] in {'unknown','accessory','tool','tooling','spare','disk','table'} and len(features)>=4:signals.append('NON_MACHINE_KIND_WITH_FOUR_MACHINE_SPEC_FAMILIES')
    more=list(SUPPLEMENTAL_EQUIPMENT_WORDS.finditer(item['title']))
    if more:
        signals.append('ADDITIONAL_COMPLETE_EQUIPMENT_OR_HOST_NOUN_IN_TITLE')
        spans.extend(dict(field='TITLE',text=m.group(),start=m.start(),end=m.end()) for m in more)
    return signals,spans,features

def inventory():
    assert r.load(OUT/'PREFLIGHT.json')['status']=='PASS';assert not (OUT/'INVERSE_KIND_NON_MACHINE_INVENTORY.json').exists()
    plan=r.load(prior.PLAN)['rows'];baseline=r.load(prior.BASELINE)
    projections={(p['supplier_id'],p['external_id']):(p,offer) for p,offer in baseline['product_offer_projections']}
    allrows=[];non=[];risks=[];terminal=[];terminal_risks=[];last=None
    try:
      with q.guard.guard() as network, patch('psycopg.connect',side_effect=AssertionError('SQL_CONNECTION_NOT_AUTHORIZED_FOR_OFFLINE_INVENTORY')):
        for n,e in enumerate(plan,1):
          last=e;jpath=o.ROOT/e['journal'];assert r.digest(jpath.read_bytes())==e['journal_sha256'];j=r.load(jpath)
          outcome,product,capture=o.extract(j);assert outcome==e['outcome']
          if outcome=='NON_PRODUCT_CATEGORY_PAGE':continue
          m=j['result'];body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes()
          if product is None:product=r.parse(e['supplier'],body,m['final_url'])
          parsed=asdict(product);kind=parsed['enrichment_evidence']['product_kind'];persisted=outcome=='PERSISTED'
          dbref=dict(persisted=False,evidence_class='FROZEN_COMPUTED_TERMINAL_NO_ENTITY')
          if persisted:
            record_path=QA/'PERSISTED_ROWS'/e['supplier']/jpath.name;db=r.load(record_path)
            assert r.encoded(parsed)==r.encoded(db['parsed_product']),'CURRENT_PARSER_VS_SEALED_SQL_RECORD_DRIFT'
            assert r.encoded([db['product'],db['offer']])==r.encoded(projections[db['product']['supplier_id'],e['external_id']])
            assert kind==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
            dbref=dict(evidence_class='PACKAGED-EVIDENCE_CURRENT_ACCEPTED_BASELINE_AND_LAST_QA',record=str(record_path.relative_to(o.ROOT)),record_sha256=r.digest(record_path.read_bytes()),source_product_id=db['product']['id'],offer_id=db['offer']['id'],capture_id=db['capture']['id'],observation_id=db['observation']['id'])
          family=identity.TAXON_BY_KIND[kind['product_kind']].family if kind['product_kind'] in identity.TAXON_BY_KIND else 'unknown'
          item=dict(e);item.update(external_id=product.site_internal_id,title=product.name,source_category=product.source_category,supplier_model=product.supplier_model,manufacturer_article=product.manufacturer_article,current_product_kind=kind['product_kind'],current_family=family,current_kind=kind,price=parsed['price'],availability=product.availability,
                    enrichment_evidence=parsed['enrichment_evidence'],technical_properties=parsed['technical_properties'],evidence_sha256=m['evidence_sha256'],response_sha256=m['response_sha256'],original_observed_at=m['observed_at'],db_evidence=dbref)
          # all2453 are replayed, including machine-family exclusions; terminals separate.
          if persisted:allrows.append(item)
          if family!='machine' or not persisted:
            facts=source_facts(e,j);path=OUT/('OWN_SOURCE_FACTS' if persisted else 'TERMINAL_SOURCE_FACTS')/e['supplier']/f"{e['index']:06d}.json"
            r.save(path,dict(**facts,inventory=item));item.update(facts=str(path.relative_to(OUT)),facts_sha256=r.digest(path.read_bytes()))
            signals,spans,specs=candidates(item,facts)
            item.update(risk_signals=signals,signal_spans=spans,spec_families=specs)
            if persisted:
              non.append(item)
              if signals:risks.append(dict(**item,semantic_decision=None))
            else:
              terminal.append(item)
              if family!='machine' and signals:terminal_risks.append(dict(**item,semantic_decision=None))
          if n%200==0:
            r.save(OUT/'CHECKPOINT.json',dict(status='OFFLINE_INVERSE_INVENTORY_RUNNING',scope_checked=n,persisted=len(allrows),non_machine=len(non),risk_candidates=len(risks),terminal=len(terminal),database_started=False,db_writes=0,supplier_http=0,checked_at=r.now()))
            print(f'INVERSE INVENTORY scope{n}/2634 persisted{len(allrows)} non_machine{len(non)} candidates{len(risks)} terminal{len(terminal)}',flush=True)
        assert len(allrows)==2453 and len(terminal)==180 and len({(x['supplier'],x['external_id']) for x in allrows})==2453
        assert len(non)==1344 and len({(x['supplier'],x['index']) for x in risks})==len(risks)
        assert any(x['supplier']=='ltt_msk' and x['index']==82 for x in risks)
        r.save(OUT/'ALL_PERSISTED_KIND_INVENTORY.json',dict(total=2453,machine_family_kinds=sorted(MACHINE),non_machine_count=len(non),by_supplier=dict(Counter(a['supplier'] for a in allrows)),rows=allrows))
        r.save(OUT/'INVERSE_KIND_NON_MACHINE_INVENTORY.json',dict(total=len(non),scope=2453,includes_unknown_and_standalone_equipment=True,group_counts=[dict(supplier=s,kind=k,count=v) for (s,k),v in sorted(Counter((a['supplier'],a['current_product_kind']) for a in non).items())],rows=non))
        r.save(OUT/'INVERSE_KIND_RISK_CANDIDATES.json',dict(count=len(risks),by_supplier=dict(Counter(a['supplier'] for a in risks)),rows=risks,semantic_review_status='PENDING',policy='Inclusive title/category machine or equipment words, own machine purpose +3 spec families, or4 spec families in broad/unknown kind. A signal is not a defect. All non-machine rows inventoried, all2453 freshly replayed.'))
        r.save(OUT/'TERMINAL_INVERSE_KIND_INVENTORY.json',dict(total=180,non_machine_count=sum(a['current_family']!='machine' for a in terminal),yug_category_not_product=1,rows=terminal))
        r.save(OUT/'TERMINAL_INVERSE_KIND_RISK_CANDIDATES.json',dict(count=len(terminal_risks),rows=terminal_risks,semantic_review_status='PENDING'))
        r.save(OUT/'OFFLINE_ZERO_PROOF.json',dict(**network,sql_connections=0,db_writes=0,source_writes=0,database_started=False,persisted_rows_replayed=2453,terminal_rows_computed=180,category_rows=1,full_qa_continued=False,checked_at=r.now()))
      print('Inventory complete: nonmachine',len(non),'candidates',len(risks),'terminal_candidates',len(terminal_risks),flush=True)
    except BaseException as error:
      r.save(OUT/'BLOCKER.json',dict(status='LTT_USS_INVERSE_PRODUCT_KIND_IMPACT_SWEEP_BLOCKED',error=f'{type(error).__name__}:{error}',row=last,partial_scope=len(allrows),non_machine=len(non),supplier_http=0,db_writes=0));raise

if __name__=='__main__':{'preflight':preflight,'inventory':inventory}[sys.argv[1]]()
