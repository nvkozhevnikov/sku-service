"""Local, versioned rules for news and VK; templates are data, never executable code."""
import hashlib
import json
from pathlib import Path

from .storage import uid

CHANNELS = ('news', 'vk')


def active_rules(state, channel):
    return [rule for rule in state.get('editorial_rules', {}).get(channel, []) if rule.get('enabled')]


def rule_instructions(state, channel):
    return '\n\n'.join(rule['title'] + '\n' + rule['text'] for rule in active_rules(state, channel))


def applied_rules(state, channels):
    return {channel: [{'id': r['id'], 'title': r['title'], 'sha256': hashlib.sha256(r['text'].encode()).hexdigest()}
                      for r in active_rules(state, channel)] for channel in channels if channel in CHANNELS}


class EditorialRules:
    def __init__(self, gateway):
        self.gateway = gateway

    def lists(self, state, value):
        channel = value.get('channel')
        if channel not in CHANNELS:
            raise ValueError('Выберите правила новости или VK.')
        live = state.setdefault('editorial_rules', {}).setdefault(channel, [])
        trash = state.setdefault('editorial_rules_trash', {}).setdefault(channel, [])
        return channel, live, trash

    def commit(self, state):
        for channel in CHANNELS:
            live = state.get('editorial_rules', {}).get(channel, [])
            trash = state.get('editorial_rules_trash', {}).get(channel, [])
            if len(live) > 50 or len(trash) > 50:
                raise ValueError('До 50 правил и 50 удалённых правил на канал. Освободите корзину.')
            if sum(len(r['text']) for r in live + trash) > 160000:
                raise ValueError('Суммарный текст правил канала больше 160 000 знаков.')
            if sum(len(r['text']) for r in live if r['enabled']) > 40000:
                raise ValueError('Активные правила канала должны занимать не более 40 000 знаков.')
        state['revision'] += 1
        self.gateway.write(state)
        return self.gateway.status()

    def mutate(self, action, value):
        with self.gateway.ai.lock:
            state, _ = self.gateway.edit(value)
            channel, live, trash = self.lists(state, value)
            rid = value.get('id', '')
            if not isinstance(rid, str):
                raise ValueError('Некорректный идентификатор правила.')
            if action == 'import':
                defaults = json.loads((Path(__file__).parent / f'{channel}_rules_defaults.json').read_text(encoding='utf-8'))['rules']
                known = {r.get('code') for r in live + trash}
                live.extend(dict(r, id=uid()) for r in defaults if r['code'] not in known)
            elif action == 'save':
                title, text, enabled = value.get('title'), value.get('text'), value.get('enabled', True)
                if not isinstance(title, str) or not title.strip() or len(title) > 120 or not isinstance(text, str) or not text.strip() or len(text) > 16000 or type(enabled) is not bool:
                    raise ValueError('Название: 1–120 знаков; текст: 1–16 000 знаков; включение — галочка.')
                if rid:
                    rule = next((r for r in live if r['id'] == rid), None)
                    if rule is None: raise ValueError('Правило не найдено.')
                    rule.update(title=title.strip(), text=text.strip(), enabled=enabled)
                else:
                    live.append({'id': uid(), 'code': '', 'title': title.strip(), 'text': text.strip(), 'enabled': enabled})
            else:
                source = trash if action in ('restore', 'purge') else live
                rule = next((r for r in source if r['id'] == rid), None)
                if rule is None: raise ValueError('Правило не найдено.')
                if action == 'delete':
                    live.remove(rule); trash.append(rule)
                elif action == 'restore':
                    trash.remove(rule); live.append(rule)
                elif action == 'purge':
                    if value.get('acknowledged') is not True: raise ValueError('Подтвердите окончательное удаление.')
                    trash.remove(rule)
                elif action == 'move':
                    direction = value.get('direction')
                    if type(direction) is not int or direction not in (-1, 1): raise ValueError('Некорректное направление.')
                    index = live.index(rule); target = index + direction
                    if 0 <= target < len(live): live[index], live[target] = live[target], live[index]
                else:
                    raise ValueError('Действие с правилом не найдено.')
            return self.commit(state)
