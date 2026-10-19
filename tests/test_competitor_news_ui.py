from copy import deepcopy
from tests.test_competitor_monitoring_ui import fixture_state,service,client

def content_state():
    state=fixture_state()
    state['competitor_sources'][0]['capabilities']=['NEWS','BLOG']
    for pid,kind,url,published,versions in [
        (3,'NEWS','https://fixture.example/news/older/','2026-10-01',[(4,'n1','2026-10-01T10:00:00+03:00','Прежний текст'),(5,'n2','2026-10-12T11:00:00+03:00','Изменённый текст')]),
        (4,'NEWS','https://fixture.example/news/new/','2026-10-12',[(6,'n3','2026-10-12T11:10:00+03:00','Новая новость')]),
        (5,'BLOG','https://fixture.example/blog/article/',None,[(7,'b1','2026-10-12T11:20:00+03:00','Статья о выборе оборудования')])]:
        state['competitor_pages'].append({'id':pid,'source_id':1,'canonical_url':url,'current_content_hash':versions[-1][1],'last_seen_at':versions[-1][2]})
        for vid,h,when,text in versions:
            state['competitor_page_versions'].append({'id':vid,'page_id':pid,'content_hash':h,'extractor_version':'fixture-content-v1','fetched_at':when,'qa_status':'ACCEPTED','payload':{'title':text,'page_type':kind,'published_at':published,'normalized_text':text+' '+('длинный материал '*50)+' уникальное окончание','item_scope_complete':False}})
            state['competitor_observations'].append({'id':vid,'page_id':pid,'page_version_id':vid,'observed_at':when,'transport':'HTTP'})
    return state

def test_news_tab_order_and_existing_records():
    with client(service(content_state())) as c:
        html=c.get('/competitor-monitoring/news').text
        assert html.index('href="/competitor-monitoring/promotions"')<html.index('href="/competitor-monitoring/news"')<html.index('href="/competitor-monitoring/assortment"')
        assert 'Изменённый текст' in html and 'Статья о выборе оборудования' in html
        assert 'NEWS · Новость' in html and 'BLOG / ARTICLE · Статья / блог' in html
        assert 'https://fixture.example/blog/article/' in html and 'Наблюдалось:' in html

def test_news_blog_type_filter_and_source():
    s=service(content_state())
    assert {n['content_type'] for n in s.view('news',{'content_type':'NEWS'})['rows']}=={'NEWS'}
    blogs=s.view('news',{'content_type':'BLOG'})['rows'];assert len(blogs)==1 and blogs[0]['indicator'] is None
    assert s.view('news',{'source':'youmet'})['total']==0
    with client(s) as c:
        html=c.get('/competitor-monitoring/news?content_type=BLOG&source=fixture_auto').text
        assert 'Статья о выборе оборудования' in html and 'Изменённый текст' not in html

def test_date_range_and_unknown_publication_fallback_sort():
    s=service(content_state());rows=s.view('news',{})['rows']
    assert [n['content_type'] for n in rows]==['BLOG','NEWS','NEWS']
    filtered=s.view('news',{'period':'custom','from':'2026-10-12','to':'2026-10-12'})['rows']
    assert len(filtered)==2
    assert s.view('news',{'period':'custom','from':'2026-10-02','to':'2026-10-03'})['total']==0

def test_search_full_text_beyond_excerpt():
    s=service(content_state())
    assert s.view('news',{'search':'УНИКАЛЬНОЕ ОКОНЧАНИЕ'})['total']==3
    assert s.view('news',{'search':'несуществующий текст'})['total']==0

def test_saved_historical_material_is_visible_by_default():
    state=content_state()
    for v in state['competitor_page_versions']:
        if v['page_id']==4:v['payload']['published_at']='2026-06-01'
    s=service(state)
    assert any(n['url'].endswith('/new/') for n in s.view('news',{})['rows'])
    assert not any(n['url'].endswith('/new/') for n in s.view('news',{'period':'7'})['rows'])

def test_real_new_changed_indicators_and_only_new():
    s=service(content_state());rows=s.view('news',{})['rows'];by={n['url']:n for n in rows}
    assert by['https://fixture.example/news/older/']['indicator']=='CHANGED'
    assert by['https://fixture.example/news/new/']['indicator']=='NEW'
    only=s.view('news',{'only_new':'1'})['rows'];assert len(only)==1 and only[0]['url'].endswith('/new/')
    assert s.view('overview',{})['kpi']['new_news']==1

def test_baseline_does_not_invent_news_events():
    state=content_state();state['competitor_page_versions']=[v for v in state['competitor_page_versions'] if v['page_id']==4]
    state['competitor_pages']=[p for p in state['competitor_pages'] if p['id']==4]
    state['competitor_observations']=[o for o in state['competitor_observations'] if o['page_id']==4];state['competitor_campaign_items']=[];state['competitor_runs']=[]
    s=service(state);assert s.view('news',{})['rows'][0]['indicator'] is None
    assert s.view('news',{'only_new':'1'})['total']==0 and s.view('overview',{})['kpi']['new_news']==0

def test_capability_controls_material_visibility_and_sources():
    state=content_state();state['competitor_sources'][0]['capabilities']=['BLOG']
    view=service(state).view('news',{});assert view['total']==1 and view['rows'][0]['content_type']=='BLOG'
    assert all(s['code']!='fixture_error' for s in view['content_sources'])

def test_latest_overview_materials_and_new_news_kpi():
    with client(service(content_state())) as c:
        html=c.get('/competitor-monitoring').text
        assert 'Последние новости конкурентов' in html and 'Новых новостей' in html
        assert 'Статья о выборе оборудования' in html and 'BLOG / ARTICLE' in html
        assert c.get('/competitor-monitoring/news?content_type=INVALID').status_code==200
        assert 'Выберите тип материала' in c.get('/competitor-monitoring/news?content_type=INVALID').text
