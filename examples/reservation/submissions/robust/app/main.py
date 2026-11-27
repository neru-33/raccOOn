from contextlib import asynccontextmanager

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from psycopg_pool import PoolTimeout
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from app import config, db, problems, service
from app.idempotency import KeyReused, fingerprint
from app.problems import Problem


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = config.load()
    pool = db.create_pool(settings)
    pool.open(wait=True, timeout=30)
    db.migrate(pool)
    app.state.settings = settings
    app.state.pool = pool
    try:
        yield
    finally:
        pool.close()


app = FastAPI(title="Booking API", version="1.0.0", lifespan=lifespan)
app.add_exception_handler(Problem, problems.handle)


@app.exception_handler(RequestValidationError)
async def validation_problem(request: Request, exc: RequestValidationError) -> JSONResponse:
    detail = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
    return problems.response(Problem(422, "invalid-request", "Invalid request", detail), request.url.path)


@app.exception_handler(StarletteHTTPException)
async def http_problem(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    return problems.response(Problem(exc.status_code, "http-error", str(exc.detail)), request.url.path)


@app.exception_handler(Exception)
async def unavailable_problem(request: Request, exc: Exception) -> JSONResponse:
    if isinstance(exc, (PoolTimeout, *service.TRANSIENT_ERRORS)):
        problem = Problem(
            503,
            "temporarily-unavailable",
            "Temporarily unavailable",
            "the database is busy; retry with the same Idempotency-Key",
            {"Retry-After": "1"},
        )
    else:
        problem = Problem(500, "internal-error", "Internal error")
    return problems.response(problem, request.url.path)


class EventRequest(BaseModel):
    capacity: int = Field(ge=0, le=1_000_000)


class BookingRequest(BaseModel):
    userId: str = Field(min_length=1, max_length=200)


@app.get("/health")
def health(request: Request) -> dict:
    service.ping(request.app.state.pool)
    return {"status": "ok"}


@app.post("/v1/events", status_code=201)
def create_event(body: EventRequest, request: Request) -> JSONResponse:
    event = service.create_event(request.app.state.pool, body.capacity)
    return JSONResponse({"event": event}, status_code=201, headers={"Location": f"/v1/events/{event['eventId']}"})


@app.get("/v1/events/{event_id}")
def get_event(event_id: str, request: Request) -> dict:
    event = service.get_event(request.app.state.pool, event_id)
    if event is None:
        raise Problem(404, "event-not-found", "Event not found", f"event {event_id} does not exist")
    return {"event": event}


@app.post("/v1/events/{event_id}/bookings", status_code=201)
def create_booking(
    event_id: str,
    body: BookingRequest,
    request: Request,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> JSONResponse:
    settings = request.app.state.settings
    if idempotency_key is not None and not (0 < len(idempotency_key) <= settings.idempotency_key_max_length):
        raise Problem(
            400,
            "invalid-idempotency-key",
            "Invalid Idempotency-Key",
            f"key length must be 1..{settings.idempotency_key_max_length}",
        )
    request_fp = fingerprint("POST", request.url.path, body.model_dump())
    try:
        outcome, replayed = service.book(request.app.state.pool, event_id, body.userId, idempotency_key, request_fp)
    except KeyReused:
        raise Problem(
            422,
            "idempotency-key-reused",
            "Idempotency-Key reused",
            "this key was already used for a different request",
        )
    headers = {"Idempotent-Replayed": "true"} if replayed else {}
    if outcome.status == 201:
        headers["Location"] = f"/v1/bookings/{outcome.body['booking']['bookingId']}"
    return JSONResponse(outcome.body, status_code=outcome.status, media_type=outcome.media_type, headers=headers)


@app.get("/v1/bookings/{booking_id}")
def get_booking(booking_id: str, request: Request) -> dict:
    booking = service.get_booking(request.app.state.pool, booking_id)
    if booking is None:
        raise Problem(404, "booking-not-found", "Booking not found", f"booking {booking_id} does not exist")
    return {"booking": booking}
