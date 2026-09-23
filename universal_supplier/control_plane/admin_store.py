from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from difflib import SequenceMatcher
import hashlib
import json
from typing import Any

from universal_supplier.postgres import PostgresConfig
from sterbrust_matching.normalization import normalize_brand, normalize_model, normalized_name
from sterbrust_matching.product_identity import classify_product_kind
from .security import AuthUser, normalize_username


REVIEW_AUTOMATIC_DECISIONS = {
    "REVIEW_EXISTING", "CONFLICT", "INSUFFICIENT_IDENTITY",
    "SAFE_NEW_PRODUCT_CANDIDATE", "ACCESSORY_OR_COMPATIBILITY_ITEM",
}
FINAL_MANUAL_DECISIONS = {"MANUAL_CONFIRMED", "MANUAL_CONFIRMED_NEW", "MANUAL_ACCESSORY", "MARKED_REVIEWED"}
CONFIRMED_IDENTITY_DECISIONS = {"EXACT_EXISTING", "HIGH_CONFIDENCE_EXISTING"}
SYNCED_EXISTING_IDENTITY = "SYNCED_EXISTING_IDENTITY"


def review_proposal_id(decision: object, best_id: object) -> str | None:
    """A proposed Sterbrust ID is a review recommendation, not a blocked candidate."""
    if str(decision or "") == "REVIEW_EXISTING" and str(best_id or "").strip():
        return str(best_id).strip()
    return None


def plan_review_sync(
    *, lifecycle: str | None, current_proposed: object, decision: object,
    best_id: object, fingerprint_changed: bool,
    human_decision_class: str | None = None, human_sterbrust_id: str | None = None,
) -> str:
    """Idempotent queue action. Never inserts a second manual confirmation."""
    proposal = review_proposal_id(decision, best_id)
    current = str(current_proposed).strip() if current_proposed else None
    confirmed = str(decision or "") in CONFIRMED_IDENTITY_DECISIONS
    human = str(human_decision_class or "")
    if human in FINAL_MANUAL_DECISIONS or human == "MANUAL_REJECTED":
        same_manual_target = (
            human == "MANUAL_CONFIRMED" and confirmed
            and str(best_id or "") == str(human_sterbrust_id or "")
        )
        if same_manual_target and lifecycle != "RESOLVED":
            return "restore_manual"
        return "unchanged"
    if lifecycle is None:
        return "insert" if str(decision or "") in REVIEW_AUTOMATIC_DECISIONS else "skip"
    if confirmed:
        return "unchanged" if lifecycle == "RESOLVED" else "sync_confirmed"
    if lifecycle == "RESOLVED":
        return "reopen" if fingerprint_changed else "unchanged"
    if not fingerprint_changed and current == proposal:
        return "unchanged"
    return "refresh_open"


def canonical_fingerprint(snapshot: dict[str, Any]) -> str:
    payload = json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def priority_for(decision: str) -> tuple[int, str]:
    return {
        "REVIEW_EXISTING": (1, "Высокая вероятность существующего товара"),
        "ACCESSORY_OR_COMPATIBILITY_ITEM": (2, "Понятный случай аксессуара или совместимости"),
        "CONFLICT": (3, "Обнаружены противоречивые признаки"),
        "SAFE_NEW_PRODUCT_CANDIDATE": (4, "Нужно подтвердить отсутствие существующего товара"),
        "INSUFFICIENT_IDENTITY": (4, "Недостаточно идентификационных данных"),
    }.get(decision, (4, "Требуется ручная проверка"))


