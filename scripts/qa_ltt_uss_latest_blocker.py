"""Record first material semantic defect and close read-only SQL state; no fix."""
from dataclasses import asdict
from bs4 import BeautifulSoup
from scripts import qa_ltt_uss_latest as q
from scripts.qa_ltt_uss_latest_semantic_facts import read
import psycopg
r,o,OUT=q.r,q.o,q.OUT

def record():
    assert not (OUT/'BLOCKER.json').exists()
    manifest=r.load(OUT/'FRESH_400_SAMPLE_MANIFEST.json');rows=manifest['rows'];position=next(i for i,e in enumerate(rows) if e['supplier']=='ltt_msk' and e['index']==129)
    assert position==11
    e=rows[position];path=OUT/'SEMANTIC_PACKETS'/e['supplier']/(o.ROOT/e['journal']).name;packet=r.load(path);db=r.load(OUT/e['persisted_record']);p=db['parsed_product']
    assert e['external_id']=='1910' and db['product']['id']==185 and db['offer']['id']==185
    assert p['enrichment_evidence']['product_kind']['product_kind']=='machine'
    assert db['product']['raw_data']['enrichment_evidence']['product_kind']==db['offer']['raw_data']['enrichment_evidence']['product_kind']==p['enrichment_evidence']['product_kind']
    assert 'устанавливается в Т-образный паз на каретке' in packet['content_text']
    assert packet['stable_ids']==['1910']
    rawhtml=BeautifulSoup(packet['content_html'],'html.parser')
    description=packet['content_text'].split('Назначение',1)[1].split('Преимущества',1)[0].strip()
    evidence=dict(supplier=e['supplier'],index=129,source_id='1910',url=e['url'],source_title=packet['own_title'],source_description=description,
        own_form_ids=packet['stable_ids'],source_price=packet['numeric_price_cells'],source_availability=packet['stock_spans'],
        expected=dict(sold_object='Пневматическая прижимная балка для установки на каретку форматно-раскроечного станка',kind_group='accessory/fixture',not_complete_machine=True),
        actual=dict(parsed_kind=p['enrichment_evidence']['product_kind'],source_product_id=185,source_product_kind=db['product']['raw_data']['enrichment_evidence']['product_kind'],offer_id=185,offer_kind=db['offer']['raw_data']['enrichment_evidence']['product_kind'],observation_id=db['observation']['id'],capture_id=db['capture']['id']),
        raw_response_sha256=packet['response_sha256'],evidence_sha256=packet['evidence_sha256'],charset=packet['charset'],original_observed_at=packet['original_observed_at'],
        journal=e['journal'],journal_sha256=e['journal_sha256'],evidence_path=packet['evidence_path'],packet=str(path.relative_to(OUT)),packet_sha256=r.digest(path.read_bytes()),persisted_record=e['persisted_record'],persisted_record_sha256=r.digest((OUT/e['persisted_record']).read_bytes()),
        source_class='PACKAGED-EVIDENCE',current_db_class='REPOSITORY-VERIFIED_ISOLATED_READONLY_SQL',semantic_assessment='INFERENCE_BACKED_BY_OWN_SOURCE_DESCRIPTION',confidence='HIGH')
    r.save(OUT/'BLOCKER_EVIDENCE.json',evidence)
    r.atomic_bytes(OUT/'BLOCKER_OWN_CARD.html',packet['content_html'].encode('utf8'))
    notes={
        278:'Гидравлическая гильотинаMD310 — самостоятельная машина; собственные title/ID,1200000RUB и нет в наличии согласуются с source/parser/DB.',
        349:'Кронштейн/держатель шпинделя — fixture, не полная машина; собственная цена4000RUB/в наличии сохранена.',
        116:'Собственный сверлильно-присадочныйMZВ2А(2шпинделя), drill_press;175000RUB/в наличии. Два шпинделя не два товара.',
        303:'Универсальный эксцентриковый кулачковый прижим продаётся как зажимная принадлежность, не полный станок;6000RUB/в наличии. Канонический подтип оснастки не сопоставлялся.',
        298:'Ролики для автоподатчиков сохранены как spare, не автоподатчик;2800RUB/в наличии, собственныйID.',
        290:'Холодный прессLTT50T2/TGMY50T сохранён под своим title/ID;435000RUB/нет в наличии. Неопределённыйkind не подменяет исходное название.',
        48:'Самостоятельный винтовой компрессорHS5HP с ресивером130л,102800RUB/в наличии; unknownkind не фабрикует другую сущность, комплект не разделён.',
        39:'Один комплект насадных фрезR2,5000RUB/в наличии; принадлежность/оснастка не выдана за целую машину и не разделена на компоненты.',
        291:'Собственный станок горячего тисненияMSY1000;972864RUB. Источник не публикует stockspan, availability остаётся неизвестным.',
        41:'Собственная радиусная фреза ф40 — tooling,2500RUB/в наличии, не станок по совместимости.',
        1:'Собственный рейсмусMB106D б/у с электроподъёмом;230000RUB/в наличии. Состояние б/у сохранено в названии, не подмена новым станком.'}
    assert {a['index'] for a in rows[:position]}==set(notes)
    results=[]
    for n,item in enumerate(rows):
        status='PASS' if n<position else 'FAIL' if n==position else 'NOT_REVIEWED_AFTER_FIRST_MATERIAL_BLOCKER'
        result=dict(supplier=item['supplier'],index=item['index'],external_id=item['external_id'],url=item['url'],status=status)
        if n<=position:
            f=read(item);assert not f['issues'];result.update(source_comparison=f,reason=notes[item['index']] if n<position else 'Источник продаёт устанавливаемую на станок пневмобалку, но parser/source_product/offer маркируют её machine/HIGH по слову станок в описании совместимости.',decision_evidence_class='PACKAGED-EVIDENCE_AND_CURRENT_READONLY_DB',prior_PASS_not_carried=True)
        results.append(result)
    r.save(OUT/'FRESH_400_SEMANTIC_RESULTS.json',dict(status='BLOCKED_FIRST_MATERIAL_DEFECT',selected=400,reviewed=12,passed=11,failed=1,unreviewed=388,reviewed_by_supplier=dict(ltt_msk=12,ltt_sib=0,ltt_yug=0,uss_stanko=0),rows=results))
    r.save(OUT/'HISTORICAL_FIX_REGRESSION_AUDIT.json',dict(status='FRESH_SEMANTIC_REVIEW_NOT_REACHED_DUE_FIRST_BLOCKER',required_indices=sorted(q.FIXED),selected_count=15,structural_automated_trace_count=15,semantic_review_count=0,prior_PASS_not_substituted=True))
    r.save(OUT/'SPECIAL_CASE_AUDIT.json',dict(status='NOT_COMPLETED_DUE_FIRST_MATERIAL_BLOCKER',fresh_operator_ambiguous_recheck=6,risk_multi_variant_spotcheck=9,sib_unit_rows_selected=8,sib_unit_fresh_semantic_reviewed=0,yug312_297_structural_and_db_present_distinct=True,yug_id1_structural_role_and_snapshot_absence=True,uf100_uf200_parser_tests_and_db_snapshot_pass=True,full_special_semantic_acceptance=False))
    blocker=dict(status='LTT_USS_FULL_QA_BLOCKED',source='ltt_msk',row=129,source_id='1910',url=e['url'],qa_layer='FRESH_PERSISTED_SEMANTIC_PRODUCT_KIND_SAFETY',blocker='Accessory/fixture is persisted as complete machine kind',severity='QA_BLOCKER_HIGH',expected=evidence['expected'],actual=evidence['actual'],confirmed_scope=[dict(supplier='ltt_msk',index=129,external_id='1910',source_product_id=185,offer_id=185)],possible_wider_scope='UNKNOWN: other accessory titles mentioning a target machine may share the kind-classification risk; not investigated after STOP',wider_scope_class='INFERENCE_NOT_CONFIRMED',db_correction_required=True,db_correction_scope='Wrong product_kind metadata in source_products.raw_data.enrichment_evidence and offers.raw_data.enrichment_evidence; source identity/price/availability are coherent. No correction authorized in this QA.',supplier_http=0,db_writes=0,source_repairs=False,structural_rows_checked=2634,persisted_automated_checked=2453,semantic_selected=400,semantic_reviewed=12,semantic_pass=11,semantic_fail=1,semantic_unreviewed=388,terminal_mapping_ambiguous=114,operator_ambiguous_fresh_recheck=6,terminal_mapping_multi_variant=66,risk_multi_variant_spotcheck=9,full_qa_verified=False,finalizer_started=False,matching_run=False,offer_selection_run=False,sterbrust_writes=0,esol_writes=0,safe_to_power_off=False,next='READONLY_AFTER_STATE_AND_CLEAN_SHUTDOWN',evidence='BLOCKER_EVIDENCE.json',checked_at=r.now())
    r.save(OUT/'BLOCKER.json',blocker);r.save(OUT/'CHECKPOINT.json',blocker)
    # Required after-state is read-only closeout, not further semantic QA.
    with q.guard.guard() as network:
      _,cfg=r.config()
      with psycopg.connect(**cfg,autocommit=True) as raw:
        raw.execute('SET default_transaction_read_only=on');raw.execute("SET application_name='ltt_uss_latest_qa_blocker_readonly_closeout'")
        assert raw.execute('SHOW transaction_read_only').fetchone()[0]=='on'
        c=q.guard.ReadOnlyConnection(raw);after=o.snapshot(c);before=r.load(OUT/'BEFORE_QA.json')
        assert all(r.encoded(before[k])==r.encoded(v) for k,v in after.items())
        verified=c.execute('''SELECT p.external_id,p.name,p.raw_data->'enrichment_evidence'->'product_kind',o.raw_data->'enrichment_evidence'->'product_kind'
          FROM source_products p JOIN suppliers s ON s.id=p.supplier_id JOIN offers o ON o.source_product_id=p.id
          WHERE s.code=%s AND p.external_id=%s''',('ltt_msk','1910')).fetchall()
        assert len(verified)==1 and verified[0][2]['product_kind']==verified[0][3]['product_kind']=='machine'
        clients=o.clients(c)
        r.save(OUT/'BLOCKER_DB_VERIFICATION.json',dict(rows=verified,read_only=True,clients=clients,db_correction_applied=False))
        r.save(OUT/'AFTER_QA.json',dict(**after,checked_at=r.now()))
        r.save(OUT/'PROTECTED_STATE_COMPARISON.json',dict(status='PASS',tables_compared=51,sequences_compared=40,protected_tables=46,table_fingerprint_delta=0,sequence_delta=0,supplier_namespace_delta=0,foreign_mutations=0,source_db_writes=0,clients=clients))
        r.save(OUT/'HTTP_ZERO_PROOF.json',dict(**network,source_persistence_calls=0,sql_writes=0,read_only_sql_enforced=True))
    q.history();r.save(OUT/'GIT_AFTER_QA.json',q.git())
    print(dict(status=blocker['status'],source='ltt_msk',row=129,source_id='1910',semantic_reviewed=12,pass_count=11,fail_count=1,DB_WRITES=0,protected_state='51tables/40sequences unchanged'),flush=True)

if __name__=='__main__':record()
