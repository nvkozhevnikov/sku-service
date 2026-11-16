'use strict';

let gatewayState = null;
let gatewayBusy = false;
let editingProfile = '';
let editingTask = '';
let profileDirty = false;

const providerPresets = {
  glm: ['GLM / Z.AI', 'https://api.z.ai/api/paas/v4', 'openai', 'max_tokens'],
  glm_coding: ['GLM / Z.AI · Coding Plan', 'https://api.z.ai/api/coding/paas/v4', 'openai', 'max_tokens'],
  openai: ['OpenAI', 'https://api.openai.com/v1', 'openai', 'max_completion_tokens'],
  deepseek: ['DeepSeek', 'https://api.deepseek.com', 'openai', 'max_tokens'],
  xai: ['Grok / xAI', 'https://api.x.ai/v1', 'openai', 'max_tokens'],
  anthropic: ['Anthropic / Claude', 'https://api.anthropic.com/v1', 'anthropic', 'max_tokens'],
};

async function refreshGateway() {
  gatewayState = await api('/api/ai/gateway');
}

function selectedRoutes() {
  const channels = selectedChannels();
  return (gatewayState?.tasks || []).filter(task => channels.includes(task.id) && !(task.id==='news' && composerPreservesNews()));
}

function gatewayRoutesReady() {
  const routes = selectedRoutes();
  return routes.length > 0 && routes.every(task => task.route.ready);
}

function paintEngine() {
  const ai = $('compose-engine').value === 'ai';
  $('compose-ai-consent').hidden = !ai;
  const box = $('compose-ai-target');
  box.replaceChildren();
  if (!ai) return;
  const routes = selectedRoutes();
  if (selectedChannels().includes('news') && composerPreservesNews()) box.append(element('p','Новость сохраняется из готового HTML. Без запроса к ИИ.'));
  if (!routes.length && !composerPreservesNews()) box.append(element('p', 'Выберите, какие материалы подготовить.'));
  for (const task of routes) {
    const route = task.route;
    const destination = route.ready ? `${route.model} · ${route.base_url}` : 'Требуется настроить подключение';
    box.append(element('b', task.name), element('p', destination));
    if (route.ready) box.append(element('small', `Сетевое ожидание — ${route.timeout_seconds || 300} с.`));
  }
  if (gatewayRoutesReady()) {
    const requests = new Set(routes.map(({route}) => JSON.stringify([route.profile_id, route.model, route.token_limit, route.instructions])));
    box.append(element('small', `Запросов к API — ${requests.size}.`));
  }
  for (const task of routes) if (task.route.rules_count) box.append(element('small', `${task.name}: правил — ${task.route.rules_count}.`));
  const ack = $('compose-ai-ack');
  ack.disabled = !gatewayRoutesReady();
  if (ack.disabled) ack.checked = false;
}

function refreshComposerConsent() {
  $('compose-ai-ack').checked = false;
  paintEngine();
}

$('compose-engine').onchange = refreshComposerConsent;
for (const input of document.querySelectorAll('[data-compose-channel]')) {
  input.addEventListener('change', refreshComposerConsent);
}

function aiButton(label, onClick, style) {
  const button = element('button', label, style);
  button.onclick = onClick;
  return button;
}

async function gatewayAction(status, pending, work) {
  if (gatewayBusy) return;
  gatewayBusy = true;
  const dialog = $('ai-dialog');
  dialog.inert = true;
  dialog.setAttribute('aria-busy', 'true');
  $(status).textContent = pending;
  $(status).dataset.error = 'false';
  try {
    await work();
  } catch (error) {
    $(status).textContent = error.message;
    $(status).dataset.error = 'true';
  } finally {
    dialog.inert = false;
    dialog.removeAttribute('aria-busy');
    gatewayBusy = false;
  }
}

function selectGatewayTab(tab) {
  for (const button of document.querySelectorAll('[data-ai-tab]')) {
    const active = button.dataset.aiTab === tab;
    button.setAttribute('aria-selected', String(active));
    button.tabIndex = active ? 0 : -1;
    $('ai-' + button.dataset.aiTab).hidden = !active;
  }
  $('ai-key').value = '';
  if (tab !== 'connections') $('ai-profile-form').hidden = true;
  if (tab !== 'tasks') $('ai-task-form').hidden = true;
}

