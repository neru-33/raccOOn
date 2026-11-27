import os

import pytest
from fastapi.testclient import TestClient

pytestmark = pytest.mark.skipif("DATABASE_URL" not in os.environ, reason="needs DATABASE_URL")

from main import app  # noqa: E402


def test_enroll_and_read_back():
    with TestClient(app) as client:
        session_id = client.post("/sessions", json={"seats": 10}).json()["data"]["id"]

        res = client.post("/enrollments", json={"session": session_id, "member": "m1"})
        assert res.status_code == 201
        reference = res.json()["data"]["reference"]

        res = client.get(f"/enrollments/{reference}")
        assert res.json()["data"]["state"] == "accepted"
        assert client.get(f"/sessions/{session_id}").json()["data"]["available"] == 9
