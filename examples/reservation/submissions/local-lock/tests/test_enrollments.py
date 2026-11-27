import os
import uuid

import psycopg
import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs DATABASE_URL")

from main import app  # noqa: E402

client = TestClient(app)


def test_enroll_and_read_back():
    session_id = f"s-{uuid.uuid4()}"
    with psycopg.connect(os.environ["DATABASE_URL"]) as conn:
        conn.execute("INSERT INTO events (id, capacity) VALUES (%s, 10)", (session_id,))

    res = client.post("/enrollments", json={"session": session_id, "member": "m1"})
    assert res.status_code == 201
    reference = res.json()["data"]["reference"]

    res = client.get(f"/enrollments/{reference}")
    assert res.json()["data"]["state"] == "accepted"
