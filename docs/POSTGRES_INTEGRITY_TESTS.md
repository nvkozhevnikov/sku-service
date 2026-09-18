# PostgreSQL integrity and Stage 3D verification

The canonical test target is the dedicated PostgreSQL 17.11 Compose service. The application role must be non-superuser and the database must be dedicated to this project.

`scripts/docker_postgres_migrate.py` checks SHA-256 for migrations 001–011, serializes execution with an advisory lock, records applied checksums, and safely skips already-applied files. A changed applied migration is a hard failure.

`scripts/run_stage3d_docker_verification.py` loads the real packaged Stage 3B Partner-ST rows into PostgreSQL, reads the 20 Stage 3C samples back with SQL, and writes source/DB/result evidence. It never treats parsed JSON as DB evidence: physical `source_products.id`, `offers.id`, `product_matches.id`, values and child counts come from SELECT statements.

The older `scripts/test_postgres_integrity.sh` remains for the closed Stage 1–3B referential regression. Stage 3D adds Compose, role, secrets, migration, SQL-trace, volume persistence and backup/restore checks. No Sterbrust write method or XML generation is part of this stage.

