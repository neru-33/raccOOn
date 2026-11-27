"""Connection pool and the app's own schema objects."""

from psycopg_pool import ConnectionPool

from app.config import Settings

# Arbitrary constant key that serializes schema setup across worker processes.
_MIGRATION_LOCK_KEY = 7_421_001

_MIGRATION = """
CREATE TABLE IF NOT EXISTS idempotency_keys (
    key            TEXT PRIMARY KEY,
    fingerprint    TEXT NOT NULL,
    response_code  INTEGER NOT NULL,
    response_body  JSONB NOT NULL,
    media_type     TEXT NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
"""


def create_pool(settings: Settings) -> ConnectionPool:
    options = f"-c lock_timeout={settings.lock_timeout_ms} -c statement_timeout={settings.statement_timeout_ms}"
    return ConnectionPool(
        settings.database_url,
        min_size=settings.pool_min_size,
        max_size=settings.pool_max_size,
        timeout=settings.pool_timeout_s,
        kwargs={"options": options},
        open=False,
    )


def migrate(pool: ConnectionPool) -> None:
    """Create the app-owned tables. The shared events/reservations tables are never altered.

    Several workers start at once; a transaction-level advisory lock keeps
    concurrent CREATE TABLE IF NOT EXISTS statements from racing.
    """
    with pool.connection() as conn, conn.transaction():
        conn.execute("SELECT pg_advisory_xact_lock(%s)", (_MIGRATION_LOCK_KEY,))
        conn.execute(_MIGRATION)
