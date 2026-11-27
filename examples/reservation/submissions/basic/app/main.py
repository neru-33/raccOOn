from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from app import db, reservations

app = FastAPI(title="Reservation API", version="1.0.0")


class ReservationRequest(BaseModel):
    eventId: str
    userId: str


class Reservation(BaseModel):
    id: str
    eventId: str
    userId: str
    status: str


@app.get("/health")
def health() -> dict:
    try:
        db.ping()
    except Exception:
        raise HTTPException(status_code=503, detail="database unavailable")
    return {"status": "ok"}


@app.post("/reservations", status_code=201, response_model=Reservation)
def create_reservation(body: ReservationRequest) -> dict:
    try:
        return reservations.reserve(body.eventId, body.userId)
    except reservations.EventNotFound:
        raise HTTPException(status_code=404, detail="event not found")
    except reservations.SoldOut:
        raise HTTPException(status_code=409, detail="event is full")


@app.get("/reservations/{reservation_id}", response_model=Reservation)
def get_reservation(reservation_id: str) -> dict:
    try:
        return reservations.get(reservation_id)
    except reservations.ReservationNotFound:
        raise HTTPException(status_code=404, detail="reservation not found")
