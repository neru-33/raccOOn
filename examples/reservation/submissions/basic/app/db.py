import os

import psycopg

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    capacity    INTEGER NOT NULL CHECK (capacity >= 0),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS reservations (
    id          TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL REFERENCES events (id),
    user_id     TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS reservations_event_id_idx ON reservations (event_id);
"""


def connect() -> psycopg.Connection:
    """Open a connection to the database given by DATABASE_URL."""
    return psycopg.connect(os.environ["DATABASE_URL"])


def create_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA)


def ping() -> None:
    with connect() as conn:
        conn.execute("SELECT 1")
