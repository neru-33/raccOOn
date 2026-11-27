import asyncio
import uuid

from enrollments.db import connect

# Serializes enrollments so two requests cannot take the last seat at once.
_enroll_lock = asyncio.Lock()


class SessionNotFound(Exception):
    pass


class SessionFull(Exception):
    pass


class EnrollmentNotFound(Exception):
    pass


async def enroll(session_id: str, member_id: str) -> dict:
    async with _enroll_lock:
        async with await connect() as conn:
            cur = await conn.execute("SELECT capacity FROM events WHERE id = %s", (session_id,))
            session = await cur.fetchone()
            if session is None:
                raise SessionNotFound(session_id)
            cur = await conn.execute(
                "SELECT count(*) FROM reservations WHERE event_id = %s AND status = 'CONFIRMED'",
                (session_id,),
            )
            taken = (await cur.fetchone())[0]
            if taken >= session[0]:
                raise SessionFull(session_id)
            reference = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO reservations (id, event_id, user_id, status) VALUES (%s, %s, %s, 'CONFIRMED')",
                (reference, session_id, member_id),
            )
    return {"reference": reference, "session": session_id, "member": member_id, "state": "accepted"}


async def find(reference: str) -> dict:
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT id, event_id, user_id, status FROM reservations WHERE id = %s", (reference,)
        )
        row = await cur.fetchone()
    if row is None:
        raise EnrollmentNotFound(reference)
    state = "accepted" if row[3] == "CONFIRMED" else row[3].lower()
    return {"reference": row[0], "session": row[1], "member": row[2], "state": state}
