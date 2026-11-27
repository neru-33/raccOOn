from __future__ import annotations

import json
from copy import deepcopy

import pytest

from evaluator import agent, contracts
from evaluator.models import ModelRequestError, ModelTurn, ToolCall
from evaluator.runners.probe import ProbeLog, SequenceResult


def example(name):
    path = contracts.CONTRACTS_DIR / "examples" / "reservation" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


class ScriptedModel:
    model_info = {"client": "fake", "endpoint_host": None, "model": "test"}

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.history = []

    def complete(self, messages, tools, response_schema):
        assert [tool["function"]["name"] for tool in tools] == contracts.tool_names("agent")
        assert response_schema is None
        self.history.append(deepcopy(messages))
        reply = next(self.turns)
        if isinstance(reply, Exception):
            raise reply
        return reply() if callable(reply) else reply


class FakeRunner:
    def __init__(self, outcomes=(), startup_error=None):
        self.observations = example("observations.oversell")
        self.run_meta = {key: self.observations[key] for key in ("schema_version", "run_id", "task_id", "submission_sha256")}
        self.outcomes = iter(outcomes)
        self.startup_error = startup_error
        self.log = ProbeLog()
        self.calls = []

    def start(self):
        self.calls.append(("start",))
        return {
            "status": "error" if self.startup_error else "ready",
            "discovered": [{"kind": "openapi", "location": "/openapi.json", "document": {
                "paths": {"/enrollments": {"post": {}}}
            }, "truncated": False}],
            "tools": [],
            "error": self.startup_error,
        }

    def probe_mapping(self, candidate, documented_operations=None):
        self.calls.append(("probe", candidate))
        if documented_operations is not None:
            self.log.documented = documented_operations
        outcome = next(self.outcomes)
        mapping_id = f"map-{self.log.attempts + 1}" if outcome.ok else None
        return self.log.record(candidate, outcome, mapping_id)

    def run_checks(self, mapping_id):
        self.calls.append(("checks", mapping_id))
        return {**self.observations, "mapping_id": mapping_id}

    def mapping_artifact(self):
        return self.log.artifact(self.run_meta)


def call(name, arguments, call_id=None):
    return ToolCall(call_id or name, name, arguments)


def probe(candidate, documented=None, call_id=None):
    args = {"candidate": candidate}
    if documented is not None:
        args["documented_operations"] = documented
    return call("probe_mapping", args, call_id)


def tool_results(messages):
    return {message["tool_call_id"]: json.loads(message["content"]) for message in messages if message["role"] == "tool"}


def test_readme_candidate_can_be_retried_with_openapi_and_checks_run_on_model_stop(tmp_path):
    (tmp_path / "README.md").write_text("Use POST /reserve\n", encoding="utf-8")
    task = example("task")
    mapping = example("mapping.oversell")
    documented = {name: {k: v for k, v in spec.items() if k != "probe_status"}
                  for name, spec in mapping["documented_operations"].items()}
    first, corrected = (entry["candidate"] for entry in mapping["probe_history"])
    runner = FakeRunner([
        SequenceResult(False, failed_stage="status_code", failed_operation="create_reservation", http_status=404),
        SequenceResult(True),
    ])
    model = ScriptedModel(
        ModelTurn([call("start_submission", {})]),
        ModelTurn([probe(first, documented, "readme-probe")]),
        ModelTurn([probe(corrected, call_id="openapi-probe")]),
        ModelTurn(content="Mapping verified"),
    )

    result = agent.orchestrate(task, tmp_path, runner, model)

    initial = json.loads(model.history[0][1]["content"])
    assert initial["operations"] == task["operations"]
    assert initial["readme"]["content"] == "1\tUse POST /reserve"
    assert initial["files"]["files"] == [{"path": "README.md", "lines": 1}]
    assert tool_results(model.history[1])["start_submission"]["discovered"][0]["location"] == "/openapi.json"
    assert tool_results(model.history[2])["readme-probe"]["http_status"] == 404
    assert tool_results(model.history[3])["openapi-probe"]["mapping_id"] == "map-2"
    assert runner.calls == [("start",), ("probe", first), ("probe", corrected), ("checks", "map-2")]
    assert result["mapping_status"] == "verified"
    assert result["probe_attempts"] == 2
    assert result["mapping_artifact"]["documented_operations"] == mapping["documented_operations"]
    assert result["observations"] == runner.observations
    assert result["model_info"] == model.model_info
    assert result["error"] is None


def test_host_rejects_invalid_arguments_and_enforces_two_probe_limit(tmp_path):
    task = example("task")
    candidate = example("mapping.correct")["operations"]
    invalid = deepcopy(candidate)
    invalid["create_reservation"]["path_template"] = "https://example.invalid/reserve"
    runner = FakeRunner([SequenceResult(False), SequenceResult(False)])
    model = ScriptedModel(
        ModelTurn([
            call("start_submission", {}),
            call("run_checks", {"mapping_id": "unverified"}),
            call("shell", {}, "unknown-tool"),
            call("read_source", {"path": "../.env"}),
            probe(invalid, call_id="invalid-probe"),
            probe(candidate, call_id="first-probe"),
            probe(candidate, call_id="second-probe"),
            probe(candidate, call_id="third-probe"),
        ]),
        ModelTurn(content="No verified mapping"),
    )

    result = agent.orchestrate(task, tmp_path, runner, model)

    replies = tool_results(model.history[1])
    assert replies["run_checks"]["error"]["code"] == "unknown_mapping"
    assert replies["unknown-tool"]["error"]["code"] == "invalid_arguments"
    assert replies["read_source"]["error"]["code"] == "path_denied"
    assert replies["invalid-probe"]["error"]["code"] == "invalid_arguments"
    assert replies["third-probe"]["error"]["code"] == "budget_exhausted"
    assert runner.calls == [("start",), ("probe", candidate), ("probe", candidate)]
    assert result["mapping_status"] == "mapping_unresolved"
    assert result["probe_attempts"] == 2
    assert result["observations"] is None


