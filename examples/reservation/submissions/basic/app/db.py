import os

import psycopg


def connect() -> psycopg.Connection:
    """Open a connection to the database given by DATABASE_URL."""
    return psycopg.connect(os.environ["DATABASE_URL"])


def ping() -> None:
    with connect() as conn:
        conn.execute("SELECT 1")
