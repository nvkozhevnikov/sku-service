from pathlib import Path
import re

import pytest
from lxml import html
from fastapi.testclient import TestClient

from content_studio.composition import generate
from content_studio.source_parser import parse_source
from content_studio.app import create_app


EXAMPLE = (Path(__file__).parent/'fixtures/belmash-b3499-source.html').read_text(encoding='utf-8')


@pytest.mark.parametrize('preset', ['news', 'product', 'promotion'])
def test_exact_user_html_keeps_spec_context_and_removes_dangling_navigation(preset):
    result = generate(EXAMPLE, channels=['vk', 'news', 'article'], preset=preset)
    vk = result['drafts']['vk']['body']
    assert 'Потребляемая мощность 600 Вт' in vk
    assert 'Диаметр над станиной 180 мм' in vk
    assert 'Расстояние между центрами 340 мм' in vk
    assert not re.search(r'(?:^|\n)\s*600 Вт\s*(?:\n|$)', vk)
    assert 'Ниже — подробное сравнение' not in vk
    assert result['title'].startswith('BELMASH B3499')
    assert 'ОБНОВЛЕНИЕ МОДЕЛЬНОГО РЯДА' not in vk
    assert result['source']['text'] == EXAMPLE
    assert all(d['state'] == 'DRAFT' for d in result['drafts'].values())
    assert result['facts'][0]['state'] == 'UNREVIEWED'


def test_exact_example_article_retains_real_comparison_and_safety_notes():
    article = generate(EXAMPLE, channels=['article'])['drafts']['article']['body']
    tree = html.fragment_fromstring(article, create_parent='div')
    assert len(tree.xpath('.//table')) == 1
    rows = [[c.text_content() for c in row] for row in tree.xpath('.//tr')]
    assert ['Характеристика', 'ML-714VS', 'B3499'] in rows
    assert ['Масса нетто', '42,5 кг', '43 кг'] in rows
    assert ['Ход каретки', '350 мм', '280 мм с кожухом; до 340 мм без кожуха'] in rows
    assert 'снимать защиту допускается только при технологической необходимости' in tree.text_content()
    assert 'не означает обработку при нулевой частоте вращения' in tree.text_content()
    assert len(tree.xpath('.//h2')) >= 3


def test_vk_keeps_whole_protective_cover_clause():
    vk = generate(EXAMPLE, channels=['vk'], preset='product')['drafts']['vk']['body']
    assert 'С установленной защитой ход каретки составляет 280 мм.' in vk
    assert 'не заменяет отключение станка от сети.' in vk
    assert '…' not in vk


def test_comparison_flattening_keeps_each_model_column():
    source = '<p>Сравнение моделей</p><table><tr><th>Параметр</th><th>Старая</th><th>Новая</th></tr><tr><th>Масса</th><td>42 кг</td><td>43 кг</td></tr><tr><th>Ход</th><td>350 мм</td><td>280 мм с защитой; до 340 мм без неё</td></tr></table>'
    drafts = generate(source, channels=['vk', 'news'])['drafts']
    assert 'Масса - Старая 42 кг; Новая 43 кг' in drafts['vk']['body']
    assert 'Ход - Старая 350 мм; Новая 280 мм с защитой; до 340 мм без неё' in drafts['vk']['body']
    assert 'Масса — Старая: 42 кг; Новая: 43 кг' in drafts['news']['body']
    assert 'Ход — Старая: 350 мм; Новая: 280 мм с защитой; до 340 мм без неё' in drafts['news']['body']


def test_card_relationship_is_scoped_and_not_a_unit_guess():
    source = '<p>Новый инструмент</p><div><strong>700 Вт</strong><span>Потребляемая мощность</span></div><div><strong>25 мм</strong><span>Глубина реза</span></div><div>600 Вт</div><p>Это число приведено отдельно без подписи.</p>'
    r = generate(source, channels=['vk', 'article'])
    vk = r['drafts']['vk']['body']
    assert 'Потребляемая мощность 700 Вт' in vk and 'Глубина реза 25 мм' in vk
    assert '600 Вт' not in vk
    assert any('без подписи' in note and '600 Вт' in note for note in r['metadata']['notes'])


