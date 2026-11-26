"""Account administration using the existing app_users, signer and audit trail."""
from copy import deepcopy
import json
import threading

from .access_policy import validate_profile
from .security import hash_password, normalize_username

LOCK_KEY = 390012
BUILTINS = [
    ("ADMIN", "Главный администратор", "ADMIN", ["*"]),
    ("SUPPLIER_MANAGER", "Менеджер поставщиков", "OPERATOR",
     ["catalog.read", "supplier.manage", "matching.read", "matching.manage", "xml.read", "xml.generate", "reports.read", "settings.read", "applications.read"]),
    ("CONTENT_MANAGER", "Контент-менеджер", "OPERATOR", ["competitor.read", "applications.read","content.read","content.edit","content.media","content.export","content.rules.manage","content.ai.use","content.site.export"]),
    ("ANALYST", "Аналитик", "VIEWER", ["catalog.read", "matching.read", "xml.read", "competitor.read", "reports.read", "applications.read"]),
    ("OBSERVER", "Наблюдатель", "VIEWER", ["catalog.read"]),
]


class Accounts:
    def __init__(self, store):
        self.store = store
        self.memory = hasattr(store, "_users")  # existing explicit InMemoryAdminStore QA double
        self.lock = threading.RLock()
        if self.memory and not hasattr(store, "_access_profiles"):
            store._access_profiles = {
                code: dict(code=code, name=name, base_role=role, permissions=perms, is_builtin=True)
                for code, name, role, perms in BUILTINS
            }

    def profiles(self):
        if self.memory:
            return deepcopy(list(self.store._access_profiles.values()))
        with self.store._connect() as c, c.cursor() as q:
            q.execute("SELECT code,name,base_role,permissions,is_builtin FROM app_access_profiles ORDER BY code LIMIT 200")
            return list(q.fetchall())

    def _memory_actor(self, actor, expected_hash, admin=True):
        row = next((u for u in self.store._users if u["id"] == actor.id), None)
        if (not row or not row.get("is_active", True) or row["password_hash"] != expected_hash
                or row.get("session_version", 1) != actor.session_version or (admin and row["role"] != "ADMIN")):
            raise PermissionError("Повторная авторизация недействительна")
        return row

    @staticmethod
    def _actor(q, actor, expected_hash, admin=True):
        q.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_KEY,))
        q.execute("SELECT id,role,is_active,password_hash,session_version FROM app_users WHERE id=%s FOR UPDATE", (actor.id,))
        row = q.fetchone()
        if (not row or not row["is_active"] or row["password_hash"] != expected_hash
                or row["session_version"] != actor.session_version or (admin and row["role"] != "ADMIN")):
            raise PermissionError("Повторная авторизация недействительна")

    def save_profile(self, code, name, permissions, actor, expected_hash):
        code, name, values, base = validate_profile(code, name, permissions)
        if code == "ADMIN":
            raise ValueError("Полные административные права роли ADMIN неизменны")
        if self.memory:
            with self.lock:
                self._memory_actor(actor, expected_hash)
                self.store._access_profiles[code] = dict(code=code, name=name, base_role=base,
                                                         permissions=values, is_builtin=False)
                for user in self.store._users:
                    if user.get("access_profile") == code:
                        user.update(role=base, permissions=values,
                                    session_version=user.get("session_version", 1) + 1)
                self.store.audit(actor.id, "ACCESS_PROFILE_SAVED", "access_profile", code, None,
                                 {"permissions": values, "base_role": base})
            return
        with self.store._connect() as c, c.transaction(), c.cursor() as q:
            self._actor(q, actor, expected_hash)
            q.execute("""INSERT INTO app_access_profiles(code,name,base_role,permissions)
                         VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT(code) DO UPDATE
                         SET name=excluded.name,base_role=excluded.base_role,
                             permissions=excluded.permissions,updated_at=now()""",
                      (code, name, base, json.dumps(values)))
            q.execute("""UPDATE app_users SET role=%s,session_version=session_version+1,updated_at=now()
                         WHERE access_profile=%s""", (base, code))
            self.store._audit(q, actor.id, "ACCESS_PROFILE_SAVED", "access_profile", code, None,
                              {"permissions": values, "base_role": base}, None)

    def create_user(self, username, name, password, profile, actor, expected_hash):
        username = normalize_username(username)
        name = str(name).strip()
        if not 1 <= len(name) <= 100:
            raise ValueError("Укажите имя длиной до 100 символов")
        encoded = hash_password(password)
        if self.memory:
            with self.lock:
                self._memory_actor(actor, expected_hash)
                selected = self.store._access_profiles.get(profile)
                if not selected:
                    raise ValueError("Роль не найдена")
                if any(u["username"] == username for u in self.store._users):
                    raise ValueError("Такой логин уже существует")
                uid = self.store.create_user(username, name, encoded, selected["base_role"], actor.id)
                target = next(u for u in self.store._users if u["id"] == uid)
                target.update(access_profile=profile, permissions=selected["permissions"], must_change_password=True)
                return uid
        with self.store._connect() as c, c.transaction(), c.cursor() as q:
            self._actor(q, actor, expected_hash)
            q.execute("SELECT base_role FROM app_access_profiles WHERE code=%s FOR SHARE", (profile,))
            selected = q.fetchone()
            if not selected:
                raise ValueError("Роль не найдена")
            q.execute("SELECT id FROM app_users WHERE username=%s", (username,))
            if q.fetchone():
                raise ValueError("Такой логин уже существует")
            q.execute("""INSERT INTO app_users(username,display_name,password_hash,role,access_profile,must_change_password)
                         VALUES (%s,%s,%s,%s,%s,true) RETURNING id""",
                      (username, name, encoded, selected["base_role"], profile))
            uid = q.fetchone()["id"]
            self.store._audit(q, actor.id, "USER_CREATED", "user", uid, None,
                              {"role": selected["base_role"], "access_profile": profile, "must_change_password": True}, None)
            return uid

    def change_profile(self, uid, profile, actor, expected_hash):
        if self.memory:
            with self.lock:
                self._memory_actor(actor, expected_hash)
                selected = self.store._access_profiles.get(profile)
                target = next((u for u in self.store._users if u["id"] == uid), None)
                if not target or not selected:
                    raise ValueError("Пользователь или роль не найдены")
                if target["role"] == "ADMIN" and target.get("is_active",True) and selected["base_role"] != "ADMIN" and sum(
                    u["role"] == "ADMIN" and u.get("is_active", True) for u in self.store._users
                ) <= 1:
                    raise ValueError("Нельзя лишить прав последнего главного администратора")
                target.update(role=selected["base_role"], access_profile=profile,
                              permissions=selected["permissions"], session_version=target.get("session_version", 1) + 1)
                self.store.audit(actor.id, "USER_PROFILE_CHANGED", "user", uid, None, {"access_profile": profile})
            return
        with self.store._connect() as c, c.transaction(), c.cursor() as q:
            self._actor(q, actor, expected_hash)
            q.execute("SELECT base_role FROM app_access_profiles WHERE code=%s FOR SHARE", (profile,))
            selected = q.fetchone()
            q.execute("SELECT role,is_active FROM app_users WHERE id=%s FOR UPDATE", (uid,))
            target = q.fetchone()
            if not target or not selected:
                raise ValueError("Пользователь или роль не найдены")
            q.execute("SELECT count(*) n FROM app_users WHERE role='ADMIN' AND is_active")
            count = q.fetchone()["n"]
            if target["role"] == "ADMIN" and target["is_active"] and selected["base_role"] != "ADMIN" and count <= 1:
                raise ValueError("Нельзя лишить прав последнего главного администратора")
            q.execute("""UPDATE app_users SET role=%s,access_profile=%s,session_version=session_version+1,
                         updated_at=now() WHERE id=%s""", (selected["base_role"], profile, uid))
            self.store._audit(q, actor.id, "USER_PROFILE_CHANGED", "user", uid, None, {"access_profile": profile}, None)

    def set_password(self, uid, password, actor, expected_hash, *, temporary):
        encoded = hash_password(password)
        action = "USER_PASSWORD_RESET" if temporary else "USER_PASSWORD_CHANGED"
        if not temporary and uid != actor.id:
            raise PermissionError("Можно изменить только собственный пароль")
        if self.memory:
            with self.lock:
                self._memory_actor(actor, expected_hash, admin=temporary)
                target = next((u for u in self.store._users if u["id"] == uid), None)
                if not target:
                    raise ValueError("Пользователь не найден")
                target.update(password_hash=encoded, must_change_password=temporary,
                              session_version=target.get("session_version", 1) + 1)
                self.store.audit(actor.id, action, "user", uid, None, {"must_change_password": temporary})
            return
        with self.store._connect() as c, c.transaction(), c.cursor() as q:
            self._actor(q, actor, expected_hash, admin=temporary)
            q.execute("""UPDATE app_users SET password_hash=%s,must_change_password=%s,
                         session_version=session_version+1,updated_at=now() WHERE id=%s RETURNING id""",
                      (encoded, temporary, uid))
            if not q.fetchone():
                raise ValueError("Пользователь не найден")
            self.store._audit(q, actor.id, action, "user", uid, None, {"must_change_password": temporary}, None)
