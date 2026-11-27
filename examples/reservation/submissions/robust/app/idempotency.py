"""Idempotency-Key handling, stored in the same transaction as the booking.

Semantics follow the IETF Idempotency-Key draft and public payment APIs
(Stripe, Toss Payments): the first outcome for a key is stored and replayed
for retries with the same request; reusing a key for a different request is
rejected with 422. Only final outcomes (2xx and 4xx) are stored; 5xx results
roll back, so the client can retry with the same key.
"""

import hashlib
import json
from dataclasses import dataclass

import psycopg
from psycopg.types.json import Jsonb


@dataclass(frozen=True)
class Outcome:
    status: int
    body: dict
    media_type: str = "application/json"


def fingerprint(method: str, path: str, body: dict) -> str:
    canonical = json.dumps({"method": method, "path": path, "body": body}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


class KeyReused(Exception):
    """The key was already used for a different request."""


def claim(conn: psycopg.Connection, key: str, request_fingerprint: str) -> Outcome | None:
    """Reserve the key in this transaction, or return the stored outcome of an earlier request.

    If another transaction holds the same key uncommitted, the INSERT waits for
    it (bounded by lock_timeout) and then sees the committed row.
    """
    inserted = conn.execute(
        "INSERT INTO idempotency_keys (key, fingerprint, response_code, response_body, media_type) "
        "VALUES (%s, %s, 0, %s, '') ON CONFLICT (key) DO NOTHING RETURNING key",
        (key, request_fingerprint, Jsonb({})),
    ).fetchone()
    if inserted:
        return None
    stored_fp, code, body, media_type = conn.execute(
        "SELECT fingerprint, response_code, response_body, media_type FROM idempotency_keys WHERE key = %s",
        (key,),
    ).fetchone()
    if stored_fp != request_fingerprint:
        raise KeyReused(key)
    return Outcome(code, body, media_type)


def record(conn: psycopg.Connection, key: str, outcome: Outcome) -> None:
    conn.execute(
        "UPDATE idempotency_keys SET response_code = %s, response_body = %s, media_type = %s WHERE key = %s",
        (outcome.status, Jsonb(outcome.body), outcome.media_type, key),
    )
