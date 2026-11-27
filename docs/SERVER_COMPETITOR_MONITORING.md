# Мониторинг конкурентов на сервере (docker-compose.server.yml)

Панель на сервере запускается через `universal_supplier.control_plane.server_app:app`.
При `STERBRUST_COMPETITOR_SERVER=YES` она показывает раздел «Мониторинг» из схемы
`competitor` той же базы. Сборщик конкурентов в веб-процесс не подключается: панель
только читает данные.

Проверки `competitor_intel.store.Store` сохраняются: роль `competitor_runtime` без
привилегий, `search_path = competitor, pg_catalog`, нет прав на изменение и DDL,
нет прав на таблицы `public`, метка `competitor.rc_identity`. Идентичность базы
берётся из `DB_NAME`, `DB_PORT` и `DB_SYSTEM_IDENTIFIER` (вместо адреса — идентификатор кластера).

## Роль только для чтения

От имени владельца базы (`POSTGRES_ADMIN_USER`), пароль передаётся через stdin:

```sql
CREATE ROLE competitor_runtime LOGIN PASSWORD '...' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
ALTER ROLE competitor_runtime SET search_path = competitor, pg_catalog;
GRANT CONNECT ON DATABASE <DB_NAME> TO competitor_runtime;
GRANT USAGE ON SCHEMA competitor TO competitor_runtime;
GRANT SELECT ON ALL TABLES IN SCHEMA competitor TO competitor_runtime;
```

Затем в `.env.server`: `STERBRUST_COMPETITOR_SERVER=YES`, `COMPETITOR_DB_PASSWORD=...`,
и `docker compose ... up -d web`.
