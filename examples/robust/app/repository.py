"""SQL for the shared events/reservations tables. Status is stored as 'CONFIRMED'."""

import uuid
from datetime import datetime

import psycopg


def lock_event(conn: psycopg.Connection, event_id: str) -> None:
    """Serialize bookings per event across every worker and instance until the transaction ends."""
    conn.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (event_id,))


def event_capacity(conn: psycopg.Connection, event_id: str) -> int | None:
    row = conn.execute("SELECT capacity FROM events WHERE id = %s", (event_id,)).fetchone()
    return None if row is None else row[0]


def confirmed_count(conn: psycopg.Connection, event_id: str) -> int:
    return conn.execute(
        "SELECT count(*) FROM reservations WHERE event_id = %s AND status = 'CONFIRMED'", (event_id,)
    ).fetchone()[0]


def user_has_booking(conn: psycopg.Connection, event_id: str, user_id: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM reservations WHERE event_id = %s AND user_id = %s AND status = 'CONFIRMED'",
        (event_id, user_id),
    ).fetchone()
    return row is not None


def insert_booking(conn: psycopg.Connection, event_id: str, user_id: str) -> dict:
    booking_id = str(uuid.uuid4())
    created_at: datetime = conn.execute(
        "INSERT INTO reservations (id, event_id, user_id, status) VALUES (%s, %s, %s, 'CONFIRMED') "
        "RETURNING created_at",
        (booking_id, event_id, user_id),
    ).fetchone()[0]
    return to_booking(booking_id, event_id, user_id, "CONFIRMED", created_at)


def find_booking(conn: psycopg.Connection, booking_id: str) -> dict | None:
    row = conn.execute(
        "SELECT id, event_id, user_id, status, created_at FROM reservations WHERE id = %s", (booking_id,)
    ).fetchone()
    return None if row is None else to_booking(*row)


def to_booking(booking_id: str, event_id: str, user_id: str, status: str, created_at: datetime) -> dict:
    return {
        "bookingId": booking_id,
        "eventId": event_id,
        "userId": user_id,
        "status": status.lower(),
        "createdAt": created_at.isoformat(),
    }
