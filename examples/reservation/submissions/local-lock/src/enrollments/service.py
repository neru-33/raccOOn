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


async def open_session(seats: int) -> dict:
    session_id = uuid.uuid4().hex[:12]
    async with await connect() as conn:
        await conn.execute("INSERT INTO sessions (id, seats) VALUES (%s, %s)", (session_id, seats))
    return {"id": session_id, "seats": seats, "available": seats}


async def session_status(session_id: str) -> dict:
    async with await connect() as conn:
        cur = await conn.execute("SELECT seats FROM sessions WHERE id = %s", (session_id,))
        session = await cur.fetchone()
        if session is None:
            raise SessionNotFound(session_id)
        cur = await conn.execute(
            "SELECT count(*) FROM enrollments WHERE session_id = %s AND state = 'accepted'", (session_id,)
        )
        taken = (await cur.fetchone())[0]
    return {"id": session_id, "seats": session[0], "available": session[0] - taken}


async def enroll(session_id: str, member_id: str) -> dict:
    async with _enroll_lock:
        async with await connect() as conn:
            cur = await conn.execute("SELECT seats FROM sessions WHERE id = %s", (session_id,))
            session = await cur.fetchone()
            if session is None:
                raise SessionNotFound(session_id)
            cur = await conn.execute(
                "SELECT count(*) FROM enrollments WHERE session_id = %s AND state = 'accepted'", (session_id,)
            )
            taken = (await cur.fetchone())[0]
            if taken >= session[0]:
                raise SessionFull(session_id)
            reference = uuid.uuid4().hex
            await conn.execute(
                "INSERT INTO enrollments (reference, session_id, member, state) VALUES (%s, %s, %s, 'accepted')",
                (reference, session_id, member_id),
            )
    return {"reference": reference, "session": session_id, "member": member_id, "state": "accepted"}


async def find(reference: str) -> dict:
    async with await connect() as conn:
        cur = await conn.execute(
            "SELECT reference, session_id, member, state FROM enrollments WHERE reference = %s", (reference,)
        )
        row = await cur.fetchone()
    if row is None:
        raise EnrollmentNotFound(reference)
    return {"reference": row[0], "session": row[1], "member": row[2], "state": row[3]}
