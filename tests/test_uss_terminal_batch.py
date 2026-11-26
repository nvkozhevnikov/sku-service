"""Exact frozen-source regressions and adversarial local ownership boundaries."""
from dataclasses import asdict
from pathlib import Path
import pytest
from bs4 import BeautifulSoup
from scripts import uss_terminal_batch_fix as b
from universal_supplier.adapters.uss_stanko import parse_uss_stanko_detail as parse
from universal_supplier.ltt_uss_capture import _card_gate

FIXTURES=Path('tests/fixtures/uss_terminal_batch')
PROVENANCE={x['index']:x for x in b.r.load(FIXTURES/'PROVENANCE.json')}

def saved(i):
    p=PROVENANCE[i];raw=Path(p['fixture']).read_bytes();assert b.r.digest(raw)==p['sha256']
    return raw.decode('utf8'),p['url']

@pytest.mark.parametrize('i,article,amount,model',[
    (20,'210 2200',2100,None),(53,'510 0004',1440,None),(61,'591 0519',420,None),
    (64,'591 0180',4740,'WM 180'),(65,'591 0360',None,'WS 360'),
    (297,'857 4017',8784,None),(305,'857 4015',14760,None),(405,'620 4001',49980,'SSK 2')])
def test_exact_eight_source_products(i,article,amount,model):
    source,url=saved(i)
    with b.o.offline_guard() as network:
        p=parse(source,source_url=url);status,diag=_card_gate('uss_stanko',source,url)
    assert status.value=='SUCCESS' and not diag and not any(network.values())
    assert p.manufacturer_article==article and p.price.current_price==amount
    assert p.price.currency==('RUB' if amount is not None else None)
    assert p.availability=='PreOrder' and p.supplier_model==model
    assert p.site_internal_id=='uss:'+url.split('.com/')[1]
    if i==65:assert p.price.state.value=='missing' and p.price.raw_text==''
    if i==53:assert p.name=='Набор фиксаторов' and p.price.raw_text=='1 440'
    if i==20:
        assert ('Наименование','Пистолет для вязких жидкостей Alu SPA') in p.technical_properties
        assert '330' not in p.price.raw_text and p.enrichment_evidence['availability_raw']=='под заказ'

@pytest.mark.parametrize('i',sorted(b.OPERATOR))
def test_operator_accepted_ambiguous_remains_exact(i):
    source,url=saved(i);p=parse(source,source_url=url)
    before=next(x for x in b.r.load(b.PHASE/'BEFORE_407.json') if x['index']==i)
    assert b.r.digest(b.r.encoded(asdict(p)))==before['payload_sha256']
    assert _card_gate('uss_stanko',source,url)[0].value=='AMBIGUOUS'

STABLE=[x for x in b.r.load(b.PHASE/'BEFORE_407.json') if x['outcome']=='MULTI_VARIANT' or x['index'] in {23,24,368,369,374,377,384}]
@pytest.mark.parametrize('a',STABLE,ids=lambda a:str(a['index']))
def test_all66_variants_and_seven_corrected_payloads_unchanged(a):
    row=b.r.load(b.o.ROOT/a['journal']);p,status,diag,outcome=b.evaluate(row)
    assert outcome==a['outcome']
    payload=asdict(p)
    # Exactly four later-authorized computed-kind transitions. Retain the
    # original full historical hash by reversing ONLY that metadata path;
    # all source/commercial/identity fields and all other rows stay exact.
    from scripts import inverse_kind_seals as seals
    seals.provenance()
    authorized={x['index']:x for x in b.r.load(seals.B/'AUTHORIZED_TERMINAL4.json')['rows']}
    if a['index'] in authorized:
        expected=authorized[a['index']]
        assert row['url']==expected['url'] and row['result']['evidence_sha256']==expected['evidence_sha256']
        assert payload['enrichment_evidence']['product_kind']['product_kind']=='machine'
        assert payload['enrichment_evidence']['product_kind']['confidence']=='HIGH'
        payload['enrichment_evidence']['product_kind']=expected['current_kind']
    assert b.r.digest(b.r.encoded(payload))==a['payload_sha256']

