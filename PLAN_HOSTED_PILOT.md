# Hosted Pilot Plan

1. Replace startup-time schema mutation with portable SQLAlchemy engine/session configuration and Alembic migrations for SQLite and MySQL/PyMySQL.
2. Add users and non-null ownership to emails, analyses, tasks, business resources, and the reserved Gmail credential model; use per-user uniqueness and foreign keys.
3. Add one centralized current-user dependency. Permit the dedicated demo user only when `LOCAL_DEMO_AUTH_ENABLED=true`; fail closed otherwise until real authentication is implemented.
4. Scope every route, seed operation, analysis query, relationship lookup, and resource selection to the current user.
5. Add isolation, authentication-mode, SQLite migration, and MySQL migration/integration tests while preserving the GPT-5.6 and evidence suites.
6. Document database URLs and migration commands, then validate SQLite locally, MySQL through Docker Compose, the full suite, Docker build, and `git diff --check`.

## Schema migration risk

Existing SQLite rows do not have an owner. The initial migration must create a stable dedicated demo user, backfill every existing email, analysis, task, and business resource to that user, and only then make ownership non-null. Existing globally unique `emails.external_id` and `business_resources.title` constraints must become per-user unique constraints. SQLite cannot perform all constraint changes in place, so Alembic batch-table recreation is required; a backup is recommended before upgrading a non-demo database. The automatic startup `create_all`/SQLite `ALTER TABLE` compatibility path will be removed, so deployments must run `alembic upgrade head` before starting the application. Downgrading removes ownership and the users table and is therefore destructive to user separation.
