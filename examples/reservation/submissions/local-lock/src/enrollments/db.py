import os

import psycopg

SCHEMA = [
    """CREATE TABLE IF NOT EXISTS sessions (
        id     TEXT PRIMARY KEY,
        seats  INTEGER NOT NULL
    )""",
    """CREATE TABLE IF NOT EXISTS enrollments (
        reference   TEXT PRIMARY KEY,
        session_id  TEXT NOT NULL REFERENCES sessions (id),
        member      TEXT NOT NULL,
        state       TEXT NOT NULL
    )""",
]


async def connect() -> psycopg.AsyncConnection:
    return await psycopg.AsyncConnection.connect(os.environ["DATABASE_URL"], autocommit=True)


async def create_schema() -> None:
    async with await connect() as conn:
        for statement in SCHEMA:
            try:
                await conn.execute(statement)
            except (psycopg.errors.UniqueViolation, psycopg.errors.DuplicateTable):
                # Another worker created it at the same moment.
                pass
