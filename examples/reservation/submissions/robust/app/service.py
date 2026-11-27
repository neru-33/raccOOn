"""Booking rules. One transaction per request; per-event serialization via an advisory lock."""

import psycopg
from psycopg_pool import ConnectionPool

from app import idempotency, repository
from app.idempotency import Outcome
from app.problems import MEDIA_TYPE, Problem

TRANSIENT_ERRORS = (psycopg.errors.LockNotAvailable, psycopg.errors.QueryCanceled, psycopg.OperationalError)


def _problem(status: int, slug: str, title: str, detail: str) -> Outcome:
    return Outcome(status, Problem(status, slug, title, detail).body(), MEDIA_TYPE)


def book(
    pool: ConnectionPool,
    event_id: str,
    user_id: str,
    idempotency_key: str | None = None,
    request_fingerprint: str | None = None,
) -> tuple[Outcome, bool]:
    """Return (outcome, replayed).

    Rules, in order: unknown event 404, the same user already holding a seat 409,
    no seat left 409, otherwise a confirmed booking 201. With an idempotency key
    the outcome is stored in the same transaction as the booking.
    """
    with pool.connection() as conn, conn.transaction():
        if idempotency_key:
            stored = idempotency.claim(conn, idempotency_key, request_fingerprint or "")
            if stored is not None:
                return stored, True
        repository.lock_event(conn, event_id)
        capacity = repository.event_capacity(conn, event_id)
        if capacity is None:
            outcome = _problem(404, "event-not-found", "Event not found", f"event {event_id} does not exist")
        elif repository.user_has_booking(conn, event_id, user_id):
            outcome = _problem(409, "already-booked", "Already booked", f"user {user_id} already holds a seat")
        elif repository.confirmed_count(conn, event_id) >= capacity:
            outcome = _problem(409, "sold-out", "Sold out", f"event {event_id} has no seats left")
        else:
            outcome = Outcome(201, {"booking": repository.insert_booking(conn, event_id, user_id)})
        if idempotency_key:
            idempotency.record(conn, idempotency_key, outcome)
        return outcome, False


def get_booking(pool: ConnectionPool, booking_id: str) -> dict | None:
    with pool.connection() as conn:
        return repository.find_booking(conn, booking_id)


def create_event(pool: ConnectionPool, capacity: int) -> dict:
    with pool.connection() as conn, conn.transaction():
        return repository.insert_event(conn, capacity)


def get_event(pool: ConnectionPool, event_id: str) -> dict | None:
    with pool.connection() as conn:
        return repository.find_event(conn, event_id)


def ping(pool: ConnectionPool) -> None:
    with pool.connection(timeout=2) as conn:
        conn.execute("SELECT 1")
