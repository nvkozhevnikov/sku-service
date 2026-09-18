# Docker PostgreSQL backup and restore

The named Docker volume protects data across container stop/recreate operations, but it is not a backup. Volume loss, host disk loss, operator error and corruption require a separate dump.

Run `scripts/docker_postgres_backup.ps1` on Windows or `scripts/docker_postgres_backup.sh` on Linux. Each helper invokes PostgreSQL 17 `pg_dump` in custom format and writes a host-visible file named `backups/universal_supplier_YYYYMMDDTHHMMSSZ.dump`. The directory and dumps are gitignored and excluded from the release ZIP.

Every backup verification restores into a new temporary database and compares principal counts. Run `scripts/docker_postgres_restore_test.ps1 -BackupPath <path>` or the shell equivalent. The helper drops only its timestamped restore-test database and never alters the primary database.

Recommended baseline retention is 7 daily and 4 weekly dumps, with an encrypted copy in a separate failure domain. Schedule daily backup from the host or an operations runner, monitor age/size/exit status, and run a restore test at least monthly. Agree stricter RPO/RTO and WAL/PITR separately if daily dumps are insufficient.

