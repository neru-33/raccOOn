import uuid

from app.db import connect


class EventNotFound(Exception):
    pass


class SoldOut(Exception):
    pass


class ReservationNotFound(Exception):
    pass


def reserve(event_id: str, user_id: str) -> dict:
    """Confirm one seat for the user, or raise SoldOut when the event is full.

    The event row is locked with SELECT ... FOR UPDATE, so concurrent requests
    for the same event wait here and see each other's committed reservations.
    """
    with connect() as conn:
        event = conn.execute(
            "SELECT capacity FROM events WHERE id = %s FOR UPDATE", (event_id,)
        ).fetchone()
        if event is None:
            raise EventNotFound(event_id)
        confirmed = conn.execute(
            "SELECT count(*) FROM reservations WHERE event_id = %s AND status = 'CONFIRMED'",
            (event_id,),
        ).fetchone()[0]
        if confirmed >= event[0]:
            raise SoldOut(event_id)
        reservation_id = str(uuid.uuid4())
        conn.execute(
            "INSERT INTO reservations (id, event_id, user_id, status) VALUES (%s, %s, %s, 'CONFIRMED')",
            (reservation_id, event_id, user_id),
        )
    return {"id": reservation_id, "eventId": event_id, "userId": user_id, "status": "CONFIRMED"}


def get(reservation_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, event_id, user_id, status FROM reservations WHERE id = %s", (reservation_id,)
        ).fetchone()
    if row is None:
        raise ReservationNotFound(reservation_id)
    return {"id": row[0], "eventId": row[1], "userId": row[2], "status": row[3]}
