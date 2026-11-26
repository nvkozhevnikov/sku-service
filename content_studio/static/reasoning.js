'use strict';

const reasoningLimit = 64000;
let lastAITrace = {text: '', phase: '', title: '', received: false, truncated: false, header: '', headerAdded: false};
let reasoningFrame = null;

function paintReasoning() {
  const box = $('compose-reasoning-body');
  const follow = box.scrollHeight - box.scrollTop - box.clientHeight < 50;
  $('compose-reasoning-text').textContent = lastAITrace.text || 'Сервис пока не передал текст рассуждений.';
  $('compose-reasoning-status').textContent = lastAITrace.phase + (lastAITrace.truncated ? ' · показано не всё' : '');
  $('compose-reasoning-copy').disabled = !lastAITrace.received;
  if (follow) box.scrollTop = box.scrollHeight;
}

function appendReasoning(text) {
  const room = reasoningLimit - lastAITrace.text.length;
  if (room > 0) lastAITrace.text += text.slice(0, room);
  if (text.length > room) lastAITrace.truncated = true;
  if (reasoningFrame === null) reasoningFrame = requestAnimationFrame(() => {
    reasoningFrame = null;
    paintReasoning();
  });
}

function beginReasoning(show) {
  if (reasoningFrame !== null) cancelAnimationFrame(reasoningFrame);
  reasoningFrame = null;
  lastAITrace = {text: '', phase: 'Ожидаем ответ модели', title: '', received: false, truncated: false, header: '', headerAdded: false};
  $('compose-reasoning').hidden = !show;
  $('compose-reasoning').open = true;
  $('show-ai-reasoning').hidden = !show;
  $('compose-reasoning-copy').textContent = $('reasoning-copy').textContent = 'Скопировать';
  paintReasoning();
}

function finishReasoning(phase) {
  if (reasoningFrame !== null) cancelAnimationFrame(reasoningFrame);
  reasoningFrame = null;
  lastAITrace.phase = phase;
  paintReasoning();
}

function reasoningEvent(event) {
  if (event.type === 'route') {
    lastAITrace.header = `[Запрос ${event.index}/${event.total} · ${event.tasks} · ${event.model}]\n`;
    lastAITrace.headerAdded = false;
    lastAITrace.phase = `Запрос ${event.index}/${event.total} · ${event.model}`;
    paintReasoning();
  } else if (event.type === 'reasoning' && typeof event.text === 'string' && event.text) {
    if (!lastAITrace.headerAdded) {
      appendReasoning((lastAITrace.text ? '\n\n' : '') + lastAITrace.header);
      lastAITrace.headerAdded = true;
    }
    lastAITrace.received = true;
    lastAITrace.phase = 'Получаем рассуждения модели';
    appendReasoning(event.text);
  } else if (event.type === 'reasoning_limit') {
    lastAITrace.truncated = true;
    paintReasoning();
  } else if (event.type === 'status' && typeof event.text === 'string') {
    lastAITrace.phase = event.text;
    paintReasoning();
  }
}

async function streamCompose(path, data) {
  const show = $('compose-show-reasoning').checked;
  beginReasoning(show);
  let reader, providerStarted = false;
  try {
    const response = await studioFetch(path + '/stream', {
      method: 'POST', credentials: 'same-origin',
      headers: {'Content-Type': 'application/json', 'X-CSRF-Token': csrf},
      body: JSON.stringify({...data, show_reasoning: show}),
    });
    if (!response.ok) {
      const value = await response.json();
      const error = Error(value.error || 'Не удалось начать подготовку.');
      if(value.ai_error) error.ai_error = value.ai_error;
      error.server_error = true;
      if (response.status === 409 && path.startsWith('/api/projects/')) {
        conflicted = true;
        $('conflict-recovery').hidden = false;
      }
      throw error;
    }
    if (!response.body) throw Error('Браузер не поддерживает потоковый ответ.');
    reader = response.body.getReader();
    const decoder = new TextDecoder('utf-8', {fatal: true});
    let pending = '', bytes = 0, result = null;
    function line(text) {
      if (!text.trim()) return;
      const event = JSON.parse(text);
      if (event.type === 'error') {
        const error = Error(event.error || 'Подготовка не завершена.');
        if(event.ai_error) error.ai_error = event.ai_error;
        else if(providerStarted) error.ai_error = {reason:'stream_error'};
        error.server_error = true;
        if (event.conflict && path.startsWith('/api/projects/')) {
          conflicted = true;
          $('conflict-recovery').hidden = false;
        }
        throw error;
      }
      if (event.type === 'route') providerStarted = true;
      if (event.type === 'result') result = event.result;
      else reasoningEvent(event);
    }
    while (result === null) {
      const part = await reader.read();
      if (part.done) {
        pending += decoder.decode();
        if (pending) line(pending);
        break;
      }
      bytes += part.value.byteLength;
      if (bytes > 8 * 1024 * 1024) throw Error('Потоковый ответ слишком большой.');
      pending += decoder.decode(part.value, {stream: true});
      let end;
      while ((end = pending.indexOf('\n')) >= 0) {
        const text = pending.slice(0, end);
        pending = pending.slice(end + 1);
        line(text);
      }
    }
    if (!result?.project?.id || !result.composition) {
      throw Error('Поток оборвался без подтверждения результата. Проверьте библиотеку перед ручным повтором.');
    }
    lastAITrace.title = result.project.title;
    finishReasoning(lastAITrace.received ? 'Материалы подготовлены' : 'Материалы подготовлены · рассуждения не переданы');
    return result;
  } catch (error) {
    finishReasoning('Подготовка не завершена');
    if (!error.ai_error && !error.server_error) {
      error = Error('Поток ответа прервался или не прошёл проверку. Проверьте библиотеку перед ручным повтором. Автоповтора нет.');
      error.ai_error = {reason: 'stream_error'};
    }
    throw error;
  } finally {
    if (reader) await reader.cancel().catch(() => {});
  }
}

async function copyReasoning(button) {
  try {
    await navigator.clipboard.writeText(lastAITrace.text);
    button.textContent = 'Скопировано';
  } catch {
    button.textContent = 'Выделите и скопируйте текст';
  }
}

$('compose-reasoning-copy').onclick = () => copyReasoning($('compose-reasoning-copy'));
$('reasoning-copy').onclick = () => copyReasoning($('reasoning-copy'));
$('show-ai-reasoning').onclick = () => {
  $('reasoning-context').textContent = lastAITrace.title ? 'Материал: ' + lastAITrace.title : 'Последняя подготовка в этой вкладке';
  $('reasoning-phase').textContent = lastAITrace.phase + (lastAITrace.truncated ? ' · показано не всё' : '');
  $('reasoning-history-text').textContent = lastAITrace.text || 'Сервис не передал текст рассуждений.';
  $('reasoning-copy').disabled = !lastAITrace.received;
  $('reasoning-dialog').showModal();
};
$('reasoning-close').onclick = () => $('reasoning-dialog').close();
