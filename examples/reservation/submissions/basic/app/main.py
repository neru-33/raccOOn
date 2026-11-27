from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from app import db, events, reservations


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.create_schema()
    yield


app = FastAPI(title="Reservation API", version="1.0.0", lifespan=lifespan)


class EventRequest(BaseModel):
    capacity: int = Field(ge=0)


class Event(BaseModel):
    id: str
    capacity: int
    remaining: int


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


@app.post("/events", status_code=201, response_model=Event)
def create_event(body: EventRequest) -> dict:
    return events.create(body.capacity)


@app.get("/events/{event_id}", response_model=Event)
def get_event(event_id: str) -> dict:
    try:
        return events.get(event_id)
    except events.EventNotFound:
        raise HTTPException(status_code=404, detail="event not found")


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
