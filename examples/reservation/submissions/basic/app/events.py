import uuid

from app.db import connect


class EventNotFound(Exception):
    pass


def create(capacity: int) -> dict:
    event_id = str(uuid.uuid4())
    with connect() as conn:
        conn.execute("INSERT INTO events (id, capacity) VALUES (%s, %s)", (event_id, capacity))
    return {"id": event_id, "capacity": capacity, "remaining": capacity}


def get(event_id: str) -> dict:
    with connect() as conn:
        row = conn.execute(
            "SELECT e.capacity, count(r.id) FROM events e "
            "LEFT JOIN reservations r ON r.event_id = e.id AND r.status = 'CONFIRMED' "
            "WHERE e.id = %s GROUP BY e.capacity",
            (event_id,),
        ).fetchone()
    if row is None:
        raise EventNotFound(event_id)
    capacity, confirmed = row
    return {"id": event_id, "capacity": capacity, "remaining": capacity - confirmed}
