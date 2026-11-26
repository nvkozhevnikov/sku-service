'use strict';
let editingRule = '';

function rulesChannel() { return $('ai-rules-channel').value; }
function currentRules() { return gatewayState?.editorial_rules?.[rulesChannel()] || []; }

async function rulesAction(action, data = {}) {
  await gatewayAction('ai-rules-status', 'Сохраняем правила…', async () => {
    gatewayState = await api('/api/ai/gateway/rules-' + action, 'POST', {
      ...data, channel: rulesChannel(), version: gatewayState.version,
    });
    updateGateway('Правила обновлены. Следующая подготовка использует новую редакцию.');
    $('ai-rules-status').textContent = 'Сохранено локально.';
  });
}

function paintEditorialRules() {
  const rules = currentRules(), channel = rulesChannel();
  const active = rules.filter(r => r.enabled);
  $('ai-rules-count').textContent = `Включено ${active.length} из ${rules.length} · ${active.reduce((n,r)=>n+r.text.length,0)} знаков. Только ${channel==='news'?'новость сайта':'пост VK'}.`;
  const list = $('ai-rules-list');
  list.replaceChildren();
  if (!rules.length) list.append(element('p', 'Правил пока нет. Добавьте своё или загрузите базовый набор.'));
  for (let i = 0; i < rules.length; i++) {
    const rule = rules[i], card = element('article', undefined, 'editorial-rule');
    const details = element('details'), summary = element('summary', rule.title);
    summary.append(element('small', rule.enabled ? ' · включено' : ' · выключено'));
    details.append(summary, element('pre', rule.text));
    const actions = element('div', undefined, 'ai-actions');
    const enabled = element('label', undefined, 'ai-checkline'), checkbox = element('input');
    checkbox.type = 'checkbox'; checkbox.checked = rule.enabled;
    checkbox.setAttribute('aria-label', 'Включить правило ' + rule.title);
    checkbox.onchange = () => rulesAction('save', {...rule, enabled: checkbox.checked});
    enabled.append(checkbox, element('span', 'Включено'));
    const edit = aiButton('Изменить', () => editEditorialRule(rule));
    const up = aiButton('↑', () => rulesAction('move', {id:rule.id, direction:-1}));
    const down = aiButton('↓', () => rulesAction('move', {id:rule.id, direction:1}));
    up.disabled = i===0; down.disabled = i===rules.length-1;
    up.setAttribute('aria-label', 'Выше: ' + rule.title); down.setAttribute('aria-label', 'Ниже: ' + rule.title);
    actions.append(enabled, edit, up, down, aiButton('Удалить', () => rulesAction('delete', {id:rule.id})));
    card.append(details, actions); list.append(card);
  }
  const trash = $('ai-rules-trash-list'); trash.replaceChildren();
  const deleted = gatewayState?.editorial_rules_trash?.[channel] || [];
  $('ai-rules-trash-title').textContent = `Удалённые правила · ${deleted.length}`;
  for (const rule of deleted) {
    const row = element('article', undefined, 'editorial-rule');
    row.append(element('b', rule.title), aiButton('Восстановить', () => rulesAction('restore', {id:rule.id})),
      aiButton('Удалить навсегда', () => {
        if (confirm('Окончательно удалить правило «' + rule.title + '»? Восстановление станет недоступно.')) {
          rulesAction('purge', {id:rule.id, acknowledged:true});
        }
      }));
    trash.append(row);
  }
}

function editEditorialRule(rule = {}) {
  editingRule = rule.id || '';
  $('ai-rule-title').value = rule.title || '';
  $('ai-rule-text').value = rule.text || '';
  $('ai-rule-enabled').checked = rule.enabled !== false;
  $('ai-rule-form').hidden = false;
  $('ai-rule-title').focus();
}

$('ai-rules-channel').onchange = () => { $('ai-rule-form').hidden = true; paintEditorialRules(); };
$('ai-rule-add').onclick = () => editEditorialRule();
$('ai-rule-cancel').onclick = () => { $('ai-rule-form').hidden = true; };
$('ai-rules-import').onclick = () => rulesAction('import');
$('ai-rule-save').onclick = async () => {
  await rulesAction('save', {id:editingRule, title:$('ai-rule-title').value, text:$('ai-rule-text').value, enabled:$('ai-rule-enabled').checked});
  if ($('ai-rules-status').dataset.error !== 'true') $('ai-rule-form').hidden = true;
};

const paintBeforeEditorial = paint;
paint = function() { paintBeforeEditorial(); $('news-review-button').hidden = channel !== 'news'; };
$('news-review-button').onclick = guarded(async () => {
  await flush();
  const metadata = await api('/api/projects/' + project.id + '/composition');
  const review = metadata?.news_review;
  const box = $('news-review-content'); box.replaceChildren();
  box.append(element('p', 'Факты и выпуск подтверждает редактор. Самопроверка модели не является внешним фактчекингом.'));
  if (!review) box.append(element('p', 'Для этой версии отчёт модели отсутствует. Выполните ручную проверку.'));
  else {
    box.append(element('h3', 'Машинная проверка HTML'), element('p', review.machine_html === 'pass' ? 'Опасных конструкций в проверенном срезе не обнаружено.' : 'Оформление требует проверки.'));
    box.append(element('h3', 'Самопроверка модели'), element('pre', JSON.stringify(review.model_qa, null, 2)));
    const list = element('ul');
    for (const issue of review.issues || []) list.append(element('li', issue));
    box.append(list, element('p', 'Статус: черновик. Факты требуют ручного подтверждения.'));
  }
  const rules = metadata?.editorial_rules?.news || [];
  if (rules.length) box.append(element('p', 'Применены правила: ' + rules.map(r=>r.title).join('; ')));
  $('news-review-dialog').showModal();
});
$('news-review-close').onclick = () => $('news-review-dialog').close();
