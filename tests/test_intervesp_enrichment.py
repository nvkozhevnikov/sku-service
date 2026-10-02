from universal_supplier.intervesp_enrichment import enrich_saved_detail
import pytest

URL='https://intervesp.ru/catalog/test/stanok-lx20-pro/'
ROW={'product_url':URL,'title':'Станок JET LX20 Pro','model_candidate':'LX20 Pro','brand':'JET',
     'category':'Станки','price_state':'price_on_request','price_text':'Цена по запросу','availability':'UNKNOWN',
     'evidence_sha256':'a'*64,'short_specs':'Мощность: 0,9 кВт'}
HTML='''<h1>Станок JET LX20 Pro</h1><meta property="product:brand" content="JET">
<div class="el_Main" id="bx_123_42"><div id="elTabDesc">Описание станка</div>
<div id="elTabProp"><table><tr><td>Мощность</td><td>кВт</td><td>0,9</td></tr></table></div>
<img itemprop="image" src="/upload/a.jpg"><div id="elTabFiles"><a href="/upload/manual.pdf">Инструкция</a></div>
<div id="elPrice"><div>Цена по запросу</div></div></div>
<img src="/advertisement.jpg"><table><tr><td>Бренд</td><td>BAD</td></tr></table>'''


def test_scoped_content_preserves_identity_and_does_not_collect_advertisements():
    product=enrich_saved_detail(HTML,ROW,source_url=URL)
    assert product.source_url == URL
    assert product.site_internal_id == '42'
    assert product.source_images == ('https://intervesp.ru/upload/a.jpg',)
    assert product.source_documents == (('Инструкция','https://intervesp.ru/upload/manual.pdf'),)
    assert product.technical_properties == (('Мощность (кВт)','0,9'),)
    assert product.description_text == 'Описание станка'
    assert product.enrichment_evidence['model_status'] == 'CANDIDATE'


@pytest.mark.parametrize('replacement',['LX20 NEW','LX20','TU2304'])
def test_execution_contradictions_remain_review(replacement):
    with pytest.raises(ValueError): enrich_saved_detail(HTML.replace('LX20 Pro',replacement),ROW,source_url=URL)


def test_brand_contradiction_is_not_auto_corrected():
    with pytest.raises(ValueError): enrich_saved_detail(HTML.replace('content="JET"','content="OTHER"'),ROW,source_url=URL)


def test_base_model_does_not_match_suffixed_detail():
    row={**ROW,'model_candidate':'TU2304','title':'Станок JET TU2304'}
    with pytest.raises(ValueError): enrich_saved_detail(HTML.replace('LX20 Pro','TU2304V'),row,source_url=URL)


def test_windows_robots_newlines_require_exact_original_digest():
    import hashlib
    from scripts.enrich_intervesp_details import verified_robots_text
    body=b'User-agent: *\r\nCrawl-delay: 20\r\n'
    expected=hashlib.sha256(body).hexdigest()
    assert 'Crawl-delay: 20' in verified_robots_text(body.replace(b'\n',b'\r\n'),expected)
    with pytest.raises(RuntimeError): verified_robots_text(body.replace(b'20',b'1'),expected)