def test_inline_sentence_is_not_misread_as_card():
    parsed = parse_source('<p>Мощность <strong>600 Вт</strong>, питание <span>230 В</span>. Только при соблюдении инструкции.</p>')
    assert len(parsed.blocks) == 1
    assert parsed.blocks[0].text == 'Мощность 600 Вт, питание 230 В. Только при соблюдении инструкции.'


def test_sentence_starting_with_number_is_not_an_orphan_value():
    source = '600 Вт — потребляемая мощность этого станка.\n\n280 мм доступно только с установленной защитой.'
    r = generate(source, channels=['vk', 'article'])
    assert '600 Вт - потребляемая мощность этого станка.' in r['drafts']['vk']['body']
    assert '280 мм доступно только с установленной защитой.' in r['drafts']['vk']['body']
    assert not any('без подписи' in note for note in r['metadata']['notes'])


def test_long_paragraph_keeps_late_qualifier_instead_of_chopping_it():
    text = 'Техническое условие: ' + 'описание ' * 170 + '280 мм только с установленной защитой.'
    r = generate('Описание\n\n' + text, channels=['vk'])
    assert ('Техническое условие ' + 'описание ' * 170 + '280 мм только с установленной защитой.') in r['drafts']['vk']['body']
    assert any('сохранён целиком' in note for note in r['metadata']['notes'])


def test_merged_table_preserved_without_fabricating_column_association():
    source = '<p>Сводная таблица</p><table><tr><th>Модель</th><th colspan="2">A и B</th></tr><tr><th>Мощность</th><td>600 Вт</td><td>700 Вт</td></tr></table>'
    r = generate(source, channels=['vk', 'article'])
    assert '600' not in r['drafts']['vk']['body']
    assert 'colspan="2"' in r['drafts']['article']['body']
    assert any('объединёнными' in note for note in r['metadata']['notes'])


@pytest.mark.parametrize('source', [
    '<h1>Новый станок</h1><p>Чистый текст.</p><script>publish_everything()</script><style>leaked-style</style>',
    '<p>Новый станок</p><p hidden>hidden-secret</p><div style="display: none">hidden-secret</div><img src="https://invalid.example/track" onerror="publish_everything()">',
    '<p>Новый станок</p><div aria-hidden="true">hidden-secret</div><iframe src="http://localhost/secret">hidden-secret</iframe>',
])
def test_active_hidden_content_and_remote_images_do_not_enter_copy(source):
    r = generate(source, channels=['vk', 'news', 'article'])
    for d in r['drafts'].values():
        assert not any(bad in d['body'] for bad in ['publish_everything', 'hidden-secret', 'leaked-style', 'onerror', '<script', '<iframe', '<img'])


def test_non_boilerplate_uppercase_title_is_preserved():
    r = generate('ОТКРЫТА НОВАЯ МАСТЕРСКАЯ\n\nОб этом сообщили организаторы.', channels=['vk'])
    assert r['title'] == 'ОТКРЫТА НОВАЯ МАСТЕРСКАЯ'


def test_example_survives_actual_compose_and_save_routes(tmp_path):
    app = create_app(tmp_path, 'source-qa')
    with TestClient(app) as client:
        csrf = client.post('/api/login', json={'code': 'source-qa'}).json()['csrf']
        headers = {'X-CSRF-Token': csrf}
        response = client.post('/api/compose', json={'source': EXAMPLE, 'channels': ['vk', 'news', 'article']}, headers=headers)
        assert response.status_code == 200, response.text
        project = response.json()['project']
        assert '<table' in project['drafts']['article']['body']
        assert 'Потребляемая мощность 600 Вт' in project['drafts']['vk']['body']
        response = client.put('/api/projects/' + project['id'], json={'project': project, 'expected_revision': project['revision']}, headers=headers)
        assert response.status_code == 200, response.text
        assert response.json()['drafts']['article']['body'] == project['drafts']['article']['body']