@pytest.mark.parametrize("termination", ["checks", "turn_limit", "deadline", "model_error"])
def test_ends_after_checks_and_uses_verified_mapping_when_budget_expires(tmp_path, monkeypatch, termination):
    task = example("task")
    candidate = example("mapping.correct")["operations"]
    runner = FakeRunner([SequenceResult(True)])
    turns = [ModelTurn([call("start_submission", {}), probe(candidate)])]
    if termination == "turn_limit":
        monkeypatch.setattr(agent, "MAX_TURNS", 1)
    elif termination == "model_error":
        turns.append(ModelRequestError("Model request timed out"))
    elif termination == "deadline":
        clock = [0]
        monkeypatch.setattr(agent, "monotonic", lambda: clock[0])

        def late_turn():
            clock[0] = agent.TIME_LIMIT_SECONDS
            return ModelTurn([call("start_submission", {})])

        turns.append(late_turn)
    else:
        turns.append(ModelTurn([
            call("run_checks", {"mapping_id": "map-1"}, "explicit"),
            call("run_checks", {"mapping_id": "map-1"}, "duplicate"),
        ]))

    result = agent.orchestrate(task, tmp_path, runner, ScriptedModel(*turns))

    assert result["mapping_status"] == "verified"
    assert result["probe_attempts"] == 1
    expected_calls = [("start",), ("probe", candidate)]
    if termination == "model_error":
        assert result["error"]["code"] == "agent_unavailable"
        assert result["observations"] is None
    else:
        assert result["error"] is None
        assert result["observations"]["mapping_id"] == "map-1"
        expected_calls.append(("checks", "map-1"))
    assert runner.calls == expected_calls


@pytest.mark.parametrize(("cause", "code"), [("submission", "submission_start_failed"), ("environment", "execution_error")])
def test_start_failure_preserves_runner_diagnostics(tmp_path, cause, code):
    runner = FakeRunner(startup_error={"stage": "readiness", "message": "App unavailable", "cause": cause, "log_excerpt": "log"})
    model = ScriptedModel(ModelTurn([call("start_submission", {}), call("start_submission", {})]))

    result = agent.orchestrate(example("task"), tmp_path, runner, model)

    assert runner.calls == [("start",)]
    assert result["mapping_status"] == "not_reached"
    assert result["error"] == {"code": code, "stage": "readiness", "message": "App unavailable", "log_excerpt": "log"}


def test_check_failure_keeps_verified_mapping_and_partial_observations(tmp_path):
    task = example("task")
    candidate = example("mapping.correct")["operations"]
    runner = FakeRunner([SequenceResult(True)])
    runner.observations["status"] = "execution_error"
    runner.observations["observations"] = runner.observations["observations"][:1]
    runner.observations["error"] = {
        "stage": "k6",
        "message": "One request timed out",
        "test_id": "reservation.concurrent",
        "round": 1,
    }
    model = ScriptedModel(ModelTurn([
        call("start_submission", {}),
        probe(candidate),
        call("run_checks", {"mapping_id": "map-1"}),
    ]))

    result = agent.orchestrate(task, tmp_path, runner, model)

    assert result["mapping_status"] == "verified"
    assert result["error"] is None
    assert result["observations"] == {**runner.observations, "mapping_id": "map-1"}
    assert len(model.history) == 1


@pytest.mark.parametrize("runner_id", ["compose-postgres-k6", None])
def test_tasks_without_operations_do_not_call_the_model(tmp_path, runner_id):
    runner = FakeRunner()
    task = {**example("task"), "operations": {}, "runner_id": runner_id}
    model = ScriptedModel()

    result = agent.orchestrate(task, tmp_path, runner, model)

    assert result["mapping_status"] == "not_required"
    assert result["probe_attempts"] == 0
    assert result["model_info"] is None
    if runner_id is None:
        assert runner.calls == [] and result["observations"] is None
    else:
        assert runner.calls == [("start",), ("checks", None)]
        assert result["observations"]["mapping_id"] is None
    assert model.history == []


class RejectingRunner(FakeRunner):
    """Rejects the first candidate before any HTTP request, like the real runner's semantic check."""

    def __init__(self, outcomes=()):
        super().__init__(outcomes)
        self.rejected = False

    def probe_mapping(self, candidate, documented_operations=None):
        if not self.rejected:
            self.rejected = True
            self.calls.append(("rejected", candidate))
            raise agent.ToolError("invalid_arguments", "candidate rejected before probing: unknown template variables")
        return super().probe_mapping(candidate, documented_operations)


def test_candidate_rejected_before_probing_uses_no_probe_budget(tmp_path):
    task = example("task")
    candidate = example("mapping.correct")["operations"]
    runner = RejectingRunner([SequenceResult(False), SequenceResult(True)])
    model = ScriptedModel(
        ModelTurn([call("start_submission", {}), probe(candidate, call_id="rejected-probe")]),
        ModelTurn([probe(candidate, call_id="first-probe"), probe(candidate, call_id="second-probe")]),
        ModelTurn(content="done"),
    )

    result = agent.orchestrate(task, tmp_path, runner, model)

    rejected = tool_results(model.history[1])["rejected-probe"]["error"]
    assert rejected["code"] == "invalid_arguments"
    assert "unknown template variables" in rejected["message"]
    assert tool_results(model.history[2])["second-probe"]["status"] == "verified"
    assert result["mapping_status"] == "verified"
    assert result["probe_attempts"] == 2