const gatewayTabs = [...document.querySelectorAll('[data-ai-tab]')];
for (const [index, button] of gatewayTabs.entries()) {
  button.onclick = () => selectGatewayTab(button.dataset.aiTab);
  button.onkeydown = event => {
    if (!['ArrowLeft', 'ArrowRight', 'Home', 'End'].includes(event.key)) return;
    event.preventDefault();
    const next = event.key === 'Home' ? 0 : event.key === 'End' ? gatewayTabs.length - 1
      : (index + (event.key === 'ArrowLeft' ? -1 : 1) + gatewayTabs.length) % gatewayTabs.length;
    selectGatewayTab(gatewayTabs[next].dataset.aiTab);
    gatewayTabs[next].focus();
  };
}

function paintGateway() {
  if (typeof paintEditorialRules === 'function') paintEditorialRules();
  const profiles = $('ai-profile-list');
  profiles.replaceChildren();
  if (!gatewayState.profiles.length) {
    const empty = element('div', undefined, 'ai-intro');
    empty.append(element('h3', 'Начните с первого подключения'),
      element('p', 'Для GLM заполните ID модели из кабинета Z.AI и вставьте ключ.'),
      aiButton('Настроить GLM', () => { showProfile(); applyProviderPreset('glm'); }, 'primary'));
    profiles.append(empty);
  }
  for (const profile of gatewayState.profiles) {
    const card = element('article', undefined, 'ai-card');
    const heading = element('div', undefined, 'ai-card-title');
    heading.append(element('h4', profile.name));
    if (profile.id === gatewayState.default) heading.append(element('span', 'Основное', 'ai-badge ready'));
    const state = !profile.enabled ? 'Выключено' : !profile.configured ? 'Нужен ключ'
      : profile.checked ? 'Тестовый ответ получен' : 'Ключ сохранён';
    const buttons = element('div', undefined, 'ai-actions');
    buttons.append(aiButton('Настроить', () => showProfile(profile)));
    if (profile.enabled && profile.id !== gatewayState.default) {
      buttons.append(aiButton('Сделать основным', () => gatewayAction('ai-global-status', 'Сохраняем…', async () => {
        gatewayState = await api('/api/ai/gateway/default', 'POST', {id: profile.id, version: gatewayState.version});
        updateGateway('Основное подключение обновлено.');
      })));
    }
    card.append(heading, element('p', profile.model, 'ai-model-label'),
      element('small', profile.base_url, 'ai-url'), element('span', state, 'ai-badge'), buttons);
    profiles.append(card);
  }

  const tasks = $('ai-task-list');
  tasks.replaceChildren();
  for (const task of gatewayState.tasks) {
    const card = element('article', undefined, 'ai-card');
    const route = task.route;
    const caption = route.model ? `${route.profile_name} · ${route.model}` : 'Нужно выбрать подключение';
    const actions = element('div', undefined, 'ai-actions');
    actions.append(aiButton('Настроить задачу', () => showTask(task)),
      aiButton('Проверить', () => {
        selectGatewayTab('check');
        $('ai-check-task').value = task.id;
        runOfflineCheck();
      }));
    card.append(element('h4', task.name), element('p', task.description),
      element('strong', caption, 'ai-model-label'),
      element('span', route.ready ? `${route.token_limit} токенов` : 'Требуется подключение', 'ai-badge'), actions);
    tasks.append(card);
  }
}

function updateGateway(message) {
  paintGateway();
  refreshComposerConsent();
  $('ai-check-result').hidden = true;
  $('ai-global-status').textContent = message;
}

function showProfile(profile = {}) {
  editingProfile = profile.id || '';
  const legacy = editingProfile === 'legacy';
  $('ai-profile-title').textContent = editingProfile ? 'Настройка подключения' : 'Новое подключение';
  $('ai-name').value = profile.name || '';
  $('ai-name').disabled = legacy;
  $('ai-enabled').disabled = legacy;
  $('ai-enabled').checked = profile.enabled !== false;
  $('ai-preset').value = Object.keys(providerPresets).find(key => providerPresets[key][1] === profile.base_url) || 'custom';
  $('ai-base-url').value = profile.base_url || '';
  $('ai-model').value = profile.model || '';
  $('ai-api-type').value = profile.api_type || 'openai';
  $('ai-token-limit').value = profile.token_limit || 4096;
  $('ai-token-parameter').value = profile.token_parameter || 'max_tokens';
  $('ai-json-mode').checked = profile.json_mode !== false;
  $('ai-timeout').value = profile.timeout_seconds || 300;
  $('ai-reasoning').value = profile.reasoning_effort || 'auto';
  $('ai-key').value = '';
  $('ai-key').placeholder = profile.configured ? 'Ключ сохранён. Здесь можно ввести новый' : 'Вставьте ключ локально';
  profileDirty = false;
  $('ai-live-test').hidden = !profile.configured || profile.enabled === false;
  $('ai-test-target').textContent = `Отправить короткий запрос в ${profile.base_url || 'выбранный API'}. Провайдер может списать токены.`;
  $('ai-test-ack').checked = false;
  $('ai-test').disabled = true;
  $('ai-status').textContent = '';
  $('ai-profile-form').hidden = false;
  paintProviderHelp();
  $('ai-name').focus();
}

