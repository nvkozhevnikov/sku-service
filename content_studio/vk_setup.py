"""Credential parsing and an honest photo capability explanation; no key creation."""
import re
from urllib.parse import parse_qs, urlsplit


def user_token_input(value):
    if not isinstance(value,str) or len(value)>8192:raise ValueError('Введите пользовательский ключ VK в локальное поле.')
    value=value.strip()
    if '://' in value:
        try:
            parsed=urlsplit(value)
            if parsed.scheme!='https' or parsed.netloc not in ('oauth.vk.com','oauth.vk.ru') or parsed.path!='/blank.html' or parsed.query:raise ValueError()
            token=parse_qs(parsed.fragment).get('access_token',[])
            if len(token)!=1:raise ValueError()
            value=token[0]
        except ValueError:raise ValueError('Нужен ключ или результат авторизации на официальном oauth.vk.com/blank.html. Пароли, cookie и сторонние ссылки не принимаются.') from None
    if not re.fullmatch(r'[A-Za-z0-9._-]{30,4096}',value):raise ValueError('Некорректный пользовательский ключ VK.')
    return value


def photo_setup(cfg):
    if not cfg.get('user_ref'):
        return {'ready':False,'reason':'user_key_required','title':'Подключите пользовательский ключ для фото',
            'message':'Ключ сообщества не загружает новые фото на стену. Нужен пользовательский ключ администратора с wall и photos. Из ключа сообщества его получить нельзя.'}
    if cfg.get('photo_capability') not in ('upload_server_verified','verified'):
        return {'ready':False,'reason':'check_required','title':'Проверьте доступ пользовательского ключа',
            'message':'Проверьте права wall и photos и право публикации в сообществе. Проверка сервера загрузки ничего не публикует.'}
    return {'ready':True,'reason':'upload_server_available','title':'Автоматическая загрузка фото подключена',
        'message':'После подтверждения поста Studio загрузит выбранные фото, сохранит их в VK и прикрепит к записи. Реальная отправка подтверждается отдельно по результату API.'}