def explanation_for(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("identity_characteristics") or {}
    conflicts = row.get("conflicts") or {}
    positive = []
    if row.get("own_model"):
        positive.append(f"Собственная модель: {row['own_model']}")
    if row.get("reference_model"):
        positive.append(f"Референсная модель: {row['reference_model']}")
    if row.get("brand_raw"):
        positive.append(f"Бренд источника: {row['brand_raw']}")
    decision = row.get("decision") or row.get("automatic_decision")
    execution = evidence.get("execution") or {}
    summary = {
        "REVIEW_EXISTING": "Найден похожий существующий товар, но идентификационных данных недостаточно для автоматического сопоставления.",
        "CONFLICT": "Найдены противоречия в идентификационных признаках товара. Требуется решение сотрудника.",
        "SAFE_NEW_PRODUCT_CANDIDATE": "Надёжного существующего совпадения не найдено. Проверьте похожие товары перед решением.",
        "ACCESSORY_OR_COMPATIBILITY_ITEM": "Товар похож на аксессуар или оснастку для указанной совместимой модели и не должен автоматически заменять основной товар.",
        "INSUFFICIENT_IDENTITY": "Недостаточно модели или других устойчивых признаков для безопасного решения.",
        "EXACT_EXISTING": "Существующее соответствие подтверждено устойчивыми идентификационными признаками.",
        "HIGH_CONFIDENCE_EXISTING": "Бренд, модель и тип товара дают высокую уверенность в существующем соответствии.",
    }.get(decision, row.get("decision_reason") or "Требуется решение оператора")
    if execution.get("execution_qualifier"):
        positive.append(f"Исполнение / версия: {execution['execution_qualifier']}")
    return {
        "summary": summary,
        "positive": positive,
        "conflicts": conflicts,
        "identity_evidence": evidence,
        "rule_version": row.get("rule_version"),
        "technical_reason": row.get("decision_reason") or "",
        "proposal_policy": (
            "Предложенный ID показывается только для REVIEW_EXISTING. "
            "Совпадение артикула в заголовке само по себе не является подтверждением."
        ),
    }


def identity_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    evidence = row.get("identity_characteristics") or {}
    return {
        "supplier": row.get("supplier"), "external_id": row.get("external_id"),
        "sku": row.get("sku"), "brand": row.get("brand_raw"),
        "own_model": row.get("own_model"), "reference_model": row.get("reference_model"),
        "product_kind": row.get("product_kind"),
        "execution": evidence.get("execution") or {},
        "identity_critical_properties": evidence.get("identity_critical_properties") or {},
    }


class PostgresAdminStore:
    def __init__(self, config: PostgresConfig) -> None:
        self.config = config

    def _connect(self):
        import psycopg
        return psycopg.connect(**self.config.kwargs(), autocommit=True, row_factory=psycopg.rows.dict_row)

    @staticmethod
    def _user(row: dict | None) -> AuthUser | None:
        return AuthUser(int(row["id"]), row["username"], row["display_name"], row["role"], row["is_active"]) if row else None

    def users_exist(self) -> bool:
        with self._connect() as c, c.cursor() as q:
            q.execute("SELECT EXISTS(SELECT 1 FROM app_users)")
            return bool(q.fetchone()["exists"])

    def user_by_username(self, username: str) -> tuple[AuthUser, str] | None:
        with self._connect() as c, c.cursor() as q:
            q.execute("SELECT id,username,display_name,role,is_active,password_hash FROM app_users WHERE username=%s", (username.strip().lower(),))
            row = q.fetchone()
        return (self._user(row), row["password_hash"]) if row else None

    def user_by_id(self, user_id: int) -> AuthUser | None:
        with self._connect() as c, c.cursor() as q:
            q.execute("SELECT id,username,display_name,role,is_active FROM app_users WHERE id=%s", (user_id,))
            return self._user(q.fetchone())

    def touch_login(self, user_id: int) -> None:
        with self._connect() as c, c.cursor() as q:
            q.execute("UPDATE app_users SET last_login_at=now(),updated_at=now() WHERE id=%s", (user_id,))

    def list_users(self) -> list[dict]:
        with self._connect() as c, c.cursor() as q:
            q.execute("SELECT id,username,display_name,role,is_active,created_at,last_login_at FROM app_users ORDER BY username")
            return list(q.fetchall())

    def create_user(self, username: str, display_name: str, password_hash: str, role: str, *, actor_id: int | None = None) -> int:
        username = normalize_username(username)
        if role not in {"ADMIN", "OPERATOR", "VIEWER"}:
            raise ValueError("Недопустимая роль")
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute("INSERT INTO app_users(username,display_name,password_hash,role) VALUES (%s,%s,%s,%s) RETURNING id", (username, display_name.strip(), password_hash, role))
            user_id = q.fetchone()["id"]
            self._audit(q, actor_id, "USER_CREATED", "user", user_id, None, {"username": username, "display_name": display_name.strip(), "role": role, "is_active": True}, None)
        return int(user_id)

    def set_user_active(self, user_id: int, active: bool, actor_id: int) -> None:
        if user_id == actor_id and not active:
            raise ValueError("Нельзя отключить собственную учётную запись")
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT username,display_name,role,is_active FROM app_users WHERE id=%s FOR UPDATE", (user_id,))
            before = q.fetchone()
            if not before:
                raise KeyError("Пользователь не найден")
            q.execute("UPDATE app_users SET is_active=%s,updated_at=now() WHERE id=%s", (active, user_id))
            after = dict(before); after["is_active"] = active
            self._audit(q, actor_id, "USER_ACTIVE_CHANGED", "user", user_id, dict(before), after, None)

    @staticmethod
    def _audit(q, user_id, action, entity_type, entity_id, before, after, comment, correlation_id=None):
        q.execute("""INSERT INTO audit_events(user_id,action,entity_type,entity_id,before_state,after_state,comment,correlation_id)
                     VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
                  (user_id, action, entity_type, str(entity_id), json.dumps(before, default=str, ensure_ascii=False) if before is not None else None,
                   json.dumps(after, default=str, ensure_ascii=False) if after is not None else None, comment, correlation_id))

    def audit(self, user_id: int | None, action: str, entity_type: str, entity_id: Any, before: dict | None, after: dict | None, comment: str | None = None, correlation_id: str | None = None) -> None:
        with self._connect() as c, c.transaction(), c.cursor() as q:
            self._audit(q, user_id, action, entity_type, entity_id, before, after, comment, correlation_id)

    def audit_events(self, limit: int = 200) -> list[dict]:
        with self._connect() as c, c.cursor() as q:
            q.execute("""SELECT a.*,u.display_name AS user_display_name FROM audit_events a
                         LEFT JOIN app_users u ON u.id=a.user_id ORDER BY a.created_at DESC,a.id DESC LIMIT %s""", (limit,))
            return list(q.fetchall())

    def generate_review_cases(self, supplier_code: str | None = None) -> dict[str, int]:
        inserted = unchanged = reopened = synced = restored = 0
        where = "WHERE sp.active"
        params: tuple[Any, ...] = ()
        if supplier_code:
            where += " AND s.code=%s"
            params = (supplier_code,)
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute(f"""SELECT sp.id AS source_product_id,s.code AS supplier,sp.external_id,sp.sku,sp.brand_raw,
                                 pid.decision,pid.own_model,pid.reference_model,pid.product_kind,pid.best_sterbrust_id,
                                 pid.decision_reason,pid.conflicts,pid.identity_characteristics,pid.rule_version
                         FROM source_products sp JOIN suppliers s ON s.id=sp.supplier_id
                         JOIN product_identity_decisions pid ON pid.source_product_id=sp.id
                         {where} ORDER BY sp.id""", params)
            for row in q.fetchall():
                row = dict(row)
                snapshot = identity_snapshot(row)
                fingerprint = canonical_fingerprint(snapshot)
                priority, reason = priority_for(row["decision"])
                explanation = explanation_for(row)
                proposal = review_proposal_id(row["decision"], row.get("best_sterbrust_id"))
                q.execute("SELECT id,lifecycle_status,source_identity_fingerprint,proposed_sterbrust_id,resolved_decision_class FROM review_cases WHERE source_product_id=%s FOR UPDATE", (row["source_product_id"],))
                current = q.fetchone()
                human_class = human_id = None
                if current is not None:
                    q.execute("""SELECT decision_class, sterbrust_product_id FROM review_decisions
                                 WHERE review_case_id=%s AND decision_class = ANY(%s)
                                 ORDER BY created_at DESC, id DESC LIMIT 1""",
                              (current["id"], sorted(FINAL_MANUAL_DECISIONS | {"MANUAL_REJECTED"})))
                    human = q.fetchone()
                    if human:
                        human_class = human["decision_class"]
                        human_id = human["sterbrust_product_id"]
                action = plan_review_sync(
                    lifecycle=None if current is None else current["lifecycle_status"],
                    current_proposed=None if current is None else current.get("proposed_sterbrust_id"),
                    decision=row["decision"], best_id=row.get("best_sterbrust_id"),
                    fingerprint_changed=current is not None and current["source_identity_fingerprint"] != fingerprint,
                    human_decision_class=human_class, human_sterbrust_id=None if human_id is None else str(human_id),
                )
                if action == "skip":
                    continue
                if action == "unchanged":
                    unchanged += 1
                    continue
                stale = None
                if current is not None and current.get("proposed_sterbrust_id") and str(current.get("proposed_sterbrust_id")) != str(proposal or ""):
                    stale = str(current.get("proposed_sterbrust_id"))
                    explanation = dict(explanation)
                    explanation["stale_proposed_sterbrust_id"] = stale
                    explanation["stale_proposal_note"] = "Прежний предложенный ID больше не является актуальной рекомендацией и не предлагается к подтверждению."
                payload = json.dumps(explanation, ensure_ascii=False, default=str)
                snapshot_json = json.dumps(snapshot, ensure_ascii=False, default=str)
                if action == "insert":
                    q.execute("""INSERT INTO review_cases(source_product_id,priority_rank,priority_reason,automatic_decision,proposed_sterbrust_id,explanation,source_identity_fingerprint,source_identity_snapshot)
                                 VALUES (%s,%s,%s,%s,%s,%s::jsonb,%s,%s::jsonb)""",
                              (row["source_product_id"], priority, reason, row["decision"], proposal, payload, fingerprint, snapshot_json))
                    inserted += 1
                    continue
                before = dict(current)
                if action == "sync_confirmed":
                    q.execute("""UPDATE review_cases SET lifecycle_status='RESOLVED',resolved_decision_class=%s,resolved_at=now(),
                                 priority_rank=%s,priority_reason=%s,automatic_decision=%s,proposed_sterbrust_id=NULL,
                                 explanation=%s::jsonb,source_identity_fingerprint=%s,source_identity_snapshot=%s::jsonb,updated_at=now()
                                 WHERE id=%s""",
                              (SYNCED_EXISTING_IDENTITY, priority, reason, row["decision"], payload, fingerprint, snapshot_json, current["id"]))
                    self._audit(q, None, "REVIEW_SYNCED_EXISTING_IDENTITY", "review_case", current["id"], before,
                                {"lifecycle_status": "RESOLVED", "resolved_decision_class": SYNCED_EXISTING_IDENTITY, "automatic_decision": row["decision"]},
                                "Очередь синхронизирована с уже подтверждённой identity. Повторное ручное подтверждение не создавалось.")
                    synced += 1
                    continue
                if action == "restore_manual":
                    q.execute("""UPDATE review_cases SET lifecycle_status='RESOLVED',resolved_decision_class=%s,
                                 resolved_at=coalesce(resolved_at, now()),priority_rank=%s,priority_reason=%s,
                                 automatic_decision=%s,proposed_sterbrust_id=NULL,explanation=%s::jsonb,
                                 source_identity_fingerprint=%s,source_identity_snapshot=%s::jsonb,updated_at=now()
                                 WHERE id=%s""",
                              (human_class, priority, reason, row["decision"], payload, fingerprint, snapshot_json, current["id"]))
                    self._audit(q, None, "REVIEW_MANUAL_DECISION_PRESERVED", "review_case", current["id"], before,
                                {"lifecycle_status": "RESOLVED", "resolved_decision_class": human_class},
                                "Указатель очереди возвращён к существующему ручному решению. Повторное подтверждение не создавалось.")
                    restored += 1
                    continue
                lifecycle = "REOPENED_SOURCE_CHANGED" if action == "reopen" else current["lifecycle_status"]
                q.execute("""UPDATE review_cases SET lifecycle_status=%s,priority_rank=%s,priority_reason=%s,
                             automatic_decision=%s,proposed_sterbrust_id=%s,explanation=%s::jsonb,
                             source_identity_fingerprint=%s,source_identity_snapshot=%s::jsonb,
                             resolved_decision_class=NULL,resolved_at=NULL,updated_at=now() WHERE id=%s""",
                          (lifecycle, priority, reason, row["decision"], proposal, payload, fingerprint, snapshot_json, current["id"]))
                if action == "reopen":
                    self._audit(q, None, "REVIEW_REQUIRED_SOURCE_CHANGED", "review_case", current["id"], before,
                                {"source_identity_fingerprint": fingerprint, "lifecycle_status": lifecycle},
                                "Материально изменились идентификационные признаки")
                    reopened += 1
                else:
                    unchanged += 1
        return {"inserted": inserted, "unchanged": unchanged, "reopened": reopened,
                "synced_existing_identity": synced, "manual_decisions_preserved": restored}

    def review_counts(self) -> dict[str, int]:
        with self._connect() as c, c.cursor() as q:
            q.execute("""SELECT count(*) FILTER (WHERE lifecycle_status<>'RESOLVED') AS open,
                                 count(*) FILTER (WHERE lifecycle_status='RESOLVED') AS resolved,
                                 count(*) FILTER (WHERE lifecycle_status='POSTPONED') AS postponed,
                                 count(*) FILTER (WHERE lifecycle_status='REOPENED_SOURCE_CHANGED') AS reopened,
                                 count(*) FILTER (WHERE lifecycle_status<>'RESOLVED' AND automatic_decision='CONFLICT') AS conflicts,
                                 count(*) FILTER (WHERE lifecycle_status<>'RESOLVED' AND automatic_decision='SAFE_NEW_PRODUCT_CANDIDATE') AS new_candidates
                          FROM review_cases""")
            result = dict(q.fetchone())
            q.execute("SELECT resolved_decision_class,count(*) AS count FROM review_cases WHERE resolved_decision_class IS NOT NULL GROUP BY resolved_decision_class")
            result["by_decision"] = {row["resolved_decision_class"]: row["count"] for row in q.fetchall()}
            return result

    def review_cases(self, *, status: str = "", supplier: str = "", decision: str = "", assignee: str = "", brand: str = "", category: str = "", opened_from: str = "", opened_to: str = "", limit: int = 200) -> list[dict]:
        clauses = ["1=1"]; params: list[Any] = []
        if status: clauses.append("rc.lifecycle_status=%s"); params.append(status)
        if supplier: clauses.append("s.code=%s"); params.append(supplier)
        if decision: clauses.append("rc.automatic_decision=%s"); params.append(decision)
        if assignee: clauses.append("rc.assigned_to_user_id=%s"); params.append(int(assignee))
        if brand: clauses.append("sp.brand_raw ILIKE %s"); params.append(f"%{brand}%")
        if category: clauses.append("coalesce(pid.identity_characteristics->>'category','') ILIKE %s"); params.append(f"%{category}%")
        if opened_from: clauses.append("rc.opened_at::date >= %s::date"); params.append(opened_from)
        if opened_to: clauses.append("rc.opened_at::date <= %s::date"); params.append(opened_to)
        qtext = " AND ".join(clauses)
        with self._connect() as c, c.cursor() as q:
            q.execute(f"""SELECT rc.*,sp.external_id,sp.name,sp.brand_raw,sp.sku,s.code AS supplier_code,s.name AS supplier_name,u.display_name AS assignee_name
                          FROM review_cases rc JOIN source_products sp ON sp.id=rc.source_product_id JOIN suppliers s ON s.id=sp.supplier_id
                           LEFT JOIN app_users u ON u.id=rc.assigned_to_user_id
                           LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id WHERE {qtext}
                          ORDER BY CASE WHEN rc.lifecycle_status='RESOLVED' THEN 1 ELSE 0 END,rc.priority_rank,rc.updated_at,rc.id LIMIT %s""", (*params, limit))
            return list(q.fetchall())

    def review_case(self, case_id: int) -> dict | None:
        with self._connect() as c, c.cursor() as q:
            q.execute("""SELECT rc.*,sp.external_id,sp.name,sp.brand_raw,sp.sku,sp.catalog_product_id,s.code AS supplier_code,s.name AS supplier_name,
                                 pid.own_model,pid.reference_model,pid.product_kind,pid.decision AS current_automatic_decision,
                                 pid.decision_reason,pid.conflicts,pid.identity_characteristics
                         FROM review_cases rc JOIN source_products sp ON sp.id=rc.source_product_id JOIN suppliers s ON s.id=sp.supplier_id
                         LEFT JOIN product_identity_decisions pid ON pid.source_product_id=sp.id WHERE rc.id=%s""", (case_id,))
            item = q.fetchone()
            if not item: return None
            item = dict(item)
            q.execute("""SELECT rd.*,u.display_name,sp.name AS sterbrust_name FROM review_decisions rd JOIN app_users u ON u.id=rd.user_id
                         LEFT JOIN sterbrust_products sp ON sp.sterbrust_product_id=rd.sterbrust_product_id
                         WHERE rd.review_case_id=%s ORDER BY rd.created_at DESC,rd.id DESC""", (case_id,))
            item["history"] = list(q.fetchall())
            q.execute("""SELECT cr.*,u.display_name,sp.name AS candidate_name FROM candidate_rejections cr JOIN app_users u ON u.id=cr.user_id
                         JOIN sterbrust_products sp ON sp.sterbrust_product_id=cr.sterbrust_product_id
                         WHERE cr.review_case_id=%s ORDER BY cr.created_at DESC,cr.id DESC""", (case_id,))
            item["rejections"] = list(q.fetchall())
            item["candidates"] = self._candidates(q, item, limit=5)
            item["proposed_candidate"] = next((row for row in item["candidates"] if str(row["sterbrust_product_id"]) == str(item.get("proposed_sterbrust_id") or "")), None)
            candidate = item["proposed_candidate"]
            if candidate:
                source_kind = item.get("product_kind") or "unknown"
                candidate_kind = classify_product_kind(candidate.get("name"), candidate.get("category_path")).product_kind
                pairs = [
                    ("Бренд", item.get("brand_raw"), candidate.get("brand_raw"), normalize_brand(item.get("brand_raw")) == normalize_brand(candidate.get("brand_raw"))),
                    ("Собственная модель", item.get("own_model"), candidate.get("model_raw"), normalize_model(item.get("own_model"), item.get("brand_raw")) == normalize_model(candidate.get("model_raw"), candidate.get("brand_raw"))),
                    ("Референсная модель", item.get("reference_model"), candidate.get("model_raw"), normalize_model(item.get("reference_model"), item.get("brand_raw")) == normalize_model(candidate.get("model_raw"), candidate.get("brand_raw"))),
                    ("Тип товара", source_kind, candidate_kind, source_kind == candidate_kind),
                ]
                item["comparison"] = [{"label": label, "source": left or "—", "candidate": right or "—", "status": "Совпадает" if same and left and right else ("Нет данных" if not left or not right else "Конфликт")} for label,left,right,same in pairs]
            else:
                item["comparison"] = []
            return item

    def _candidates(self, q, item: dict, *, limit: int) -> list[dict]:
        evidence = item.get("identity_characteristics") or {}
        explicit = [str(value) for value in evidence.get("candidate_sterbrust_ids", []) if value]
        proposed = str(item.get("proposed_sterbrust_id") or "")
        ordered_ids = list(dict.fromkeys(([proposed] if proposed else []) + explicit))
        q.execute("SELECT sterbrust_product_id FROM candidate_rejections WHERE source_product_id=%s AND evidence_fingerprint=%s", (item["source_product_id"], item["source_identity_fingerprint"]))
        rejected = {str(row["sterbrust_product_id"]) for row in q.fetchall()}
        ordered_ids = [value for value in ordered_ids if value not in rejected]
        rows: list[dict] = []
        if ordered_ids:
            q.execute("""SELECT sterbrust_product_id,name,brand_raw,model_raw,category_path,product_url
                         FROM sterbrust_products WHERE active AND sterbrust_product_id=ANY(%s)""", (ordered_ids,))
            indexed = {str(row["sterbrust_product_id"]): dict(row) for row in q.fetchall()}
            rows.extend(indexed[value] | {"candidate_source": "matcher"} for value in ordered_ids if value in indexed)
        model = item.get("own_model") or item.get("reference_model")
        normalized_model = normalize_model(model, item.get("brand_raw"))
        if normalized_model:
            q.execute("""SELECT sterbrust_product_id,name,brand_raw,model_raw,category_path,product_url
                         FROM sterbrust_products WHERE active AND normalized_model=%s LIMIT 50""", (normalized_model,))
            rows.extend(dict(row) | {"candidate_source": "model"} for row in q.fetchall())
        brand = normalize_brand(item.get("brand_raw"))
        if brand:
            q.execute("""SELECT sterbrust_product_id,name,brand_raw,model_raw,category_path,product_url
                         FROM sterbrust_products WHERE active AND normalized_brand=%s LIMIT 200""", (brand,))
            for row in q.fetchall():
                if SequenceMatcher(None, normalized_name(item.get("name")), normalized_name(row["name"])).ratio() >= 0.60:
                    rows.append(dict(row) | {"candidate_source": "название"})
        unique: list[dict] = []
        seen: set[str] = set()
        for row in rows:
            key = str(row["sterbrust_product_id"])
            if key in rejected or key in seen:
                continue
            seen.add(key); unique.append(row)
            if len(unique) >= limit:
                break
        return unique

    def search_candidates(self, case_id: int, search: str, limit: int = 50) -> list[dict]:
        item = self.review_case(case_id)
        if not item: raise KeyError("Карточка проверки не найдена")
        with self._connect() as c, c.cursor() as q:
            pattern = f"%{search.strip()}%"
            q.execute("""SELECT sterbrust_product_id,name,brand_raw,model_raw,article_raw,category_path,product_url FROM sterbrust_products
                         WHERE active AND (sterbrust_product_id ILIKE %s OR name ILIKE %s OR brand_raw ILIKE %s OR model_raw ILIKE %s OR article_raw ILIKE %s)
                         AND NOT EXISTS (SELECT 1 FROM candidate_rejections cr WHERE cr.source_product_id=%s AND cr.sterbrust_product_id=sterbrust_products.sterbrust_product_id AND cr.evidence_fingerprint=%s)
                         ORDER BY name,sterbrust_product_id LIMIT %s""", (pattern, pattern, pattern, pattern, pattern, item["source_product_id"], item["source_identity_fingerprint"], limit))
            return list(q.fetchall())

    def decide(self, case_id: int, user_id: int, decision_class: str, *, sterbrust_product_id: str | None = None, parent_sterbrust_product_id: str | None = None, comment: str = "") -> int:
        comment = comment.strip()
        if decision_class not in FINAL_MANUAL_DECISIONS | {"POSTPONED"}: raise ValueError("Недопустимое решение")
        if decision_class in {"MANUAL_CONFIRMED_NEW", "MANUAL_ACCESSORY"} and not comment: raise ValueError("Для этого решения обязателен комментарий")
        if decision_class == "MANUAL_CONFIRMED" and not sterbrust_product_id: raise ValueError("Выберите товар Sterbrust")
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT * FROM review_cases WHERE id=%s FOR UPDATE", (case_id,)); case = q.fetchone()
            if not case: raise KeyError("Карточка проверки не найдена")
            catalog_id = None
            if sterbrust_product_id:
                q.execute("SELECT catalog_product_id FROM sterbrust_products WHERE sterbrust_product_id=%s AND active", (sterbrust_product_id,)); candidate = q.fetchone()
                if not candidate: raise ValueError("Выбранный товар Sterbrust не существует или неактивен")
                catalog_id = candidate["catalog_product_id"]
            if parent_sterbrust_product_id:
                q.execute("SELECT 1 FROM sterbrust_products WHERE sterbrust_product_id=%s AND active", (parent_sterbrust_product_id,))
                if not q.fetchone(): raise ValueError("Родительский товар Sterbrust не существует или неактивен")
            if decision_class == "MANUAL_CONFIRMED":
                q.execute("SELECT supplier_id,catalog_product_id FROM source_products WHERE id=%s FOR UPDATE", (case["source_product_id"],)); source = q.fetchone()
                if source["catalog_product_id"] is not None and source["catalog_product_id"] != catalog_id: raise ValueError("Нельзя создать противоречивое ручное сопоставление")
                q.execute("SELECT id FROM source_products WHERE supplier_id=%s AND catalog_product_id=%s AND id<>%s LIMIT 1", (source["supplier_id"], catalog_id, case["source_product_id"]))
                if q.fetchone(): raise ValueError("Небезопасное объединение двух товаров одного поставщика запрещено")
                q.execute("UPDATE source_products SET catalog_product_id=%s,updated_at=now() WHERE id=%s", (catalog_id, case["source_product_id"]))
            q.execute("SELECT id FROM review_decisions WHERE review_case_id=%s ORDER BY created_at DESC,id DESC LIMIT 1", (case_id,)); previous = q.fetchone()
            q.execute("""INSERT INTO review_decisions(review_case_id,source_product_id,user_id,decision_class,sterbrust_product_id,parent_sterbrust_product_id,comment,source_identity_fingerprint,source_identity_snapshot,previous_decision_id)
                         VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s) RETURNING id""",
                      (case_id, case["source_product_id"], user_id, decision_class, sterbrust_product_id, parent_sterbrust_product_id, comment or None, case["source_identity_fingerprint"], json.dumps(case["source_identity_snapshot"], ensure_ascii=False, default=str), previous["id"] if previous else None))
            decision_id = q.fetchone()["id"]
            lifecycle = "POSTPONED" if decision_class == "POSTPONED" else "RESOLVED"
            resolved_class = None if decision_class == "POSTPONED" else decision_class
            q.execute("UPDATE review_cases SET lifecycle_status=%s,resolved_decision_class=%s,resolved_at=CASE WHEN %s='RESOLVED' THEN now() ELSE NULL END,postponed_comment=%s,updated_at=now() WHERE id=%s", (lifecycle, resolved_class, lifecycle, comment or None, case_id))
            self._audit(q, user_id, "REVIEW_DECISION_" + decision_class, "review_case", case_id, dict(case), {"lifecycle_status": lifecycle, "decision_class": decision_class, "sterbrust_product_id": sterbrust_product_id, "parent_sterbrust_product_id": parent_sterbrust_product_id}, comment or None)
            return int(decision_id)

    def reject_candidate(self, case_id: int, user_id: int, sterbrust_product_id: str, comment: str) -> int:
        if not comment.strip(): raise ValueError("Для отклонения кандидата обязателен комментарий")
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute("SELECT * FROM review_cases WHERE id=%s FOR UPDATE", (case_id,)); case = q.fetchone()
            if not case: raise KeyError("Карточка проверки не найдена")
            q.execute("SELECT 1 FROM sterbrust_products WHERE sterbrust_product_id=%s", (sterbrust_product_id,))
            if not q.fetchone(): raise ValueError("Кандидат Sterbrust не найден")
            q.execute("""INSERT INTO candidate_rejections(review_case_id,source_product_id,sterbrust_product_id,user_id,evidence_fingerprint,comment)
                         VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id""", (case_id, case["source_product_id"], sterbrust_product_id, user_id, case["source_identity_fingerprint"], comment.strip()))
            row = q.fetchone()
            self._audit(q, user_id, "CANDIDATE_REJECTED", "review_case", case_id, None, {"sterbrust_product_id": sterbrust_product_id, "evidence_fingerprint": case["source_identity_fingerprint"]}, comment.strip())
            return int(row["id"]) if row else 0

    def assign(self, case_ids: list[int], user_id: int | None, actor_id: int) -> int:
        with self._connect() as c, c.transaction(), c.cursor() as q:
            q.execute("UPDATE review_cases SET assigned_to_user_id=%s,lifecycle_status=CASE WHEN lifecycle_status='OPEN' THEN 'ASSIGNED' ELSE lifecycle_status END,updated_at=now() WHERE id=ANY(%s) AND lifecycle_status<>'RESOLVED'", (user_id, case_ids))
            count = q.rowcount
            self._audit(q, actor_id, "REVIEW_BULK_ASSIGN", "review_case_set", ",".join(map(str, case_ids)), None, {"assigned_to_user_id": user_id, "count": count}, None)
            return count

    def bulk_update(self, case_ids: list[int], action: str, actor_id: int, comment: str = "") -> int:
        decision_class = {"postpone": "POSTPONED", "mark_reviewed": "MARKED_REVIEWED"}.get(action)
        if not decision_class: raise ValueError("Недопустимое групповое действие")
        count = 0
        for case_id in case_ids:
            try:
                self.decide(case_id, actor_id, decision_class, comment=comment)
                count += 1
            except (KeyError, ValueError):
                continue
        return count


class InMemoryAdminStore:
    """Security/review QA double; never used by the production app factory."""
    def __init__(self, users: list[dict] | None = None, cases: list[dict] | None = None) -> None:
        self._users = deepcopy(users or [])
        self._cases = deepcopy(cases or [])
        self._audit_events: list[dict] = []
        self._decisions: list[dict] = []
        self._rejections: list[dict] = []

    def users_exist(self): return bool(self._users)
    def user_by_username(self, username):
        row = next((u for u in self._users if u["username"] == username.lower()), None)
        return (AuthUser(row["id"], row["username"], row["display_name"], row["role"], row.get("is_active", True)), row["password_hash"]) if row else None
    def user_by_id(self, user_id):
        row = next((u for u in self._users if u["id"] == user_id), None)
        return AuthUser(row["id"], row["username"], row["display_name"], row["role"], row.get("is_active", True)) if row else None
    def touch_login(self, user_id): pass
    def list_users(self): return [{k:v for k,v in u.items() if k != "password_hash"} for u in self._users]
    def create_user(self, username, display_name, password_hash, role, actor_id=None):
        uid = max([u["id"] for u in self._users] or [0]) + 1; self._users.append({"id":uid,"username":normalize_username(username),"display_name":display_name,"password_hash":password_hash,"role":role,"is_active":True}); self.audit(actor_id,"USER_CREATED","user",uid,None,{"role":role}); return uid
    def set_user_active(self, user_id, active, actor_id):
        row=next(u for u in self._users if u["id"]==user_id); row["is_active"]=active; self.audit(actor_id,"USER_ACTIVE_CHANGED","user",user_id,None,{"is_active":active})
    def audit(self,user_id,action,entity_type,entity_id,before,after,comment=None,correlation_id=None): self._audit_events.append({"id":len(self._audit_events)+1,"user_id":user_id,"action":action,"entity_type":entity_type,"entity_id":str(entity_id),"before_state":deepcopy(before),"after_state":deepcopy(after),"comment":comment})
    def audit_events(self,limit=200): return list(reversed(self._audit_events[-limit:]))
    def generate_review_cases(self): return {"inserted":0,"unchanged":len(self._cases),"reopened":0}
    def review_counts(self):
        return {"open":sum(c.get("lifecycle_status")!="RESOLVED" for c in self._cases),"resolved":sum(c.get("lifecycle_status")=="RESOLVED" for c in self._cases),"postponed":sum(c.get("lifecycle_status")=="POSTPONED" for c in self._cases),"reopened":sum(c.get("lifecycle_status")=="REOPENED_SOURCE_CHANGED" for c in self._cases),"conflicts":sum(c.get("lifecycle_status")!="RESOLVED" and c.get("automatic_decision")=="CONFLICT" for c in self._cases),"new_candidates":sum(c.get("lifecycle_status")!="RESOLVED" and c.get("automatic_decision")=="SAFE_NEW_PRODUCT_CANDIDATE" for c in self._cases),"by_decision":{}}
    def review_cases(self,**filters): return self._cases
    def review_case(self,case_id):
        item=next((deepcopy(c) for c in self._cases if c["id"]==case_id),None)
        if item is not None:
            item.setdefault("history",[]); item.setdefault("rejections",[]); item.setdefault("candidates",[])
            proposed=str(item.get("proposed_sterbrust_id") or "")
            item.setdefault("proposed_candidate",next((c for c in item["candidates"] if str(c.get("sterbrust_product_id"))==proposed),item["candidates"][0] if item["candidates"] and not proposed else None))
            item.setdefault("comparison",[])
        return item
    def search_candidates(self,case_id,search,limit=50): return self.review_case(case_id).get("candidates",[])[:limit]
    def decide(self,case_id,user_id,decision_class,**values):
        comment=values.get("comment","").strip()
        if decision_class in {"MANUAL_CONFIRMED_NEW","MANUAL_ACCESSORY"} and not comment: raise ValueError("Для этого решения обязателен комментарий")
        case=next(c for c in self._cases if c["id"]==case_id); case["lifecycle_status"]="POSTPONED" if decision_class=="POSTPONED" else "RESOLVED"; case["resolved_decision_class"]=None if decision_class=="POSTPONED" else decision_class
        self._decisions.append({"case_id":case_id,"decision_class":decision_class,"comment":comment}); self.audit(user_id,"REVIEW_DECISION_"+decision_class,"review_case",case_id,None,{"decision_class":decision_class},comment or None); return len(self._decisions)
    def reject_candidate(self,case_id,user_id,sterbrust_product_id,comment):
        if not comment.strip(): raise ValueError("Для отклонения кандидата обязателен комментарий")
        case=next(c for c in self._cases if c["id"]==case_id)
        case["candidates"]=[c for c in case.get("candidates",[]) if str(c.get("sterbrust_product_id")) != str(sterbrust_product_id)]
        if str(case.get("proposed_sterbrust_id") or "") == str(sterbrust_product_id): case["proposed_sterbrust_id"]=None
        self._rejections.append({"case_id":case_id,"sterbrust_product_id":sterbrust_product_id,"comment":comment}); self.audit(user_id,"CANDIDATE_REJECTED","review_case",case_id,None,{"sterbrust_product_id":sterbrust_product_id},comment); return len(self._rejections)
    def assign(self,case_ids,user_id,actor_id): return 0
    def bulk_update(self,case_ids,action,actor_id,comment=""):
        if action not in {"postpone","mark_reviewed"}: raise ValueError("Недопустимое групповое действие")
        decision="POSTPONED" if action=="postpone" else "MARKED_REVIEWED"
        for case_id in case_ids: self.decide(case_id,actor_id,decision,comment=comment)
        return len(case_ids)
