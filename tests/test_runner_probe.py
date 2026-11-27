from __future__ import annotations

import json
from itertools import count
from pathlib import Path

import pytest

from evaluator import contracts
from evaluator.runners.probe import (
    HttpResponse,
    ProbeLog,
    TransportError,
    documented_probe_status,
    run_sequence,
    validate_candidate,
)

EXAMPLES = contracts.CONTRACTS_DIR / "examples" / "reservation"


def _load(name: str) -> dict:
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


TASK = _load("task.json")
OPS = TASK["operations"]


class FakeApp:
    """Minimal in-memory app for the /sessions and /enrollments API of the oversell example."""

    def __init__(self, get_returns_other_id: bool = False) -> None:
        self.sessions: dict[str, int] = {}
        self.rows: dict[str, dict] = {}
        self.ids = count(1)
        self.get_returns_other_id = get_returns_other_id
        self.calls: list[tuple[str, str]] = []

    def send(self, method, path, headers, body, timeout):
        self.calls.append((method, path))
        if method == "POST" and path == "/sessions":
            sid = f"s-{next(self.ids)}"
            self.sessions[sid] = json.loads(body)["seats"]
            return HttpResponse(201, json.dumps({"data": {"id": sid}}).encode())
        if method == "GET" and path.startswith("/sessions/"):
            sid = path.rsplit("/", 1)[1]
            taken = sum(1 for r in self.rows.values() if r["session"] == sid)
            return HttpResponse(200, json.dumps({"data": {"available": self.sessions[sid] - taken}}).encode())
        if method == "POST" and path == "/enrollments":
            payload = json.loads(body)
            rid = f"r-{next(self.ids)}"
            self.rows[rid] = payload
            return HttpResponse(201, json.dumps({"data": {"reference": rid, "state": "accepted"}}).encode())
        if method == "GET" and path.startswith("/enrollments/"):
            rid = path.rsplit("/", 1)[1]
            if rid not in self.rows:
                return HttpResponse(404, b'{"error":"not found"}')
            shown = "other" if self.get_returns_other_id else rid
            return HttpResponse(200, json.dumps({"data": {"reference": shown, "state": "accepted"}}).encode())
        return HttpResponse(404, b"not found")


def _inputs(op: str, name: str):
    return {"capacity": 5, "user_id": "u-1"}[name]


def _candidate(variant: str, attempt: int = -1) -> dict:
    return _load(f"mapping.{variant}.json")["probe_history"][attempt]["candidate"]


def test_valid_candidate_passes_semantic_checks() -> None:
    assert validate_candidate(_candidate("oversell"), OPS) is None


@pytest.mark.parametrize(
    ("mutate", "fragment"),
    [
        (lambda c: c.pop("get_reservation"), "missing"),
        (lambda c: c["create_reservation"].update(path_template="http://evil/x"), "relative path"),
        (lambda c: c["create_reservation"]["response_fields"].pop("confirmed"), "response_fields"),
        (lambda c: c["create_reservation"].update(body_template={"x": "{secret}"}), "unknown template variables"),
        (lambda c: c["create_reservation"].update(protocol="grpc"), "protocol"),
    ],
)
def test_invalid_candidates_are_rejected(mutate, fragment: str) -> None:
    candidate = _candidate("oversell")
    mutate(candidate)
    assert fragment in validate_candidate(candidate, OPS)


def test_sequence_verifies_linked_operations() -> None:
    app = FakeApp()
    result = run_sequence(OPS, _candidate("oversell"), app, _inputs, timeout=5)
    assert result.ok
    assert result.outputs["create_event"] == {"event_id": "s-1"}
    assert result.outputs["create_reservation"] == {"reservation_id": "r-2", "confirmed": True}
    assert result.outputs["get_event"] == {"remaining_seats": 4}
    assert app.calls == [
        ("POST", "/sessions"),
        ("POST", "/enrollments"),
        ("GET", "/enrollments/r-2"),
        ("GET", "/sessions/s-1"),
    ]


def test_prior_outputs_resolve_links_for_a_partial_sequence() -> None:
    app = FakeApp()
    created = run_sequence({"create_event": OPS["create_event"]}, _candidate("oversell"), app, _inputs, timeout=5)
    counted = run_sequence(
        {"get_event": OPS["get_event"]}, _candidate("oversell"), app, _inputs, timeout=5,
        prior_outputs=created.outputs,
    )
    assert counted.ok and counted.outputs["get_event"] == {"remaining_seats": 5}


def test_wrong_create_path_fails_at_status_code() -> None:
    result = run_sequence(OPS, _candidate("oversell", 0), FakeApp(), _inputs, timeout=5)
    assert (result.failed_stage, result.failed_operation, result.http_status) == ("status_code", "create_reservation", 404)


def test_mismatched_id_fails_at_id_link() -> None:
    result = run_sequence(OPS, _candidate("oversell"), FakeApp(get_returns_other_id=True), _inputs, timeout=5)
    assert (result.failed_stage, result.failed_operation) == ("id_link", "get_reservation")


def test_404_on_linked_operation_is_id_link() -> None:
    candidate = _candidate("oversell")
    candidate["get_reservation"]["path_template"] = "/enrollments/x-{reservation_id}"
    result = run_sequence(OPS, candidate, FakeApp(), _inputs, timeout=5)
    assert (result.failed_stage, result.http_status) == ("id_link", 404)


def test_unreadable_field_fails_at_response_field() -> None:
    candidate = _candidate("oversell")
    candidate["create_reservation"]["response_fields"]["reservation_id"]["pointer"] = "/id"
    result = run_sequence(OPS, candidate, FakeApp(), _inputs, timeout=5)
    assert result.failed_stage == "response_field"
    assert result.response_excerpt and "reference" in result.response_excerpt


def test_transport_error() -> None:
    class Down:
        def send(self, *args, **kwargs):
            raise TransportError("connection refused")

    result = run_sequence(OPS, _candidate("oversell"), Down(), _inputs, timeout=5)
    assert result.failed_stage == "transport" and result.http_status is None


@pytest.mark.parametrize("variant", ["correct", "oversell", "unresolved"])
def test_documented_status_matches_examples(variant: str) -> None:
    mapping = _load(f"mapping.{variant}.json")
    for op, call in mapping["documented_operations"].items():
        assert documented_probe_status(op, call, mapping["probe_history"]) == call["probe_status"]


def test_probe_log_builds_mapping_artifact_matching_example() -> None:
    example = _load("mapping.oversell.json")
    run_meta = {k: example[k] for k in ("schema_version", "run_id", "task_id", "submission_sha256")}
    log = ProbeLog()
    log.documented = {
        op: {k: v for k, v in call.items() if k != "probe_status"}
        for op, call in example["documented_operations"].items()
    }
    app = FakeApp()
    for i, record in enumerate(example["probe_history"]):
        outcome = run_sequence(OPS, record["candidate"], app, _inputs, timeout=5)
        log.record(record["candidate"], outcome, "map-2" if outcome.ok else None)
    artifact = log.artifact(run_meta)
    assert artifact["mapping_id"] == "map-2"
    assert artifact["documented_operations"] == example["documented_operations"]
    assert artifact["operations"] == example["operations"]
    assert [r["failed_stage"] for r in artifact["probe_history"]] == ["status_code", None]


def test_probe_log_without_attempts_has_no_artifact() -> None:
    assert ProbeLog().artifact({}) is None
