"""End-to-end checks of the compose-postgres-k6 runner against tests/runner_app.

Needs Docker and pulls/builds images, so it runs only with RACCOON_DOCKER_TESTS=1.
"""

from __future__ import annotations

import json
import os
import shutil
import uuid
from copy import deepcopy
from pathlib import Path
from unittest.mock import Mock

import pytest

from evaluator import cli, contracts
from evaluator.models import ModelClient, ModelTurn, ToolCall
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


@pytest.mark.parametrize(("mode", "expected_score"), [("correct", 40), ("oversell", 16)])
def test_cli_grades_and_cleans_up_through_agent(tmp_path, monkeypatch, mode, expected_score):
    submission = _submission(tmp_path, {"APP_MODE": mode})
    output = tmp_path / "output"
    model = Mock(spec=ModelClient)
    model.model_info = {"client": "test", "endpoint_host": None, "model": "scripted"}
    turns = [ModelTurn([ToolCall("start", "start_submission", {})])]
    if mode == "oversell":
        (submission / "README.md").write_text("Create a reservation with POST /reserve\n", encoding="utf-8")
        incorrect = deepcopy(GOOD)
        incorrect["create_reservation"].update(
            path_template="/reserve", source_ref={"path": "README.md", "start_line": 1, "end_line": 1},
        )
        documented = {
            "create_reservation": {key: incorrect["create_reservation"][key]
                                   for key in ("protocol", "method", "path_template", "source_ref")},
        }
        turns.append(ModelTurn([ToolCall("first-probe", "probe_mapping", {
            "candidate": incorrect, "documented_operations": documented,
        })]))
    turns.extend([
        ModelTurn([ToolCall("verified-probe", "probe_mapping", {"candidate": GOOD})]),
        # The agent must still run checks when the model stops after verification.
        ModelTurn(content="The mapping is verified."),
    ])
    model.complete.side_effect = turns
    monkeypatch.setattr(cli.models, "load_client", lambda env: model)

    code = cli.main([
        "run", "--task", str(TASK_DIR / "task.json"),
        "--submission", str(submission), "--out", str(output),
    ])

    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    assert not _project_exists(dc.project_name(report["run_id"]))
    assert code == cli.ExitCode.OK, report["errors"]
    contracts.validate("report.schema.json", report)
    assert report["base_score"] == expected_score
    assert report["review_status"] == "off"
    assert report["model_info"] == model.model_info
    assert model.complete.call_count == len(turns)
    assert (output / "report.md").is_file()
    for filename, schema in (("mapping.json", "mapping.schema.json"), ("observations.json", "observations.schema.json")):
        artifact = json.loads((output / filename).read_text(encoding="utf-8"))
        contracts.validate(schema, artifact)
        for key in ("schema_version", "run_id", "task_id", "submission_sha256", "mapping_id"):
            assert artifact[key] == report[key]

    mapping = json.loads((output / "mapping.json").read_text(encoding="utf-8"))
    history = mapping["probe_history"]
    assert history[-1]["result"] == "verified"
    if mode == "oversell":
        assert report["probe_attempts"] == 2
        assert history[0]["http_status"] == 404
        assert mapping["documented_operations"]["create_reservation"]["probe_status"] == "failed"
    else:
        assert report["probe_attempts"] == 1


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
