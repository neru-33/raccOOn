import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    pool_min_size: int = 1
    pool_max_size: int = 10
    pool_timeout_s: float = 5.0
    lock_timeout_ms: int = 3000
    statement_timeout_ms: int = 5000
    idempotency_key_max_length: int = 255


def load() -> Settings:
    return Settings(
        database_url=os.environ["DATABASE_URL"],
        pool_max_size=int(os.environ.get("DB_POOL_MAX_SIZE", "10")),
        lock_timeout_ms=int(os.environ.get("DB_LOCK_TIMEOUT_MS", "3000")),
        statement_timeout_ms=int(os.environ.get("DB_STATEMENT_TIMEOUT_MS", "5000")),
    )
