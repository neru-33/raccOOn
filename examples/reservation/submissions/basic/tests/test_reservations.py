"""Run against a database that has the events and reservations tables:

    DATABASE_URL=postgresql://... pytest
"""

import os
import uuid

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs DATABASE_URL")

from app import db  # noqa: E402
from app.main import app  # noqa: E402

client = TestClient(app)


def make_event(capacity: int) -> str:
    event_id = f"test-{uuid.uuid4()}"
    with db.connect() as conn:
        conn.execute("INSERT INTO events (id, capacity) VALUES (%s, %s)", (event_id, capacity))
    return event_id


def test_create_and_get_reservation():
    event_id = make_event(1)
    created = client.post("/reservations", json={"eventId": event_id, "userId": "u1"})
    assert created.status_code == 201
    assert created.json()["status"] == "CONFIRMED"

    fetched = client.get(f"/reservations/{created.json()['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == created.json()


def test_full_event_returns_409():
    event_id = make_event(1)
    assert client.post("/reservations", json={"eventId": event_id, "userId": "u1"}).status_code == 201
    assert client.post("/reservations", json={"eventId": event_id, "userId": "u2"}).status_code == 409


def test_unknown_event_returns_404():
    assert client.post("/reservations", json={"eventId": "missing", "userId": "u1"}).status_code == 404
