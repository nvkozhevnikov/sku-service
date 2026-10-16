"""Actual rule extraction, frozen row82 call trace, collision witnesses."""
from scripts.audit_inverse_kind import *
import inspect
from bs4 import BeautifulSoup

def rules():
    rows=[]
    for t in identity.PRODUCT_TAXONOMY:
        for regex in t.aliases:
            if t.family!='machine':rows.append(dict(layer='SHARED_ORDERED_TAXONOMY',kind=t.kind,family=t.family,regex=regex,input='NFKC/lower/yo/dashes/whitespace normalized head/sold-span/title/category',guard='Explicit complete-machine head preferred; ordered first alias wins',source_path='sterbrust_matching/product_identity.py'))
    source=inspect.getsource(identity.classify_product_kind);tree=ast.parse(source)
    rule_node=next(n for n in ast.walk(tree) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='rules' for t in n.targets))
    for kind,regex in ast.literal_eval(rule_node.value):rows.append(dict(layer='SHARED_SOLD_OBJECT',kind=kind,regex=regex,input='normalized title',guard='Only prior machine family; match must precede first explicit machine noun',source_path='sterbrust_matching/product_identity.py'))
    extra={'vacuum_pump':r'\bнасос\w*\b','cutting_attachment':r'\bгильотин\w*\b','hand_edge_trimmer':r'\bподрезател\w*\s+кромк\w*\b'}
    for kind,regex in extra.items():
        assert regex in source
        rows.append(dict(layer='SHARED_SOLD_OBJECT_CORROBORATED',kind=kind,regex=regex,input='normalized title; own description corroboration',guard='Before first machine noun, plus vacuum/host-relation/manual-purpose predicates',source_path='sterbrust_matching/product_identity.py'))
    from universal_supplier.adapters import ltt_uss_html as shared
    local=inspect.getsource(shared.source_evidence);node=ast.parse(local)
    for n in ast.walk(node):
        if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='search' and isinstance(n.args[0],ast.Constant):
            regex=n.args[0].value
            if regex==r'\bстанок\b':continue
            rows.append(dict(layer='ADAPTER_SHARED_LOCAL_OVERRIDE',kind='spare' if 'ролик' in regex else 'tooling',regex=regex,input="name.lower().split(' для ')[0] (not NFKC/lemma)",guard='No machine-head veto' if 'ролик' in regex else 'Exact singular станок veto only',source_path='universal_supplier/adapters/ltt_uss_html.py'))
    for i,a in enumerate(rows):
        a.update(rule_id=i,semantics='REGEX_RE_SEARCH_WORD_BOUNDARY_WITH_PRODUCTIVE_STEM_PREFIX' if '\\w*' in a['regex'] else 'REGEX_RE_SEARCH_BOUNDARY_EXACT_OR_ENUMERATED_FORMS',lemmatizer=False,unanchored_search=True,left_boundary='\\b' in a['regex'],productive_stem='\\w*' in a['regex'],potential_collision='Morphological/compound continuation allowed after stem' if '\\w*' in a['regex'] else 'Exact/phrase forms; priority and ownership still require evidence')
    return rows

def trace82():
    e=next(e for e in r.load(prior.PLAN)['rows'] if e['supplier']=='ltt_msk' and e['index']==82);j=r.load(o.ROOT/e['journal']);m=j['result']
    body=(r.OUT/'EVIDENCE'/e['supplier']/(m['evidence_sha256']+'.html')).read_bytes();assert r.digest(body)==m['evidence_sha256']
    calls=[];original=identity.classify_product_kind
    def spy(name,category='',*,own_description=''):
        answer=original(name,category,own_description=own_description)
        calls.append(dict(name=name,category=category,own_description=own_description,answer=asdict(answer)))
        return answer
    with q.guard.guard() as network,patch('psycopg.connect',side_effect=AssertionError('OFFLINE_SQL_FORBIDDEN')),patch.object(identity,'classify_product_kind',new=spy):product=r.parse(e['supplier'],body,m['final_url'])
    assert len(calls)==1 and calls[0]['answer']['product_kind']=='machine' and product.enrichment_evidence['product_kind']['product_kind']=='spare'
    head=product.name.lower().split(' для ')[0];regex=r'\b(?:ролик\w*|запчаст\w*|ремень\w*)';match=re.search(regex,head)
    assert match.group()=='роликовой'
    facts=source_facts(e,j);db=r.load(QA/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name)
    r.save(OUT/'ROW82_EVIDENCE.json',dict(entry=e,source_title=product.name,category=product.source_category,source_facts=facts,real_classifier_calls=calls,
        description_selector='#product_descrption [itemprop="description"]',actual_classifier_description_empty=calls[0]['own_description']=='',adapter_override=dict(regex=regex,head=head,match=match.group(),span=match.span(),semantics='word-start boundary + productive prefix/stem; not internal arbitrary substring; matches full adjective via w-star',normalization='lowercase and literal split before space-для-space; no lemma',result=product.enrichment_evidence['product_kind']),
        parsed_product=asdict(product),db_source_kind=db['product']['raw_data']['enrichment_evidence']['product_kind'],db_offer_kind=db['offer']['raw_data']['enrichment_evidence']['product_kind'],db_evidence=dict(source_product_id=db['product']['id'],offer_id=db['offer']['id'],capture_id=db['capture']['id'],observation_id=db['observation']['id']),
        real_call_chain=['run_ltt_uss_run1.r.parse','universal_supplier.adapters.ltt.parse_ltt_modx_html','ltt_uss_html.source_evidence','product_identity.classify_product_kind: machine/HIGH','ltt_uss_html local spare override: spare/HIGH'],
        decision='CONFIRMED_FALSE_NON_MACHINE_COMPLETE_MACHINE',expected_kind='machine',source_class='PACKAGED-EVIDENCE',technical_class='REPOSITORY-VERIFIED',semantic_class='INFERENCE',network=network,db_writes=0))
    print('Row82 real call trace PASS; match=роликовой; classifier-description length',len(calls[0]['own_description']),flush=True)

