"""Fresh source witnesses and explicit decisions; no source or SQL mutations."""
from scripts.qa_product_kind29_fresh import *
from scripts import qa_ltt_uss_latest_semantic_facts as facts
facts.OUT=OUT

def terminals():
    operator={1:'Family H1 drills/screwdrivers; own ST PRO H2 and article2404210 do not prove a stable child identity for this family URL.',12:'Family H1 blow guns; BP PRO Duo2102190/2760RUB is a child row, family identity still unresolved by explicit operator policy.',17:'Family H1 grinding machines/engravers; WST PRO and separate abrasive accessory section; child commercial data does not authorize family ingestion.',306:'DPA21 technical sheet; no commercial article, price, stock or purchase control.',307:'DPA22 technical sheet; no commercial article, price, stock or purchase control.',318:'DRO5 technical display/sensor sheet; no commercial article, price, stock or purchase control.'}
    multi={27:'Two main Mobilboy301/24 executions with distinct articles2003325/26 and42660/36840 prices.',50:'Two main TKS315E electrical executions220/380V,articles5903315/16 and separate controls; saw accessory not the basis.',86:'Multiple sold accessories including independent ALA2/ALA10 article5121503/04 prices7740/18240, not a single BRS1 set.',131:'Main CDS6250B/C columns each have own POR/stock and differing spindle82/105mm. Not inferred from dimension lists.',182:'Four DDP executions,4 articles3359011/12/13/15 and4 separate prices/orders.',285:'Main GU1 has220/380V executions3107110/09 priced72420/75660; accessory duct/light excluded as basis.',300:'Main BSI100/140 distinct3000210/14 with13980/20460 and own controls; other tooling separate.',317:'Three main stands8574010/12/13 at31896/39138/39468 with separate controls. Vibration feet not basis; repeated HTML anchor not identity proof.',328:'Four main stands8353000S/8353010/8574018/19 with18420/23472/33396/45960 and independent orders; host machines and feet not confused with sold stands.'}
    snapshot=r.load(OUT/'BEFORE_QA.json');ids={p['external_id'] for p,offer in snapshot['product_offer_projections']}
    for filename,notes,key in [('AMBIGUOUS_DECISION_VERIFICATION.json',operator,'operator_facts'),('MULTI_VARIANT_DECISION_VERIFICATION.json',multi,'risk_facts')]:
        data=r.load(OUT/filename)
        for row in data['rows']:assert row['source_id'] not in ids
        assert {a['index'] for a in data[key]}==set(notes)
        for a in data[key]:
            assert r.digest((OUT/a['facts']).read_bytes())==a['sha256']
            a.update(semantic_decision='PASS',fresh_semantic_reason=notes[a['index']],source_class='PACKAGED-EVIDENCE',interpretation_class='INFERENCE',prior_passes_used=False)
        data.update(status='PASS',fresh_review_count=len(notes),current_terminal_products_absent=True)
        r.save(OUT/filename,data)
    from scripts.qa_ltt_uss_final_packets import source_packet
    from sterbrust_matching.product_identity import classify_product_kind
    rows=[]
    for e in r.load(PLAN)['rows']:
        if e['supplier']!='uss_stanko' or e['index'] not in {191,194,195,196,197}:continue
        packet=source_packet(e);title=next(h['text'] for h in packet['headings'] if h['tag']=='h1');kind=classify_product_kind(title,'')
        assert kind.product_kind=='cutting_tool' and kind.confidence=='HIGH' and e['outcome']=='AMBIGUOUS' and e['external_id'] not in ids
        r.save(OUT/'TERMINAL_KIND_PACKETS'/f"{e['index']:06d}.json",packet)
        rows.append(dict(index=e['index'],title=title,kind=kind.__dict__ if hasattr(kind,'__dict__') else str(kind),outcome=e['outcome'],status='PASS',reason='Own headings and commercial rows sell cutting cutters/sets/inserts; host lathe is applicability, not sold machine. Multiple independent products remain fail-closed terminal.',evidence_sha256=packet['evidence_sha256'],prior_passes_used=False))
    assert len(rows)==5;r.save(OUT/'TERMINAL_KIND_5.json',dict(status='PASS',count=5,rows=rows,db_products_created=0,terminal_outcome_delta=0))
    print('Fresh terminal operator6/risk9/cutting5 PASS')

def mappings():
    from scripts.audit_ltt_uss_product_kind import OUT as audit
    decisions=r.load(audit/'PRODUCT_KIND_SEMANTIC_DECISIONS.json')['rows']
    cohort=[a for a in decisions if a['semantic_decision']=='VALID_MACHINE']
    plan={(e['supplier'],e['index']):e for e in r.load(PLAN)['rows']};rows=[]
    for a in cohort:
        e=plan[a['supplier'],a['index']];j=r.load(o.ROOT/e['journal']);m=j['result']
        assert e['external_id']==a['external_id'] and e['url']==a['url'] and m['evidence_sha256']==a['evidence_sha256']
        db=r.load(OUT/'PERSISTED_ROWS'/e['supplier']/(o.ROOT/e['journal']).name)
        kind=db['parsed_product']['enrichment_evidence']['product_kind']
        assert kind==a['raw_enrichment_evidence']['product_kind']==db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']
        rows.append(dict(supplier=e['supplier'],index=e['index'],external_id=e['external_id'],url=e['url'],evidence_sha256=m['evidence_sha256'],current_kind=kind,mapping='PASS',fresh_semantic=False))
    assert len(rows)==362
    r.save(OUT/'VALID_MACHINE_MAPPING.json',dict(status='PASS',count=362,rows=rows,false_machine_downgrade=0,fresh_semantic_subset_pending=True))
    print('362 exact current decision mappings, no new semantic approvals')

def show(supplier,start=0,end=100):
    selected=[e for e in r.load(OUT/'FRESH_400_SAMPLE_MANIFEST.json')['rows'] if e['supplier']==supplier]
    for e in selected[start:end]:
        f=facts.read(e);packet=r.load(OUT/f['packet']);db=r.load(OUT/e['persisted_record'])
        f['source_kind_context']=packet['content_text']
        f['raw_availability']=db['parsed_product']['enrichment_evidence']['availability_raw']
        f['db_source_kind']=db['product']['raw_data']['enrichment_evidence']['product_kind'];f['db_offer_kind']=db['offer']['raw_data']['enrichment_evidence']['product_kind']
        assert f['kind']==f['db_source_kind']==f['db_offer_kind']
        r.save(OUT/'SEMANTIC_FACTS'/supplier/f"{e['index']:06d}.json",f)
        print(f"{e['index']} ID{e['external_id']}|{f['title']}|{f['kind']['product_kind']}|{f['source_price']['raw']} ->{f['parsed_price']['current_price']} {f['parsed_price']['currency']} {f['parsed_price']['state']}|{f['source_stocks']} ->{f['parsed_availability']} raw={f['raw_availability']}|issues={f['issues']}")

if __name__=='__main__':
    if sys.argv[1]=='mapping':mappings()
    elif sys.argv[1]=='terminals':terminals()
    else:show(sys.argv[1],int(sys.argv[2]) if len(sys.argv)>2 else 0,int(sys.argv[3]) if len(sys.argv)>3 else 100)
