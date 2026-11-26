BEGIN;

CREATE TABLE app_access_profiles (
    code text PRIMARY KEY CHECK (code ~ '^[A-Z][A-Z0-9_]{2,47}$'),
    name text NOT NULL CHECK (length(btrim(name)) BETWEEN 1 AND 100),
    base_role text NOT NULL CHECK (base_role IN ('ADMIN','OPERATOR','VIEWER')),
    permissions jsonb NOT NULL CHECK (jsonb_typeof(permissions)='array'),
    is_builtin boolean NOT NULL DEFAULT false,
    updated_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO app_access_profiles(code,name,base_role,permissions,is_builtin) VALUES
 ('ADMIN','Главный администратор','ADMIN','["*"]',true),
 ('SUPPLIER_MANAGER','Менеджер поставщиков','OPERATOR',
  '["catalog.read","supplier.manage","matching.read","matching.manage","xml.read","xml.generate","reports.read","settings.read","applications.read"]',true),
 ('CONTENT_MANAGER','Контент-менеджер','OPERATOR',
  '["competitor.read","applications.read"]',true),
 ('ANALYST','Аналитик','VIEWER',
  '["catalog.read","matching.read","xml.read","competitor.read","reports.read","applications.read"]',true),
 ('OBSERVER','Наблюдатель','VIEWER','["catalog.read"]',true);

ALTER TABLE app_users ADD COLUMN must_change_password boolean NOT NULL DEFAULT false;
ALTER TABLE app_users ADD COLUMN access_profile text REFERENCES app_access_profiles(code);
CREATE INDEX app_users_access_profile_idx ON app_users(access_profile);
-- Existing accounts, password hashes, sessions and history are retained.

COMMIT;
