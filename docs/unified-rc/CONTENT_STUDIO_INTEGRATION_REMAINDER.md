# Content Studio integration remainder — source audit, 16 November 2026

REPOSITORY-VERIFIED in existing work/content-studio, no files/DB/credentials changed:
studio/app.py create_app builds its own Store and in-memory sessions. /api/login
uses a process-local one-time code; studio_session + x-csrf-token guard /api/*.
Loopback host/origin checks intentionally reject external deployment hosts.
studio/storage.py uses SQLite studio.sqlite3; projects/revisions/media/composition_runs.
studio/vk.py adds vk_settings/vk_intents/vk_steps; ai.py/ai_gateway.py add settings
tables. Projects use string IDs and optimistic revision contracts; media remain
files under data/media. studio/vk_transport.py depends on CurrentUser Windows DPAPI.
The existing Applications link is NOT shared authentication or a database migration.

Concrete remaining work, separately reviewed feature scope:
1. Add an optional shared-auth adapter to create_app rather than a second login.
   Resolve the existing Unified RC signed session through app_users/session_version;
   force password change, inactive user and revoked-session rules must apply on every
   Studio API. Mount under the same HTTPS origin and review cookie path/CSRF/CSP/
   trusted hosts; keep standalone loopback mode explicitly separate for regression.
2. Extend the current permission catalog with content read/edit/media/export and
   separate VK/site publish permissions. Map CONTENT_MANAGER explicitly; require
   ADMIN for integration secrets. Enforce per-project access and publication scopes
   server-side, including direct API/file/preview requests. Tests: unauthenticated,
   expired/revoked/inactive/forced-change, limited roles, cross-project IDs and CSRF.
3. Introduce a storage interface around existing Store; implement PostgreSQL schema
   content_studio with migrations/least-privilege role. Preserve project/media IDs,
   revision numbers/hashes, composition runs, VK intent digest uniqueness/expiry/
   prepare-confirm states, AI settings and audit. No implicit ownership assumption:
   operator must assign legacy projects explicitly; do not attach all to first ADMIN.
4. Prepare read-only SQLite export + media SHA256 manifest, restore only in isolated
   PostgreSQL first, compare counts/IDs/revisions/hashes and optimistic conflict tests.
   Preserve original SQLite/media/backups; no migration or data rewrite in this task.
5. DPAPI ciphertext cannot become a Linux shared secret by copying files. Keep it
   outside packages; provision credentials through an approved target secret store
   with DB references, audited access and rotation. Never expose tokens to browsers,
   logs, Git or project export. No VK/AI/site call is needed for offline migration QA.
6. Regress editable HTML/history/concurrent revision conflicts, image bounds/EXIF,
   project ZIP import/export, relative media, AI errors and VK idempotent confirmation.
   Only then enable an authenticated Applications destination; real publication and
   server deployment still require separate authorization.

DONE: implementation boundaries and concrete integration contract inventory.
APPLIED: documentation only. VERIFIED: source files/README, not a Studio LIVE/UI or
PostgreSQL migration test. NOT_APPLIED: SSO, shared DB, secret transfer, publication.
