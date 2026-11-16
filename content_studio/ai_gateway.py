"""Local registry for AI connections and the three Studio editorial tasks."""

import json

from .storage import Conflict, now, packed, uid
from .editorial_rules import EditorialRules, rule_instructions, active_rules


TASKS = {
    'vk': ('Пост для VK', 'Короткий пост простым текстом. Ссылка и хештеги добавляются в Studio.'),
    'news': ('Новость для сайта', 'Новость с заголовком, анонсом и SEO полями.'),
    'article': ('Статья для сайта', 'Развёрнутый материал с разделами, характеристиками и SEO полями.'),
}


class AIGateway:
    def __init__(self, ai):
        self.ai = ai
        self.rules = EditorialRules(self)
        if ai.store.initialize_schema:
            with ai.store.connection() as connection:
                connection.execute('CREATE TABLE IF NOT EXISTS ai_gateway (singleton INTEGER PRIMARY KEY CHECK(singleton=1), payload TEXT NOT NULL)')
                connection.execute('INSERT OR IGNORE INTO ai_gateway VALUES (1, ?)', (packed({'revision': 0, 'profiles': {}, 'tasks': {}, 'default': 'legacy'}),))

    def load(self):
        with self.ai.store.connection() as connection:
            return json.loads(connection.execute('SELECT payload FROM ai_gateway WHERE singleton=1').fetchone()[0])

    def write(self, state):
        with self.ai.store.connection() as connection:
            connection.execute('UPDATE ai_gateway SET payload=? WHERE singleton=1', (packed(state),))

    @staticmethod
    def version(state, legacy):
        return f"{state['revision']}:{legacy.get('version', 0)}"

    def edit(self, value):
        state = self.load()
        legacy = self.ai.config()
        if value.get('version') != self.version(state, legacy):
            raise Conflict('Настройки ИИ изменились. Откройте панель заново.')
        return state, legacy

    def resolve(self,state, legacy, channel):
        task = state['tasks'].get(channel, {})
        selection = task.get('profile', 'default')
        profile_id = state['default'] if selection == 'default' else selection
        profile = legacy if profile_id == 'legacy' else state['profiles'].get(profile_id, {})
        config = dict(profile)
        if task.get('model'):
            config['model'] = task['model']
        if task.get('token_limit') is not None:
            config['token_limit'] = task['token_limit']
        instructions = '\n\n'.join(filter(None, [task.get('instructions', ''), rule_instructions(state, channel)]))
        return {
            'channel': channel, 'profile_id': profile_id,
            'profile_name': profile.get('name', 'Прежнее подключение' if legacy else 'Основное подключение'),
            'model': config.get('model', ''), 'base_url': config.get('base_url', ''),
            'token_limit': config.get('token_limit'), 'instructions': instructions,
            'rules_count': len(active_rules(state, channel)),
            'timeout_seconds': config.get('timeout_seconds', 300),
            'reasoning_effort': config.get('reasoning_effort', 'auto'),
            'ready': bool(self.ai.public_config(profile)['configured'] and profile.get('enabled', True)),
            'config': config,
        }

    def status(self):
        with self.ai.lock:
            state, legacy = self.load(), self.ai.config()
            profiles = [dict(self.ai.public_config(profile), id=profile_id) for profile_id, profile in state['profiles'].items()]
            if legacy:
                profiles.insert(0, dict(self.ai.public_config(legacy), id='legacy', name='Прежнее подключение', enabled=True))
            tasks = []
            for channel, (name, description) in TASKS.items():
                route = self.resolve(state, legacy, channel)
                route.pop('config')
                tasks.append({'id': channel, 'name': name, 'description': description,
                              'settings': state['tasks'].get(channel, {}), 'route': route})
            return {'version': self.version(state, legacy), 'default': state['default'], 'profiles': profiles, 'tasks': tasks,
                    'editorial_rules': state.get('editorial_rules', {}), 'editorial_rules_trash': state.get('editorial_rules_trash', {})}

    def save_profile(self, value):
        with self.ai.lock:
            state, legacy = self.edit(value)
            profile_id = value.get('id', '')
            if not isinstance(profile_id, str) or (profile_id and profile_id not in state['profiles']):
                raise ValueError('Выберите существующее подключение.')
            if not profile_id and len(state['profiles']) >= 20:
                raise ValueError('В Studio можно сохранить до 20 подключений ИИ.')
            name, enabled = value.get('name', ''), value.get('enabled', True)
            if not isinstance(name, str) or not name.strip() or len(name.strip()) > 80 or type(enabled) is not bool:
                raise ValueError('Укажите название подключения до 80 знаков.')
            config = self.ai.connection_config(value, state['profiles'].get(profile_id, {}), allow_empty=True)
            profile_id = profile_id or uid()
            first_connection = not state['profiles'] and not legacy.get('key_ref')
            state['profiles'][profile_id] = dict(config, name=name.strip(), enabled=enabled)
            if first_connection and enabled:
                state['default'] = profile_id
            state['revision'] += 1
            self.write(state)
            return self.status() | {'saved_id': profile_id}

    def save_default(self, value):
        with self.ai.lock:
            state, legacy = self.edit(value)
            profile_id = value.get('id')
            profile = legacy if profile_id == 'legacy' else state['profiles'].get(profile_id) if isinstance(profile_id, str) else None
            if not profile or not profile.get('enabled', True):
                raise ValueError('Выберите включённое подключение.')
            state['default'] = profile_id
            state['revision'] += 1
            self.write(state)
            return self.status()

    def save_task(self, value):
        with self.ai.lock:
            state, legacy = self.edit(value)
            channel, profile = value.get('id'), value.get('profile', 'default')
            if not isinstance(channel, str) or channel not in TASKS or not isinstance(profile, str):
                raise ValueError('Выберите задачу и подключение.')
            if profile not in ('default', 'legacy') and profile not in state['profiles']:
                raise ValueError('Подключение для этой задачи отсутствует.')
            model, instructions, limit = value.get('model', ''), value.get('instructions', ''), value.get('token_limit')
            if not isinstance(model, str) or (model and not self.ai.valid_model(model)):
                raise ValueError('Введите точный ID модели или оставьте поле пустым.')
            if not isinstance(instructions, str) or len(instructions) > 5000:
                raise ValueError('Редакционные указания должны быть короче 5001 знака.')
            if limit is not None and (type(limit) is not int or not 256 <= limit <= 16000):
                raise ValueError('Укажите лимит от 256 до 16 000 токенов.')
            settings = {'profile': profile, 'model': model, 'instructions': instructions.strip(), 'token_limit': limit}
            if settings == {'profile': 'default', 'model': '', 'instructions': '', 'token_limit': None}:
                state['tasks'].pop(channel, None)
            else:
                state['tasks'][channel] = settings
            state['revision'] += 1
            self.write(state)
            return self.status()

    def test_profile(self, value):
        with self.ai.lock:
            state, legacy = self.edit(value)
            profile_id = value.get('id')
            profile = state['profiles'].get(profile_id) if isinstance(profile_id, str) else None
            if value.get('acknowledged') is not True or not profile or not profile.get('enabled') or not profile.get('key_ref'):
                raise ValueError('Сохраните ключ, включите подключение и подтвердите тестовый запрос.')
            self.ai.check_connection(profile)
            profile.update(checked=True, checked_at=now())
            self.write(state)
            return self.status()

    def groups(self, state, legacy, channels):
        groups = {}
        for channel in channels:
            route = self.resolve(state, legacy, channel)
            key = (route['profile_id'], route['model'], route['token_limit'], route['instructions'])
            if key not in groups:
                groups[key] = dict(route, channels=[])
            groups[key]['channels'].append(channel)
        return list(groups.values())
