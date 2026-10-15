import pytest
from competitor_intel.harvey_decode import decode_harvey
from competitor_intel.execution import settings,SOURCES
from competitor_intel.five_source_extract import content_kind
from competitor_intel.change_detection import detect_events

def test_retained_harvey_pattern_is_explicit_not_silent_loss():
    data='<h1>Дисконт</h1><div class="new-detail__title-info"><li>Стол д'.encode()+b'\xd0...'+b'</div><p class="new-detail__title-price">102 680 RUB</p>'
    text,proof=decode_harvey(data)
    assert '[INCOMPLETE_UTF8_D0]...' in text and '102 680 RUB' in text
    assert proof['damaged_spans'][0]['raw_hex']=='d0' and proof['missing_character']=='UNKNOWN_NOT_RECONSTRUCTED'

@pytest.mark.parametrize('data',[b'<p class="price">100\xd0...</p>',b'<div class="new-detail__title-info">\xff...</div>',b'<div class="new-detail__title-info">\xd0...\xd0...</div>'])
def test_unknown_damage_fails_closed(data):
    with pytest.raises(ValueError):decode_harvey(data)

def test_explicit_charset_and_bom():
    assert decode_harvey('Станок'.encode('cp1251'),'text/html; charset=windows-1251')[0]=='Станок'
    assert decode_harvey('Станок'.encode('utf-16'))[1]['method']=='BOM'

def test_deferred_article_not_news_and_initial_completion_not_new_event():
    assert content_kind('amur_stan','https://amur-stan.ru/articles/gidroabrazivnaya-rezka/cena/','NEWS')=='BLOG'
    s={'source':'amur_stan','entity':'https://amur-stan.ru/articles/a/','title':'Статья','page_type':'BLOG','version':'initial','extractor':'five-sources-1.2','observed_at':'2026-10-13T18:00:00Z','items':[]}
    assert not [e for e in detect_events({'history':[s],'coverage':[]},'2026-10-13') if e['event_type']!='BASELINE_OBSERVATION']

def test_future_schedule_eleven_distinct_enabled():
    cfg=settings();assert cfg['enabled'] is True and len(cfg['selected_sources'])==11
    assert set(cfg['selected_sources'])==SOURCES
    assert cfg['timezone']=='Europe/Moscow' and cfg['time_of_day']=='08:00' and cfg['skip_if_running'] is True

def test_deferred_category_context_and_harvey_outlet_detail():
    from scripts.close_competitor_onboarding_gaps import material_kind,harvey_page
    from competitor_intel.transport import Capture
    from datetime import datetime,timezone
    assert material_kind('https://amur-stan.ru/articles/gidroabrazivnaya-rezka/')=='CONTENT'
    assert material_kind('https://amur-stan.ru/articles/gidroabrazivnaya-rezka/cena/')=='BLOG'
    h='<h1>JIB Станок Дисконт (341П)</h1><p class="new-detail__title-price">1000 ₽<span class="base__price">2000 ₽</span><span class="base__price base__price_percent">-50%</span></p>'
    url='https://harvey-rus.ru/catalog/discount/item/'
    p=harvey_page(Capture(url,url,h,'fixture','hash',datetime.now(timezone.utc),'FIXTURE'))
    assert p.items[0].tab_title=='CONDITION:OUTLET' and p.items[0].old_price==2000 and p.items[0].new_price==1000
