"""End-to-end checks of the compose-postgres-k6 runner against tests/runner_app.

Needs Docker and pulls/builds images, so it runs only with RACCOON_DOCKER_TESTS=1.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import pytest

from evaluator import contracts
from evaluator.runners import compose as dc
from evaluator.runners import open_session

pytestmark = pytest.mark.skipif(
    os.environ.get("RACCOON_DOCKER_TESTS") != "1", reason="set RACCOON_DOCKER_TESTS=1 to run Docker tests"
)

APP_DIR = Path(__file__).parent / "runner_app"
TASK_DIR = contracts.CONTRACTS_DIR / "examples" / "reservation"
TASK = json.loads((TASK_DIR / "task.json").read_text(encoding="utf-8"))

GOOD = {
    "create_reservation": {
        "protocol": "http",
        "method": "POST",
        "path_template": "/bookings",
        "query_template": {},
        "body_template": {"event": "{event_id}", "user": "{user_id}"},
        "success_codes": [201],
        "response_fields": {
            "reservation_id": {"pointer": "/booking/id"},
            "confirmed": {"pointer": "/booking/state", "equals": "CONFIRMED"},
        },
        "source_ref": {"path": "app.py", "start_line": 1, "end_line": 1},
    },
    "get_reservation": {
        "protocol": "http",
        "method": "GET",
        "path_template": "/bookings/{reservation_id}",
        "query_template": {},
        "body_template": None,
        "success_codes": [200],
        "response_fields": {
            "reservation_id": {"pointer": "/booking/id"},
            "confirmed": {"pointer": "/booking/state", "equals": "CONFIRMED"},
        },
        "source_ref": {"path": "app.py", "start_line": 1, "end_line": 1},
    },
}


def _submission(tmp_path: Path, env: dict[str, str]) -> Path:
    sub = tmp_path / "submission"
    shutil.copytree(APP_DIR, sub)
    lines = ["services:", "  app:", "    build: .", "    environment:"]
    lines += [f"      {k}: \"{v}\"" for k, v in env.items()]
    (sub / "compose.yaml").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return sub


def _session(sub: Path):
    run_meta = {
        "schema_version": 1,
        "run_id": f"run-test-{uuid.uuid4().hex[:8]}",
        "task_id": TASK["task_id"],
        "submission_sha256": "0" * 64,
    }
    return open_session(TASK, TASK_DIR, sub, run_meta)


def _project_exists(name: str) -> bool:
    out = dc.run_command(["docker", "compose", "ls", "--all", "--format", "json"], timeout=30)
    return any(p.get("Name") == name for p in json.loads(out or "[]"))


@pytest.mark.parametrize(
    ("mode", "expect_oversell"),
    [("correct", False), ("oversell", True)],
)
def test_runner_end_to_end(tmp_path: Path, mode: str, expect_oversell: bool) -> None:
    session = _session(_submission(tmp_path, {"APP_MODE": mode}))
    try:
        started = session.start()
        assert started["status"] == "ready", started
        assert started["discovered"] == []

        wrong = json.loads(json.dumps(GOOD))
        wrong["create_reservation"]["path_template"] = "/reserve"
        documented = {
            "create_reservation": {
                "protocol": "http",
                "method": "POST",
                "path_template": "/reserve",
                "source_ref": {"path": "README.md", "start_line": 1, "end_line": 3},
            }
        }
        first = session.probe_mapping(wrong, documented)
        assert (first["status"], first["failed_stage"], first["http_status"]) == ("failed", "status_code", 404)
        second = session.probe_mapping(GOOD)
        assert second["status"] == "verified", second

        mapping = session.mapping_artifact()
        assert mapping["mapping_id"] == second["mapping_id"]
        assert mapping["documented_operations"]["create_reservation"]["probe_status"] == "failed"

        obs = session.run_checks(second["mapping_id"])
        assert obs["status"] == "complete", obs
        rows = {(o["test_id"], o["round"]): o["metrics"] for o in obs["observations"]}
        assert rows[("reservation.functional", 1)] == {"created": True, "retrieved": True}
        concurrent = [rows[("reservation.concurrent", r)] for r in (1, 2, 3)]
        if expect_oversell:
            assert any(m["persisted_confirmed"] >= 2 for m in concurrent), concurrent
        else:
            assert all(m == {"confirmed_responses": 1, "persisted_confirmed": 1} for m in concurrent), concurrent
    finally:
        session.close()
    assert not _project_exists(session.project.name)


def test_submission_that_ignores_injected_env_fails_as_submission(tmp_path: Path) -> None:
    session = _session(_submission(tmp_path, {"APP_DB_ENV": "DB_URL"}))
    try:
        started = session.start()
        assert started["status"] == "error"
        assert started["error"]["cause"] == "submission", started
        assert "DB_URL" in (started["error"]["log_excerpt"] or "")
    finally:
        session.close()
    assert not _project_exists(session.project.name)
