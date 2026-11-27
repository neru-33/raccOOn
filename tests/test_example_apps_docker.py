"""Run the reservation example submissions through the compose-postgres-k6 runner.

Needs Docker and builds images, so it runs only with RACCOON_DOCKER_TESTS=1.
"""

from __future__ import annotations

import json
import os
import shutil
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

from evaluator import contracts
from evaluator.runners import open_session

pytestmark = pytest.mark.skipif(
    os.environ.get("RACCOON_DOCKER_TESTS") != "1", reason="set RACCOON_DOCKER_TESTS=1 to run Docker tests"
)

ROOT = contracts.CONTRACTS_DIR.parent
TASK_DIR = ROOT / "examples" / "reservation"
TASK = json.loads((TASK_DIR / "task.json").read_text(encoding="utf-8"))
REF = {"path": "README.md", "start_line": 1, "end_line": 1}


def _call(method, path, body, codes, id_ptr, state_ptr, state_value):
    return {
        "protocol": "http",
        "method": method,
        "path_template": path,
        "query_template": {},
        "body_template": body,
        "success_codes": codes,
        "response_fields": {
            "reservation_id": {"pointer": id_ptr},
            "confirmed": {"pointer": state_ptr, "equals": state_value},
        },
        "source_ref": REF,
    }


CANDIDATES = {
    "basic": {
        "create_reservation": _call("POST", "/reservations", {"eventId": "{event_id}", "userId": "{user_id}"},
                                    [201], "/id", "/status", "CONFIRMED"),
        "get_reservation": _call("GET", "/reservations/{reservation_id}", None, [200], "/id", "/status", "CONFIRMED"),
    },
    "local-lock": {
        "create_reservation": _call("POST", "/enrollments", {"session": "{event_id}", "member": "{user_id}"},
                                    [201], "/data/reference", "/data/state", "accepted"),
        "get_reservation": _call("GET", "/enrollments/{reservation_id}", None, [200],
                                 "/data/reference", "/data/state", "accepted"),
    },
    "robust": {
        "create_reservation": _call("POST", "/v1/events/{event_id}/bookings", {"userId": "{user_id}"},
                                    [201], "/booking/bookingId", "/booking/status", "confirmed"),
        "get_reservation": _call("GET", "/v1/bookings/{reservation_id}", None, [200],
                                 "/booking/bookingId", "/booking/status", "confirmed"),
    },
}


def _session(tmp_path: Path, app: str):
    snapshot = tmp_path / app
    shutil.copytree(TASK_DIR / "submissions" / app, snapshot, ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
    run_meta = {
        "schema_version": 1,
        "run_id": f"run-example-{app}-{uuid.uuid4().hex[:6]}",
        "task_id": TASK["task_id"],
        "submission_sha256": "0" * 64,
    }
    return open_session(TASK, TASK_DIR, snapshot, run_meta)


def _run(tmp_path: Path, app: str) -> tuple[dict, dict]:
    session = _session(tmp_path, app)
    try:
        started = session.start()
        assert started["status"] == "ready", started
        probe = session.probe_mapping(CANDIDATES[app])
        assert probe["status"] == "verified", probe
        obs = session.run_checks(probe["mapping_id"])
    finally:
        session.close()
    rows = {(o["test_id"], o["round"]): o["metrics"] for o in obs["observations"]}
    print(app, json.dumps([rows.get(("reservation.concurrent", r)) for r in (1, 2, 3)]))
    return started, rows


@pytest.mark.parametrize("app", ["basic", "robust"])
def test_correct_apps_confirm_exactly_one(tmp_path: Path, app: str) -> None:
    started, rows = _run(tmp_path, app)
    assert started["discovered"] and started["discovered"][0]["kind"] == "openapi"
    assert rows[("reservation.functional", 1)] == {"created": True, "retrieved": True}
    for r in (1, 2, 3):
        assert rows[("reservation.concurrent", r)] == {"confirmed_responses": 1, "persisted_confirmed": 1}


def test_local_lock_app_oversells_with_multiple_workers(tmp_path: Path) -> None:
    started, rows = _run(tmp_path, "local-lock")
    assert rows[("reservation.functional", 1)] == {"created": True, "retrieved": True}
    concurrent = [rows[("reservation.concurrent", r)] for r in (1, 2, 3)]
    assert any(m["persisted_confirmed"] >= 2 for m in concurrent), concurrent


def test_local_lock_readme_create_example_is_not_reproducible(tmp_path: Path) -> None:
    session = _session(tmp_path, "local-lock")
    try:
        assert session.start()["status"] == "ready"
        wrong = json.loads(json.dumps(CANDIDATES["local-lock"]))
        wrong["create_reservation"]["path_template"] = "/reserve"
        result = session.probe_mapping(wrong)
        assert (result["failed_stage"], result["http_status"]) == ("status_code", 404)
        paths = session._discover()[0]["document"]["paths"]
        assert "/enrollments" in paths
    finally:
        session.close()


def _post(base: str, path: str, body: dict, headers: dict) -> tuple[int, dict, dict]:
    req = urllib.request.Request(base + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            status, headers, body = resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as err:
        status, headers, body = err.code, err.headers, err.read()
    return status, {k.lower(): v for k, v in headers.items()}, json.loads(body)


def test_robust_app_idempotency_and_duplicate_user(tmp_path: Path) -> None:
    session = _session(tmp_path, "robust")
    try:
        assert session.start()["status"] == "ready"
        event_id = session._seed_event("idem", 5)
        base, path = session._base_url, f"/v1/events/{event_id}/bookings"
        key = {"Idempotency-Key": str(uuid.uuid4())}

        first = _post(base, path, {"userId": "u1"}, key)
        retry = _post(base, path, {"userId": "u1"}, key)
        assert first[0] == retry[0] == 201
        assert retry[2] == first[2] and retry[1].get("idempotent-replayed") == "true"

        reused = _post(base, path, {"userId": "u2"}, key)
        assert reused[0] == 422 and reused[1]["content-type"].startswith("application/problem+json")

        duplicate = _post(base, path, {"userId": "u1"}, {})
        assert duplicate[0] == 409 and duplicate[2]["type"] == "/problems/already-booked"
    finally:
        session.close()