function paintProviderHelp() {
  let host = '', path = '';
  try { const url = new URL($('ai-base-url').value); host = url.hostname; path = url.pathname.replace(/\/$/, ''); } catch {}
  $('ai-provider-help').hidden = host !== 'api.z.ai';
  const coding = path === '/api/coding/paas/v4';
  $('ai-provider-help-title').textContent = coding ? 'GLM Coding Plan' : 'GLM Model API';
  $('ai-provider-help-text').textContent = coding
    ? 'Выбран адрес подписки. Используйте ключ Coding Plan и доступную тарифу модель. Успешный короткий тест подтверждает получение ответа; полный материал проверяется отдельно. Условия использования подписки указаны в справке Z.AI.'
    : 'Обычный Model API использует Standard API Key и баланс или пакет API. Для подписки Coding Plan выберите её отдельный пресет; тип ключа и тарификация описаны в справке Z.AI.';
}

function invalidateProfileTest() {
  profileDirty = true;
  $('ai-test-ack').checked = false;
  $('ai-test').disabled = true;
}

function applyProviderPreset(id) {
  $('ai-preset').value = id;
  const preset = providerPresets[id];
  if (preset) {
    const [name, url, type, tokenParameter] = preset;
    if (!$('ai-name').value) $('ai-name').value = name;
    $('ai-base-url').value = url;
    $('ai-api-type').value = type;
    $('ai-token-parameter').value = tokenParameter;
    $('ai-reasoning').value = 'auto';
  }
  $('ai-key').value = '';
  invalidateProfileTest();
}

function showTask(task) {
  editingTask = task.id;
  const settings = task.settings;
  $('ai-task-title').textContent = task.name;
  const select = $('ai-task-profile');
  select.replaceChildren(new Option('Основное подключение', 'default'));
  for (const profile of gatewayState.profiles) {
    select.add(new Option(`${profile.name} · ${profile.model}${profile.enabled ? '' : ' · выключено'}`, profile.id));
  }
  select.value = settings.profile || 'default';
  $('ai-task-model').value = settings.model || '';
  $('ai-task-limit').value = settings.token_limit ?? '';
  $('ai-task-instructions').value = settings.instructions || '';
  $('ai-task-status').textContent = '';
  $('ai-task-form').hidden = false;
  select.focus();
}

function runOfflineCheck() {
  return gatewayAction('ai-check-status', 'Проверяем формат…', async () => {
    $('ai-check-result').hidden = true;
    const result = await api('/api/ai/gateway/offline-check', 'POST', {id: $('ai-check-task').value});
    const route = result.route;
    const destination = route.base_url
      ? `${route.profile_name} → ${route.model} · ${route.base_url} · ${route.token_limit} токенов. ` : '';
    $('ai-check-route').textContent = destination + (route.ready ? '' : 'Для реального запуска требуется действующее подключение.');
    const preview = $('ai-check-output');
    const copy = element('p');
    // The example is generated locally from a fixed fixture. Render it as text.
    const documentText = new DOMParser().parseFromString(result.draft.body, 'text/html');
    copy.textContent = [...documentText.body.childNodes].map(node => node.textContent).join('\n');
    preview.replaceChildren(element('h4', result.draft.title), copy);
    $('ai-check-request').textContent = result.instructions + '\n\n' + JSON.stringify(result.request, null, 2);
    $('ai-check-status').textContent = 'Локальная проверка прошла. Потрачено 0 токенов.';
    $('ai-check-result').hidden = false;
  });
}

