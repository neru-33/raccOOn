"""Database tests. They need a PostgreSQL database with the events and reservations tables:

    DATABASE_URL=postgresql://... pytest
"""

import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import pytest

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs DATABASE_URL")

from fastapi.testclient import TestClient  # noqa: E402

from app import config, db, service  # noqa: E402
from app.idempotency import fingerprint  # noqa: E402
from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def pool():
    p = db.create_pool(config.load())
    p.open(wait=True)
    db.migrate(p)
    yield p
    p.close()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_event(pool, capacity: int) -> str:
    event_id = f"test-{uuid.uuid4()}"
    with pool.connection() as conn:
        conn.execute("INSERT INTO events (id, capacity) VALUES (%s, %s)", (event_id, capacity))
    return event_id


def test_last_seat_goes_to_exactly_one_of_many_concurrent_requests(pool):
    event_id = make_event(pool, 1)

    def attempt(i: int) -> int:
        outcome, _ = service.book(pool, event_id, f"user-{i}")
        return outcome.status

    with ThreadPoolExecutor(max_workers=8) as executor:
        statuses = list(executor.map(attempt, range(30)))

    assert statuses.count(201) == 1
    assert statuses.count(409) == 29


def test_same_user_cannot_hold_two_seats(pool):
    event_id = make_event(pool, 5)
    first, _ = service.book(pool, event_id, "user-1")
    second, _ = service.book(pool, event_id, "user-1")
    assert first.status == 201
    assert second.status == 409 and second.body["type"] == "/problems/already-booked"


def test_retry_with_same_key_replays_first_response(client, pool):
    event_id = make_event(pool, 5)
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    first = client.post(f"/v1/events/{event_id}/bookings", json={"userId": "u1"}, headers=headers)
    retry = client.post(f"/v1/events/{event_id}/bookings", json={"userId": "u1"}, headers=headers)
    assert first.status_code == retry.status_code == 201
    assert retry.json() == first.json()
    assert retry.headers["Idempotent-Replayed"] == "true"


def test_reusing_key_for_different_request_is_422(client, pool):
    event_id = make_event(pool, 5)
    headers = {"Idempotency-Key": str(uuid.uuid4())}
    client.post(f"/v1/events/{event_id}/bookings", json={"userId": "u1"}, headers=headers)
    reused = client.post(f"/v1/events/{event_id}/bookings", json={"userId": "u2"}, headers=headers)
    assert reused.status_code == 422
    assert reused.headers["content-type"].startswith("application/problem+json")


def test_sold_out_outcome_is_also_replayed(pool):
    event_id = make_event(pool, 1)
    service.book(pool, event_id, "winner")
    key = str(uuid.uuid4())
    fp = fingerprint("POST", f"/v1/events/{event_id}/bookings", {"userId": "late"})
    first, replayed_first = service.book(pool, event_id, "late", key, fp)
    again, replayed_again = service.book(pool, event_id, "late", key, fp)
    assert (first.status, replayed_first) == (409, False)
    assert (again.status, replayed_again) == (409, True)


def test_errors_use_problem_details(client):
    res = client.get("/v1/bookings/does-not-exist")
    assert res.status_code == 404
    assert res.headers["content-type"].startswith("application/problem+json")
    assert res.json()["type"] == "/problems/booking-not-found"
