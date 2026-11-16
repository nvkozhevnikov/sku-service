"""Application permissions layered on the existing users/sessions, never DB roles."""
from urllib.parse import urlsplit

PERMISSIONS = {
    "catalog.read": "Каталог и наличие",
    "supplier.manage": "Управление поставщиками и заданиями",
    "matching.read": "Просмотр сопоставлений",
    "matching.manage": "Решения по сопоставлению",
    "xml.read": "Просмотр XML",
    "xml.generate": "Формирование XML",
    "competitor.read": "Новости, статьи, акции и конкуренты",
    "competitor.collect": "Разрешённые задания мониторинга",
    "reports.read": "История и отчёты",
    "settings.read": "Просмотр настроек",
    "applications.read": "Приложения",
    "content.read": "Контент-студия: просмотр своих материалов",
    "content.edit": "Контент-студия: создание и редактирование",
    "content.media": "Контент-студия: загрузка фотографий",
    "content.export": "Контент-студия: скачивание пакетов",
    "content.rules.manage": "Контент-студия: редакционные правила",
    "content.ai.use": "Контент-студия: явная генерация ИИ",
    "content.vk.publish": "Контент-студия: подготовка и подтверждение ВК",
    "content.site.export": "Контент-студия: HTML для сайта",
}
LEGACY_READ = frozenset(k for k in PERMISSIONS if k.endswith(".read") and not k.startswith('content.'))


def allowed(user, permission):
    if user.role == "ADMIN":
        return True
    configured = getattr(user, "permissions", None)
    permissions = (LEGACY_READ | ({"supplier.manage", "matching.manage", "xml.generate", "competitor.collect"}
                                  if user.role == "OPERATOR" else set())
                   if configured is None else configured)
    return permission in permissions


def request_permission(path, method):
    write = method not in {"GET", "HEAD", "OPTIONS"}
    if path.startswith('/content-studio'):
        return 'content.read'  # child adapter enforces exact action + project ACL
    if path.startswith(("/users", "/roles", "/settings/users")):
        return "accounts.admin"
    if path.startswith("/applications"):
        return "applications.read"
    if path.startswith(("/competitor-monitoring", "/news")):
        return "competitor.collect" if write else "competitor.read"
    if path.startswith(("/exports", "/rc-final", "/xml")):
        return "xml.generate" if write else "xml.read"
    if path.startswith(("/review", "/automatic-matching", "/matching")):
        return "matching.manage" if write else "matching.read"
    if path.startswith(("/scheduler", "/settings", "/rules")):
        return "supplier.manage" if write else "settings.read"
    if path == "/" or path.startswith((
        "/suppliers", "/products", "/runs", "/data-quality", "/properties",
        "/reparse", "/commercial-qa", "/live11", "/source-", "/parser-", "/release-jobs",
    )):
        return "supplier.manage" if write else "catalog.read"
    return "accounts.admin"  # new/unclassified endpoints fail closed for limited profiles


def validate_profile(code, name, permissions):
    import re
    code, name = str(code).strip().upper(), str(name).strip()
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,47}", code) or not 1 <= len(name) <= 100:
        raise ValueError("Код роли: 3–48 символов A–Z/0–9/_; название: 1–100 символов")
    values = sorted(set(permissions))
    if not values or any(value not in PERMISSIONS for value in values):
        raise ValueError("Выберите известные разрешения; полные права выдаются только роли ADMIN")
    base_role = "OPERATOR" if any(not x.endswith(".read") for x in values) else "VIEWER"
    return code, name, values, base_role


def validate_new_password(password, confirmation):
    if password != confirmation:
        raise ValueError("Новые пароли не совпадают")
    if not 12 <= len(password) <= 128:
        raise ValueError("Пароль: 12–128 символов")
    groups = (any(c.islower() for c in password), any(c.isupper() for c in password),
              any(c.isdigit() for c in password), any(not c.isalnum() for c in password))
    if sum(groups) < 3:
        raise ValueError("Используйте хотя бы три типа: строчные, прописные, цифры, специальные символы")
    return password


def application_link(value):
    """Only an explicitly configured HTTPS endpoint; no synthesized SSO contract."""
    value = str(value or "").strip()
    parsed = urlsplit(value)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
        return ""
    return value