def audit():
    actual=rules();items=r.load(OUT/'ALL_PERSISTED_KIND_INVENTORY.json')['rows']+r.load(OUT/'TERMINAL_INVERSE_KIND_INVENTORY.json')['rows']
    for a in actual:
        hits=[]
        for item in items:
            inputs={'TITLE':identity.normalize_text(item['title']),'CATEGORY':identity.normalize_text(item['source_category'])}
            if a['layer']=='ADAPTER_SHARED_LOCAL_OVERRIDE':inputs={'LOCAL_HEAD':item['title'].lower().split(' для ')[0]}
            for field,value in inputs.items():
                for m in re.finditer(a['regex'],value,re.I):
                    token=re.search(r'\w+',value[m.start():]);word=token.group() if token else m.group()
                    hits.append(dict(supplier=item['supplier'],index=item['index'],outcome=item['outcome'],field=field,match=m.group(),whole_token=word,span=m.span(),current_kind=item['current_product_kind'],current_reason=item['current_kind']['source'],title=item['title']))
        a.update(corpus_hits=len(hits),hit_rows=len({(h['supplier'],h['index']) for h in hits}),hits=hits)
    example_terms=['ролик','нож','пила','диск','патрон','упор','линейка','датчик','насос','резец','фреза','щетка','барабан','головка','узел']
    examples={term:[dict(rule_id=a['rule_id'],kind=a['kind'],regex=a['regex']) for a in actual if term in a['regex'] or term[:-1] in a['regex']] for term in example_terms}
    paths=['sterbrust_matching/product_identity.py','sterbrust_matching/normalization.py','universal_supplier/adapters/ltt_uss_html.py','universal_supplier/adapters/ltt.py','universal_supplier/adapters/ltt_yug.py','universal_supplier/adapters/uss_stanko.py']
    r.save(OUT/'MARKER_COLLISION_AUDIT.json',dict(status='REPOSITORY_RULES_AND_FULL_CORPUS_HITS_INVENTORIED',rules=actual,rule_count=len(actual),configured_taxonomy=[asdict(t) for t in identity.TAXON_BY_KIND.values()],compatibility_markers=list(identity.COMPATIBILITY_MARKERS),compatibility_matching='re.search each normalized marker, choose earliest; boundaries present, no lemmatizer',example_marker_coverage=examples,
        four_source_callers=[dict(path=p,line=i+1,call=line.strip()) for p in paths[-3:] for i,line in enumerate((o.ROOT/p).read_text('utf8').splitlines()) if 'source_evidence(' in line],code_sha256={p:r.digest((o.ROOT/p).read_bytes()) for p in paths},
        risk_notes=['Initial word boundary prevents arbitrary mid-word matches, but productive stem w-star admits adjectives and longer compounds.','Local spare override unconditionally replaces correct machine evidence; source scope all four adapters.','Shared table/disk etc use stem patterns (стол*/круг*) that can match столяр*/круглопил* in category; this can be wrong non-machine subtype without being inverse machine defect.','Shared sold-object rules use machine-order and corroboration guards; do not globally remove markers.','Regex collision witness is not itself semantic defect; inspect own sold object.','нож is exact word in sold-object rules, пила узла is exact phrase, not generic all-пила marker.','щетка/барабан/головка are not configured standalone non-machine aliases in current ingestion classifier; no invented rule.'],source_class='REPOSITORY-VERIFIED',supplier_http=0,db_writes=0))
    print('Configured markers',len(actual),'full corpus rows',len(items),flush=True)

if __name__=='__main__':{'trace82':trace82,'audit':audit}[sys.argv[1]]()
