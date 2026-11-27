"""Run against an empty PostgreSQL database:

    DATABASE_URL=postgresql://... pytest
"""

import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs DATABASE_URL")

from app.main import app  # noqa: E402


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def make_event(client, capacity: int) -> str:
    res = client.post("/events", json={"capacity": capacity})
    assert res.status_code == 201
    return res.json()["id"]


def test_create_and_get_reservation(client):
    event_id = make_event(client, 1)
    created = client.post("/reservations", json={"eventId": event_id, "userId": "u1"})
    assert created.status_code == 201
    assert created.json()["status"] == "CONFIRMED"

    fetched = client.get(f"/reservations/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_full_event_returns_409_and_remaining_is_zero(client):
    event_id = make_event(client, 1)
    assert client.post("/reservations", json={"eventId": event_id, "userId": "u1"}).status_code == 201
    assert client.post("/reservations", json={"eventId": event_id, "userId": "u2"}).status_code == 409
    assert client.get(f"/events/{event_id}").json()["remaining"] == 0


def test_unknown_event_returns_404(client):
    assert client.post("/reservations", json={"eventId": "missing", "userId": "u1"}).status_code == 404
