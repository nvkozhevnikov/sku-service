BEGIN;

-- 019 seeded CONTENT_MANAGER without the Content Studio permissions that
-- accounts.BUILTINS defines, so on PostgreSQL that profile got 403 on the
-- whole studio. Only the untouched 019 seed is updated: a profile already
-- edited by an administrator is left as it is.
UPDATE app_access_profiles
   SET permissions = '["competitor.read","applications.read","content.read","content.edit","content.media","content.export","content.rules.manage","content.ai.use","content.site.export"]'::jsonb,
       updated_at = now()
 WHERE code = 'CONTENT_MANAGER'
   AND is_builtin
   AND permissions = '["competitor.read","applications.read"]'::jsonb;

COMMIT;
