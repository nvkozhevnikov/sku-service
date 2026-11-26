"""Bounded SSE decoding of provider-visible reasoning; no persistence or retries."""
import json

from .ai_errors import provider_error

MAX_RESPONSE = 2 * 1024 * 1024
MAX_LINE = 65536
MAX_REASONING = 64000


class StreamCancelled(Exception):
    pass


class ReasoningText:
    """Hold enough trailing characters to redact an API key split across events."""
    def __init__(self, token, emit):
        self.token, self.emit = token, emit
        self.pending = ''
        self.count = 0
        self.truncated = False

    def push(self, text='', final=False):
        if not isinstance(text, str):
            raise ValueError('Некорректный формат рассуждений AI API.')
        self.pending = (self.pending + text).replace(self.token, '[ключ скрыт]')
        keep = 0 if final else max(0, len(self.token) - 1)
        cut = max(0, len(self.pending) - keep)
        safe, self.pending = self.pending[:cut], self.pending[cut:]
        if getattr(self.emit, 'include_reasoning', True) and safe:
            available = MAX_REASONING - self.count
            if available:
                fragment = safe[:available]
                self.count += len(fragment)
                self.emit({'type': 'reasoning', 'text': fragment})
            if len(safe) > available and not self.truncated:
                self.truncated = True
                self.emit({'type': 'reasoning_limit'})


def read_chat_stream(response, base, token, emit):
    total = 0
    data_lines = []
    content = []
    finished = False
    content_started = False
    reasoning = ReasoningText(token, emit)

    def event(data):
        nonlocal finished, content_started
        if data == '[DONE]':
            if not finished:
                raise ValueError('Модель не завершила поток; сохранённый материал не заменён.')
            reasoning.push(final=True)
            text = ''.join(content)
            if not text.strip() or token in text:
                raise ValueError('AI API вернул пустой или небезопасный ответ; содержимое скрыто.')
            return text
        try:
            value = json.loads(data)
            if not isinstance(value, dict):
                raise ValueError("Некорректный формат потока AI API.")
            if 'error' in value:
                raise provider_error(base, 200, data.encode())
            choices = value.get('choices', [])
            if not isinstance(choices, list):
                raise ValueError("Некорректный формат потока AI API.")
            if not choices:  # Usage-only frames are allowed.
                return None
            choice = choices[0]
            if not isinstance(choice, dict) or choice.get('index', 0) != 0:
                raise ValueError("Некорректный формат потока AI API.")
            delta = choice.get('delta', {})
            if not isinstance(delta, dict):
                raise ValueError("Некорректный формат потока AI API.")
            thought = delta.get('reasoning_content') or ''
            text = delta.get('content') or ''
            if not isinstance(thought, str) or not isinstance(text, str):
                raise ValueError("Некорректный формат потока AI API.")
            if finished and (thought or text):
                raise ValueError("Некорректный формат потока AI API.")
            if thought:
                reasoning.push(thought)
            if text:
                if not content_started:
                    content_started = True
                    emit({'type': 'status', 'text': 'Модель передаёт текст материалов.'})
                content.append(text)
            finish = choice.get('finish_reason')
            if finish is not None:
                if finish != 'stop':
                    raise ValueError('Модель не завершила ответ; проверьте лимит токенов. Материал не заменён.')
                finished = True
        except (KeyError, TypeError, IndexError, json.JSONDecodeError, RecursionError):
            raise ValueError('Некорректный формат потока AI API; сохранённый материал не заменён.') from None
        return None

    while True:
        line = response.readline(MAX_LINE + 1)
        if not line:
            break
        total += len(line)
        if len(line) > MAX_LINE or total > MAX_RESPONSE:
            raise ValueError('Ответ AI API слишком большой.')
        try:
            line = line.decode('utf-8').rstrip('\r\n')
        except UnicodeError:
            raise ValueError('Некорректная кодировка потока AI API.') from None
        if line.startswith('data:'):
            data_lines.append(line[5:].lstrip(' '))
        elif not line and data_lines:
            result = event('\n'.join(data_lines))
            data_lines = []
            if result is not None:
                return result
    # SSE permits an unterminated final line, but a completion marker is mandatory.
    if data_lines:
        result = event('\n'.join(data_lines))
        if result is not None:
            return result
    raise ValueError('Поток AI API оборвался до завершения. Автоповтора нет; сохранённый материал не заменён.')
