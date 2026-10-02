import copy
import unittest
import json
from pathlib import Path
from universal_supplier.saved_full_enrichment import extract_payload


class FullTests(unittest.TestCase):
    def fixture(self):
        html='''<h1>Станок HTZ-800 RS</h1><div class="el_Main"><div class="el_Brand"><img alt="Бренд: MEBOR"><span>Словения</span></div>
        <div class="el_Images"><a data-fancybox="elPreview" href="/original.jpg" data-caption=""><img src="/thumb.jpg"></a></div>
        <div id="elTabDesc">Собственное описание</div><div id="elTabProp"><table>
        <tr><td>Ширина шкивов, мм</td><td>80</td></tr><tr><td>Ширина ленточной пилы, мм</td><td>100</td></tr>
        <tr><td>Скорость подачи, м/мин</td><td>0 – 60</td></tr><tr><td>Главный двигатель, кВт</td><td>18,5</td></tr>
        </table></div><div id="elTabFiles">Информационные файлы в процессе создания</div></div>
        <div id="elPrice">Цена по запросу</div><meta property="product:availability" content="preorder">
        <img src="/navigation.jpg"><div id="elTabLeas">1000000</div>'''
        scope=dict(source='intervesp',external_id='id',supplier_url='https://intervesp.ru/product/',capture='saved.html',capture_sha256='sha',new_group_id='g',new_candidate_id='c')
        proof=dict(model='HTZ-800 RS',section={'id':4583},typed_anchors={'blade_width':[100],'pulley_width':[80]},
                   gates=dict.fromkeys(['SOURCE_IDENTITY','INDEPENDENT_TYPED_IDENTITY','CROSS_SOURCE_DEDUP','CANONICAL_ABSENCE','SAFE_SECTION'],'PASS'))
        return html,scope,proof

    def payload(self):
        h,s,p=self.fixture();return extract_payload(h,s,p,'capture-time')

    def test_documents_not_found_not_blocker(self):
        p=self.payload();self.assertEqual(p['fields']['documents']['state'],'NOT_FOUND');self.assertTrue(p['readiness']['full_ready'])

    def test_request_not_zero_or_calculator_price(self):
        p=self.payload();self.assertIsNone(p['fields']['price']['value']);self.assertEqual(p['fields']['price_state']['value'],'price_on_request')

    def test_availability_own_metadata_not_price(self):
        p=self.payload();self.assertEqual(p['fields']['availability']['value'],'preorder')
        h,s,proof=self.fixture();h=h.replace('<meta property="product:availability" content="preorder">','')
        p=extract_payload(h,s,proof,'t');self.assertEqual(p['fields']['availability']['state'],'NOT_FOUND');self.assertIsNone(p['fields']['availability']['value'])

    def test_original_image_no_nav_and_empty_caption(self):
        p=self.payload();imgs=p['fields']['images']['value'];self.assertEqual(len(imgs),1)
        self.assertEqual(imgs[0]['original_url'],'https://intervesp.ru/original.jpg');self.assertEqual(imgs[0]['caption']['state'],'NOT_FOUND')

    def test_ranges_roles_not_collapsed(self):
        props=self.payload()['fields']['characteristics']['value']
        self.assertEqual(props[0]['role'],'pulley_width');self.assertEqual(props[1]['role'],'blade_width')
        self.assertEqual(props[2]['value'],'0 – 60');self.assertEqual(props[3]['value'],18.5);self.assertEqual(props[3]['unit'],'kW')

    def test_identity_conflict_blocks(self):
        h,s,p=self.fixture();h=h.replace('Станок HTZ-800 RS','Станок HTZ-800')
        self.assertFalse(extract_payload(h,s,p,'t')['readiness']['full_ready'])

    def test_missing_description_unknown_blocks(self):
        h,s,p=self.fixture();h=h.replace('<div id="elTabDesc">Собственное описание</div>','')
        out=extract_payload(h,s,p,'t');self.assertEqual(out['fields']['description']['state'],'UNKNOWN');self.assertFalse(out['readiness']['full_ready'])

    def test_article_not_invented(self):
        p=self.payload();self.assertEqual(p['fields']['manufacturer_article']['state'],'NOT_FOUND');self.assertIsNone(p['fields']['manufacturer']['value'])

    def test_critical_table_conflict_blocks(self):
        h,s,p=self.fixture();h=h.replace('<table>','<table><tr><td>Ширина ленточной пилы, мм</td><td>90</td></tr>')
        self.assertFalse(extract_payload(h,s,p,'t')['readiness']['full_ready'])

    def test_no_input_mutation_replay(self):
        h,s,p=self.fixture();old=copy.deepcopy((s,p));a=extract_payload(h,s,p,'t');b=extract_payload(h,s,p,'t')
        self.assertEqual(a,b);self.assertEqual((s,p),old)

    def test_optional_geometry_not_main_dimensions(self):
        h,s,p=self.fixture();h=h.replace('Собственное описание','''<div class="elPropBlock"><h2>Комплектация дополнительная</h2>
            <ul><li>Опциональный конвейер<ul><li>длина 4500 мм</li><li>ширина 400 мм</li></ul></li></ul></div>''')
        out=extract_payload(h,s,p,'t');self.assertEqual(len(out['optional_characteristics']),2)
        self.assertTrue(all(r['role']=='OPTIONAL_CONFIGURATION' for r in out['optional_characteristics']))
        self.assertEqual(len(out['fields']['characteristics']['value']),4)

    def test_gross_net_roles_separate(self):
        h,s,p=self.fixture();h=h.replace('<table>','<table><tr><td>Вес нетто, кг</td><td>180</td></tr><tr><td>Вес брутто, кг</td><td>210</td></tr>')
        rows=extract_payload(h,s,p,'t')['fields']['characteristics']['value']
        self.assertEqual([r['role'] for r in rows[:2]],['net_weight','gross_weight'])

    def test_power_role_and_range_not_scalar(self):
        h,s,p=self.fixture();h=h.replace('Главный двигатель, кВт','Главный двигатель S6, кВт').replace('18,5','18,5 / 26')
        rows=extract_payload(h,s,p,'t')['fields']['characteristics']['value']
        self.assertIn('s6',rows[-1]['role']);self.assertEqual(rows[-1]['value'],'18,5 / 26')

    def test_real_two_payloads_complete_and_only_two_rows_changed(self):
        root=Path(__file__).resolve().parents[1];out=root/'reports/RC_LOCAL/TWO_NEW_FULL_ENRICHMENT_2026-10-02'
        if not out.exists():self.skipTest('Full payload artifacts not generated')
        p=json.loads((out/'PRODUCT_CARD_PAYLOADS.json').read_text(encoding='utf-8'))
        self.assertEqual([len(x['fields']['characteristics']['value']) for x in p],[10,7])
        self.assertEqual([len(x['fields']['images']['value']) for x in p],[3,1])
        self.assertEqual([len(x['optional_characteristics']) for x in p],[0,4])
        self.assertTrue(all(x['readiness']['full_ready'] for x in p))
        self.assertTrue(all(not x['field_statuses']['UNKNOWN'] and not x['field_statuses']['CONFLICT'] for x in p))
        before=json.loads((root/'reports/RC_LOCAL/TWO_NEW_IDENTITY_ACCEPTED_2026-10-02/MATCHING_ACCEPTED.json').read_text(encoding='utf-8'))['rows']
        after=json.loads((out/'MATCHING_ACCEPTED.json').read_text(encoding='utf-8'))['rows']
        self.assertEqual(sum(a!=b for a,b in zip(before,after)),2)
        self.assertTrue(all(r['sterbrust_product_id'] is None for r in after if r['classification']=='READY_TO_CREATE_FULL'))