async function openAI() {
  await refreshGateway();
  paintGateway();
  selectGatewayTab('connections');
  $('ai-profile-form').hidden = $('ai-task-form').hidden = $('ai-check-result').hidden = true;
  $('ai-global-status').textContent = $('ai-check-status').textContent = '';
  $('ai-dialog').showModal();
}

$('ai-settings').onclick = guarded(openAI);
$('compose-ai-settings').onclick = guarded(openAI);
$('ai-close').onclick = () => $('ai-dialog').close();
$('ai-dialog').addEventListener('cancel', event => { if (gatewayBusy) event.preventDefault(); });
$('ai-dialog').addEventListener('close', () => { $('ai-key').value = ''; refreshComposerConsent(); });
$('ai-add-profile').onclick = () => showProfile();
$('ai-profile-cancel').onclick = () => { $('ai-key').value = ''; $('ai-profile-form').hidden = true; };
$('ai-task-cancel').onclick = () => { $('ai-task-form').hidden = true; };
$('ai-preset').onchange = () => applyProviderPreset($('ai-preset').value);
$('ai-preset').addEventListener('change', paintProviderHelp);
$('ai-base-url').addEventListener('input', paintProviderHelp);
$('compose-error-settings').onclick = guarded(openAI);
for (const id of ['ai-name', 'ai-api-type', 'ai-base-url', 'ai-model', 'ai-key', 'ai-enabled', 'ai-token-limit', 'ai-token-parameter', 'ai-json-mode', 'ai-timeout', 'ai-reasoning']) {
  $(id).addEventListener('input', invalidateProfileTest);
}

$('ai-save').onclick = () => gatewayAction('ai-status', 'Сохраняем…', async () => {
  const settings = {
    id: editingProfile, version: gatewayState.version, name: $('ai-name').value.trim(),
    base_url: $('ai-base-url').value.trim(), api_type: $('ai-api-type').value, model: $('ai-model').value.trim(),
    key: $('ai-key').value, enabled: $('ai-enabled').checked, token_limit: Number($('ai-token-limit').value),
    token_parameter: $('ai-token-parameter').value, json_mode: $('ai-json-mode').checked,
    timeout_seconds: Number($('ai-timeout').value), reasoning_effort: $('ai-reasoning').value,
  };
  let id;
  if (editingProfile === 'legacy') {
    await api('/api/ai/settings', 'POST', {...settings, ai_gateway_version: gatewayState.version});
    await refreshGateway();
    id = 'legacy';
  } else {
    gatewayState = await api('/api/ai/gateway/profile', 'POST', settings);
    id = gatewayState.saved_id;
  }
  updateGateway('Подключение сохранено.');
  showProfile(gatewayState.profiles.find(profile => profile.id === id));
  $('ai-status').textContent = 'Сохранено на этом компьютере.';
});

$('ai-task-save').onclick = () => gatewayAction('ai-task-status', 'Сохраняем…', async () => {
  const limit = $('ai-task-limit').value;
  gatewayState = await api('/api/ai/gateway/task', 'POST', {
    version: gatewayState.version, id: editingTask, profile: $('ai-task-profile').value,
    model: $('ai-task-model').value.trim(), token_limit: limit ? Number(limit) : null,
    instructions: $('ai-task-instructions').value,
  });
  updateGateway('Маршрут задачи сохранён.');
  $('ai-task-status').textContent = 'Задача сохранена.';
});

$('ai-test-ack').onchange = () => { $('ai-test').disabled = profileDirty || !$('ai-test-ack').checked; };
$('ai-test').onclick = () => gatewayAction('ai-status', 'Ждём ответ API…', async () => {
  if (profileDirty || !$('ai-test-ack').checked) return;
  const profile = gatewayState.profiles.find(item => item.id === editingProfile);
  $('ai-test-ack').checked = false;
  $('ai-test').disabled = true;
  if (editingProfile === 'legacy') {
    await api('/api/ai/test', 'POST', {version: profile.version, acknowledged: true});
    await refreshGateway();
  } else {
    gatewayState = await api('/api/ai/gateway/test-profile', 'POST', {id: editingProfile, version: gatewayState.version, acknowledged: true});
  }
  paintGateway();
  $('ai-status').textContent = 'Тестовый ответ получен.';
});

$('ai-run-check').onclick = runOfflineCheck;
$('ai-check-task').onchange = () => { $('ai-check-result').hidden = true; $('ai-check-status').textContent = ''; };
