from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from enrollments import service
from enrollments.db import connect, create_schema


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_schema()
    yield


app = FastAPI(title="Enrollment Service", version="0.3.0", lifespan=lifespan)


class SessionRequest(BaseModel):
    seats: int


class EnrollRequest(BaseModel):
    session: str
    member: str


@app.get("/health")
async def health() -> dict:
    try:
        async with await connect() as conn:
            await conn.execute("SELECT 1")
    except Exception:
        raise HTTPException(status_code=503, detail="db down")
    return {"ok": True}


@app.post("/sessions", status_code=201)
async def create_session(body: SessionRequest) -> dict:
    return {"data": await service.open_session(body.seats)}


@app.get("/sessions/{session_id}")
async def get_session(session_id: str) -> dict:
    try:
        return {"data": await service.session_status(session_id)}
    except service.SessionNotFound:
        raise HTTPException(status_code=404, detail="no such session")


@app.post("/enrollments", status_code=201)
async def create_enrollment(body: EnrollRequest) -> dict:
    try:
        return {"data": await service.enroll(body.session, body.member)}
    except service.SessionNotFound:
        raise HTTPException(status_code=404, detail="no such session")
    except service.SessionFull:
        raise HTTPException(status_code=409, detail="session full")


@app.get("/enrollments/{reference}")
async def get_enrollment(reference: str) -> dict:
    try:
        return {"data": await service.find(reference)}
    except service.EnrollmentNotFound:
        raise HTTPException(status_code=404, detail="no such enrollment")
