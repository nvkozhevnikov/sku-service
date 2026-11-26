import json

import pytest

from content_studio.content import render, safe_news_link
from content_studio.distribution import site_link
from test_distribution_ai import ai_setup, SOURCE


@pytest.mark.parametrize('url,expected',[
    ('sterbrust.com/catalog/machine/','https://sterbrust.com/catalog/machine/'),
    ('www.sterbrust.com','https://www.sterbrust.com/'),
    ('https://STERBRUST.COM/news/a/','https://sterbrust.com/news/a/'),
    ('/catalog/machine/','https://sterbrust.com/catalog/machine/'),
    ('https://sterbrust.ru/news/a/','https://sterbrust.ru/news/a/'),
    ('',''),
])
def test_owned_publication_addresses(url,expected):
    assert site_link(url)==expected


@pytest.mark.parametrize('url',[
    '//evil.example/a','https://sterbrust.com.evil.example/catalog/',
    'https://evil.example/sterbrust.com','https://sterbrust.com:8888/',
    'https://user:pass@sterbrust.com/','https://sterbrust.com/?access_token=HIDDEN',
    'javascript:alert(1)','http://sterbrust.com/catalog/',
])
def test_owned_domain_fix_keeps_unsafe_links_rejected(url):
    with pytest.raises(ValueError):site_link(url)


def test_news_links_and_preview_use_canonical_com():
    assert safe_news_link('https://sterbrust.com/company/news/a/')
    assert safe_news_link('https://www.sterbrust.com/catalog/a/')
    assert not safe_news_link('//sterbrust.com/catalog/a/')
    assert 'https://sterbrust.com/contacts/' in render('<a href="/contacts/">Связаться</a>',{},'preview',profile='news')


def test_link_preflight_is_private_and_never_calls_model_or_writes_project(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    endpoint='/api/distribution/validate-links'
    assert client.post(endpoint,json={}).status_code==403
    for data,field in [({'publication_url':'https://wrong.example/?token=HIDDEN'},'compose-publication-url'),
                       ({'url':'mailto:post@sterbrust.com'},'compose-url')]:
        result=client.post(endpoint,json=data,headers=headers)
        assert result.status_code==400 and result.json()['field']==field and 'HIDDEN' not in result.text
    result=client.post(endpoint,json={'publication_url':'sterbrust.com/catalog/a/'},headers=headers)
    assert result.json()=={'publication_url':'https://sterbrust.com/catalog/a/'}
    assert not fake.calls and not app.state.store.list()


def test_composition_accepts_com_footer_without_ai(tmp_path):
    app,fake,client,headers,cfg=ai_setup(tmp_path)
    result=client.post('/api/compose',json={'source':SOURCE,'channels':['vk'],
        'publication_url':'https://sterbrust.com/catalog/a/','hashtags':[]},headers=headers)
    assert result.status_code==200
    assert result.json()['project']['drafts']['vk']['body'].endswith('https://sterbrust.com/catalog/a/')
    assert not fake.calls
