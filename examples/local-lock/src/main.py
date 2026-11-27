from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from enrollments import service
from enrollments.db import connect

app = FastAPI(title="Enrollment Service", version="0.3.0")


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