@pytest.mark.parametrize('mutation',['two_main_rows','accessory_only','article_mismatch','layout','no_heading','family','wrong_model','span'])
def test_unsafe_column_tables_not_promoted(mutation):
    source,url=saved(65);d=BeautifulSoup(source,'html.parser');table=d.select_one('table.data');tr=table.find_all('tr')[1]
    if mutation=='two_main_rows':table.append(BeautifulSoup(str(tr).replace('5910360','5910999').replace('591 0360','591 0999'),'html.parser').tr)
    elif mutation=='accessory_only':tr.find('td').string='Уплотнение для WS 360'
    elif mutation=='article_mismatch':tr.select_one('a[name]')['name']='artOTHER'
    elif mutation=='layout':table.wrap(d.new_tag('nav'))
    elif mutation=='no_heading':d.h1.decompose()
    elif mutation=='family':d.h1.string='Угломеры разных моделей'
    elif mutation=='wrong_model':tr.find('td').string='Угломер WS 361'
    elif mutation=='span':tr.find('td')['colspan']='2'
    assert _card_gate('uss_stanko',str(d),url)[0].value!='SUCCESS'

def test_shared_main_accessory_ownership_not_first_row():
    source,url=saved(20);d=BeautifulSoup(source,'html.parser');table=d.select_one('a[name="art2102200"]').find_parent('table')
    rows=table.find_all('tr');rows[2].extract();rows[0].insert_after(rows[2])
    p=parse(str(d),source_url=url)
    assert p.manufacturer_article=='210 2200' and p.price.current_price==2100 and p.availability=='PreOrder'

@pytest.mark.parametrize('mutation',['different_second_main','wrong_h2','no_h2','two_matching_rows'])
def test_shared_table_ambiguous_ownership_fails_closed(mutation):
    source,url=saved(20)
    if mutation=='different_second_main':source=source.replace('Уплотнение для баллона SPA','Пистолет другого исполнения SPA')
    elif mutation=='wrong_h2':source=source.replace('Пистолет для вязких жидкостей Alu SPA</a>','Пистолет другой модели</a>')
    elif mutation=='no_h2':
        d=BeautifulSoup(source,'html.parser');d.h2.decompose();source=str(d)
    elif mutation=='two_matching_rows':source=source.replace('Уплотнение для баллона SPA','Пистолет для вязких жидкостей Alu SPA')
    assert _card_gate('uss_stanko',source,url)[0].value=='AMBIGUOUS'

@pytest.mark.parametrize('mutation',['no_boundary','wrong_article','extra_main_before_boundary','heading_after_table','navigation','canonical_mismatch'])
def test_local_primary_h2_requires_full_boundary_proof(mutation):
    source,url=saved(405);d=BeautifulSoup(source,'html.parser')
    if mutation=='no_boundary':source=source.replace('Рекомендуемые дополнительные принадлежности:','Другие изделия:')
    elif mutation=='wrong_article':source=source.replace('name="art6204001"','name="art999"')
    elif mutation=='extra_main_before_boundary':
        t=d.select_one('table.data');t.insert_before(BeautifulSoup(str(t).replace('6204001','7777777'),'html.parser'));source=str(d)
    elif mutation=='heading_after_table':d.select_one('table.data').insert_after(d.h2.extract());source=str(d)
    elif mutation=='navigation':d.h2.parent.wrap(d.new_tag('nav'));source=str(d)
    elif mutation=='canonical_mismatch':source=source.replace(url,url.replace('ssk2','other'))
    assert parse(source,source_url=url).name==''
    assert _card_gate('uss_stanko',source,url)[0].value=='AMBIGUOUS'
