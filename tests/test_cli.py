"""CLI integration through the public module boundaries without live calls."""

import io
import json
from copy import deepcopy

import pytest

from evaluator import cli, contracts
from evaluator.models import ModelTurn, ToolCall
from evaluator.runners import compose_postgres_k6
from evaluator.runners.build import AppBuild
from evaluator.runners.probe import SequenceResult

EXAMPLES = contracts.CONTRACTS_DIR / "examples" / "reservation"


def _example(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def _invocation(tmp_path, task_path=None, mode="off"):
    submission = tmp_path / "submission"
    submission.mkdir()
    (submission / "README.md").write_text("example\n", encoding="utf-8")
    output = tmp_path / "output"
    task_path = task_path or EXAMPLES / "task.json"
    return ["run", "--task", str(task_path), "--submission", str(submission), "--out", str(output),
            "--review", mode], output


class _Session:
    def __init__(self, snapshot, meta, variant):
        self.snapshot = snapshot
        self.meta = meta
        self.closed = False
        self.result = _example(f"orchestration-result.{variant}")
        for field in ("mapping_artifact", "observations"):
            if self.result[field] is not None:
                self.result[field].update(meta)

    def mapping_artifact(self):
        return self.result["mapping_artifact"]

    def close(self):
        assert self.snapshot.exists()
        self.closed = True


class _Model:
    model_info = {"client": "mock", "endpoint_host": None, "model": "offline"}


def _install_session(monkeypatch, variant):
    sessions = []

    def open_session(task, task_dir, snapshot, meta, *, on_check_progress=None):
        assert task_dir == EXAMPLES
        assert (snapshot / "README.md").is_file()
        session = _Session(snapshot, meta, variant)
        sessions.append(session)
        return session

    monkeypatch.setattr(cli.runners, "open_session", open_session)
    monkeypatch.setattr(cli.models, "load_client", lambda env: _Model())
    return sessions


def _report(output):
    report = json.loads((output / "report.json").read_text(encoding="utf-8"))
    contracts.validate("report.schema.json", report)
    assert (output / "report.md").is_file()
    return report


class _Terminal(io.StringIO):
    def isatty(self):
        return True


def _interactive_terminal(monkeypatch, columns="100"):
    terminal = _Terminal()
    monkeypatch.setattr(cli.sys, "stderr", terminal)
    monkeypatch.setenv("TERM", "xterm")
    monkeypatch.setenv("COLUMNS", columns)
    monkeypatch.delenv("CI", raising=False)
    return terminal


@pytest.mark.parametrize("variant", ["correct", "oversell", "evidence-gap", "unresolved", "start-failed"])
@pytest.mark.parametrize("display", ["redirected", "interactive", "ci"])
def test_live_cli_reproduces_reservation_reports(variant, display, tmp_path, monkeypatch, capsys):
    terminal = _interactive_terminal(monkeypatch) if display != "redirected" else _Terminal()
    if display == "ci":
        monkeypatch.setenv("CI", "true")
    args, output = _invocation(tmp_path, mode="live")
    sessions = _install_session(monkeypatch, variant)
    monkeypatch.setattr(cli.agent, "orchestrate", lambda *args: sessions[0].result)
    review_artifact = None
    if (EXAMPLES / f"ai-review.{variant}.json").is_file():
        review_artifact = _example(f"ai-review.{variant}")
        refs = [ref for finding in review_artifact["quality_findings"] for ref in finding["source_refs"]]
        refs.extend(hypothesis["source_ref"] for hypothesis in review_artifact["hypotheses"])
        line_counts = {}
        for ref in refs:
            path = ref["path"]
            line_counts[path] = max(line_counts.get(path, 0), ref["end_line"])
        if variant == "evidence-gap":
            line_counts["tests/test_capacity.py"] = 40
        for relative, count in line_counts.items():
            path = tmp_path / "submission" / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n" * count, encoding="utf-8")

    def review(task, snapshot, observations, mapping, mode, model, *, run_meta):
        assert sessions[0].closed
        assert observations["run_id"] == mapping["run_id"]
        assert run_meta == sessions[0].meta
        assert isinstance(model, _Model)
        artifact = {**review_artifact, **sessions[0].meta}
        return {"status": "ok", "review_artifact": artifact, "error": None}

    monkeypatch.setattr(cli.review, "review", review)

    expected = _example(f"report.{variant}")
    exit_code = cli.ExitCode.OK if expected["run_status"] == "graded" else cli.ExitCode.INCOMPLETE
    assert cli.main(args) == exit_code
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
    screen = terminal.getvalue()
    if display == "interactive":
        assert "███" in screen
        assert "Prepare submission snapshot" in screen
        assert "Initialize evaluation agent" in screen
        assert f"Report: {output / 'report.md'}" in screen
        if exit_code == cli.ExitCode.INCOMPLETE:
            assert "Evaluation incomplete" in screen
            assert "SKIP  Score functional results" in screen
            assert "DONE  Score functional results" not in screen
        elif variant == "evidence-gap":
            assert "Functional scoring complete - Quality score unavailable" in screen
            assert "Evaluation complete" not in screen
        else:
            assert "Evaluation complete" in screen
        if variant == "start-failed":
            assert "readiness: submission_start_failed - GET /health did not return 200 within 60s" in screen
            assert "Quality: Skipped" in screen
    else:
        assert screen == ""
    assert _report(output) == {**expected, **sessions[0].meta}
    artifacts = {
        "mapping.json": sessions[0].result["mapping_artifact"],
        "observations.json": sessions[0].result["observations"],
        "ai_review.json": review_artifact,
    }
    for name, expected_artifact in artifacts.items():
        path = output / name
        if expected_artifact is None:
            assert not path.exists()
        else:
            assert json.loads(path.read_text(encoding="utf-8")) == {**expected_artifact, **sessions[0].meta}
    assert sessions[0].closed
    assert not sessions[0].snapshot.exists()


def test_agent_failure_closes_runner_and_preserves_probe_history(tmp_path, monkeypatch):
    args, output = _invocation(tmp_path, mode="live")
    sessions = _install_session(monkeypatch, "unresolved")

    def fail(*args):
        raise RuntimeError("agent failed")

    review_calls = []
    monkeypatch.setattr(cli.agent, "orchestrate", fail)
    monkeypatch.setattr(cli.review, "review", lambda *args, **kwargs: review_calls.append(args))

    assert cli.main(args) == cli.ExitCode.INCOMPLETE
    report = _report(output)
    assert report["errors"][0]["code"] == "agent_unavailable"
    assert report["base_score"] is None
    assert report["probe_attempts"] == 2
    assert report["review_status"] == "skipped"
    assert not review_calls
    assert (output / "mapping.json").is_file()
    assert not (output / "observations.json").exists()
    assert sessions[0].closed
    assert not sessions[0].snapshot.exists()


def test_review_failure_preserves_functional_score(tmp_path, monkeypatch):
    terminal = _interactive_terminal(monkeypatch)
    args, output = _invocation(tmp_path, mode="live")
    sessions = _install_session(monkeypatch, "correct")
    monkeypatch.setattr(cli.agent, "orchestrate", lambda *args: sessions[0].result)

    def fail(*args, **kwargs):
        raise RuntimeError("review failed")

    monkeypatch.setattr(cli.review, "review", fail)

    assert cli.main(args) == cli.ExitCode.OK
    report = _report(output)
    assert report["base_score"] == 40
    assert report["review_status"] == "unavailable"
    assert not (output / "ai_review.json").exists()
    screen = terminal.getvalue()
    assert "FAIL  Review submission quality" in screen
    assert "Functional scoring complete - Quality review failed" in screen
    assert "Evaluation complete" not in screen
    assert "Functional: 40/40  |  Quality: Unavailable  |  Total: Unavailable" in screen


@pytest.mark.parametrize("problem", ["wrong-run", "wrong-mapping", "malformed", "non-finite"])
def test_cli_keeps_mapping_when_observations_are_invalid(problem, tmp_path, monkeypatch):
    args, output = _invocation(tmp_path)
    sessions = _install_session(monkeypatch, "correct")

    def orchestrate(task, snapshot, session, model):
        observations = session.result["observations"]
        if problem == "wrong-run":
            observations["run_id"] = "another-run"
        elif problem == "wrong-mapping":
            observations["mapping_id"] = "another-mapping"
        elif problem == "malformed":
            del observations["observations"]
        else:
            observations["observations"][1]["metrics"]["persisted_confirmed"] = float("inf")
        return session.result

    monkeypatch.setattr(cli.agent, "orchestrate", orchestrate)

    assert cli.main(args) == cli.ExitCode.INCOMPLETE
    report = _report(output)
    assert report["mapping_status"] == "verified"
    assert report["base_score"] is None
    assert report["errors"][0]["code"] == "invalid_observations"
    assert (output / "mapping.json").is_file()
    assert not (output / "observations.json").exists()
    assert sessions[0].closed


def test_malformed_orchestration_still_produces_an_incomplete_report(tmp_path, monkeypatch):
    args, output = _invocation(tmp_path)
    sessions = _install_session(monkeypatch, "correct")
    monkeypatch.setattr(cli.agent, "orchestrate", lambda *args: {})

    assert cli.main(args) == cli.ExitCode.INCOMPLETE
    report = _report(output)
    assert report["errors"][0]["stage"] == "orchestration"
    assert sessions[0].closed


def test_malformed_review_is_reported_without_losing_functional_score(tmp_path, monkeypatch):
    args, output = _invocation(tmp_path, mode="live")
    _install_session(monkeypatch, "correct")
    monkeypatch.setattr(cli.agent, "orchestrate", lambda task, snapshot, session, model: session.result)
    monkeypatch.setattr(cli.review, "review", lambda *args, **kwargs: {"status": "ok"})

    assert cli.main(args) == cli.ExitCode.OK
    report = _report(output)
    assert report["base_score"] == 40
    assert report["review_status"] == "invalid_output"
    assert not (output / "ai_review.json").exists()


def test_review_off_without_runner_needs_no_model(tmp_path, monkeypatch):
    terminal = _interactive_terminal(monkeypatch, columns="40")
    task_path = tmp_path / "task.json"
    task = _example("task")
    task.update(task_id="quality-only", runner_id=None, runner_config={}, operations={}, metrics={}, rules=[])
    task["score_limits"]["functional"] = 0
    task_path.write_text(json.dumps(task), encoding="utf-8")
    args, output = _invocation(tmp_path, task_path)

    def unexpected(*args):
        raise AssertionError("unexpected external integration")

    monkeypatch.setattr(cli.runners, "open_session", unexpected)
    monkeypatch.setattr(cli.models, "load_client", unexpected)

    assert cli.main(args) == cli.ExitCode.OK
    report = _report(output)
    assert report["mapping_status"] == "not_required"
    assert report["base_score"] == 0
    assert report["review_status"] == "off"
    screen = terminal.getvalue()
    assert "raccOOn" in screen
    assert "quality-only" in screen
    assert "Configure runner" not in screen
    assert "Review submission quality" not in screen
    assert "Functional scoring complete" in screen
    assert "Quality: Disabled" in screen
    assert "Total: Unavailable" in screen


def test_live_review_without_runner_uses_cli_run_metadata(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task = _example("task")
    criterion = next(item for item in task["quality_rubric"] if item["id"] == "documentation")
    task.update(runner_id=None, runner_config={}, operations={}, metrics={}, rules=[], quality_rubric=[criterion])
    task["score_limits"] = {"functional": 0, "quality": criterion["max_points"]}
    task_path.write_text(json.dumps(task), encoding="utf-8")
    args, output = _invocation(tmp_path, task_path, mode="live")

    class Model(_Model):
        def complete(self, messages, tools, response_schema):
            assert json.loads(messages[1]["content"])["observations"] == []
            finding = {
                "criterion_id": criterion["id"],
                "rating": "meets",
                "reason": "The README documents the example.",
                "source_refs": [{"path": "README.md", "start_line": 1, "end_line": 1}],
                "observation_refs": [],
                "feature_summary": None,
            }
            return ModelTurn(content=json.dumps({"quality_findings": [finding], "hypotheses": []}))

    monkeypatch.setattr(cli.models, "load_client", lambda env: Model())

    assert cli.main(args) == cli.ExitCode.OK
    report = _report(output)
    artifact = json.loads((output / "ai_review.json").read_text(encoding="utf-8"))
    assert report["review_status"] == "ok"
    assert report["total_score"] == criterion["points_by_rating"]["meets"]
    assert artifact["run_id"] == report["run_id"]
    assert artifact["submission_sha256"] == report["submission_sha256"]
    assert not (output / "observations.json").exists()


def test_invalid_task_fails_before_output_creation(tmp_path):
    task_path = tmp_path / "task.json"
    task_path.write_text('{"schema_version": 1}', encoding="utf-8")
    args, output = _invocation(tmp_path, task_path)

    assert cli.main(args) == cli.ExitCode.CONTRACT_ERROR
    assert not output.exists()


def test_runner_without_operations_uses_null_mapping_without_loading_model(tmp_path, monkeypatch):
    terminal = _interactive_terminal(monkeypatch)
    task_path = tmp_path / "task.json"
    task = _example("task")
    task["operations"] = {}
    task_path.write_text(json.dumps(task), encoding="utf-8")
    args, output = _invocation(tmp_path, task_path)
    calls = []

    class Session:
        def __init__(self, meta):
            self.meta = meta

        def start(self):
            calls.append("start")
            return {"status": "ready"}

        def run_checks(self, mapping_id):
            calls.append(("check", mapping_id))
            return {**_example("observations.correct"), **self.meta, "mapping_id": mapping_id}

        def close(self):
            calls.append("close")

    monkeypatch.setattr(cli.runners, "open_session", lambda task, folder, snapshot, meta, **kwargs: Session(meta))
    monkeypatch.setattr(cli.models, "load_client", lambda env: calls.append("model"))

    assert cli.main(args) == cli.ExitCode.OK
    assert _report(output)["base_score"] == 40
    assert calls == ["start", ("check", None), "close"]
    assert not (output / "mapping.json").exists()
    screen = terminal.getvalue()
    assert "DONE  Prepare and start submission" in screen
    assert "DONE  Run submission checks" in screen
    assert "Verify API mapping" not in screen
    assert "Wait for evaluation agent" not in screen


def test_interactive_progress_follows_actual_agent_calls_and_declared_check_rounds(tmp_path, monkeypatch):
    terminal = _interactive_terminal(monkeypatch)
    task = _example("task")
    task["task_id"] = "data-service"
    test_ids = {"reservation.functional": "data.ingest", "reservation.concurrent": "data.query"}
    task["metrics"] = {test_ids[key]: value for key, value in task["metrics"].items()}
    task["metrics"]["data.query"]["rounds"] = [3, 7, 9]
    scenarios = task["runner_config"]["scenarios"]
    task["runner_config"]["scenarios"] = {test_ids[key]: value for key, value in scenarios.items()}
    for rule in task["rules"]:
        rule["test_id"] = test_ids[rule["test_id"]]
    task_path = tmp_path / "task.json"
    task_path.write_text(json.dumps(task), encoding="utf-8")
    args, output = _invocation(tmp_path, task_path)

    def start(runner):
        runner._build = AppBuild("compose", runner.snapshot_dir / "compose.yaml", "app")
        runner._base_url = "http://127.0.0.1:1"

    runner_type = compose_postgres_k6.ComposePostgresK6Runner
    monkeypatch.setattr(runner_type, "_start", start)
    monkeypatch.setattr(runner_type, "_discover", lambda self: [])
    outcomes = iter([SequenceResult(False, failed_stage="status_code", http_status=404), SequenceResult(True)])
    monkeypatch.setattr(compose_postgres_k6, "run_sequence", lambda *args, **kwargs: next(outcomes))
    monkeypatch.setattr(runner_type, "_check_count_meaning", lambda *args: None)
    monkeypatch.setattr(runner_type, "_sequence_round", lambda *args: {"created": True, "retrieved": True})
    monkeypatch.setattr(runner_type, "_burst_round", lambda *args: {"confirmed_responses": 1, "persisted_confirmed": 1})

    candidate = _example("mapping.correct")["operations"]
    rejected = deepcopy(candidate)
    rejected["create_reservation"]["body_template"] = {"event": "{unknown_variable}"}
    turns = iter([
        ModelTurn([ToolCall("start", "start_submission", {})]),
        ModelTurn([ToolCall("invalid", "probe_mapping", {"candidate": rejected})]),
        ModelTurn([ToolCall("first", "probe_mapping", {"candidate": candidate})]),
        ModelTurn([ToolCall("second", "probe_mapping", {"candidate": candidate})]),
        ModelTurn([ToolCall("checks", "run_checks", {"mapping_id": "map-1"})]),
    ])

    class Model(_Model):
        def complete(self, messages, tools, response_schema):
            return next(turns)

    monkeypatch.setattr(cli.models, "load_client", lambda env: Model())

    assert cli.main(args) == cli.ExitCode.OK
    report = _report(output)
    assert report["base_score"] == 40
    assert report["probe_attempts"] == 2
    screen = terminal.getvalue()
    stages = [
        "Wait for evaluation agent", "DONE  Prepare and start submission",
        "FAIL  Verify API mapping (attempt 1)", "DONE  Verify API mapping (attempt 2)",
        "data.ingest, round 1/1", "data.query, round 2/3 (ID 7)", "DONE  Run submission checks",
    ]
    positions = [screen.index(stage) for stage in stages]
    assert positions == sorted(positions)
    assert "(attempt 3)" not in screen


def test_model_config_uses_dotenv_and_prefers_process_environment(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("EVALUATOR_MODEL=from-file\nEVALUATOR_RESPONSE_FORMAT=none\n", encoding="utf-8")
    monkeypatch.setenv("EVALUATOR_MODEL", "from-environment")
    monkeypatch.delenv("EVALUATOR_RESPONSE_FORMAT", raising=False)

    def load_client(env):
        assert env["EVALUATOR_MODEL"] == "from-environment"
        assert env["EVALUATOR_RESPONSE_FORMAT"] == "none"
        return _Model()

    monkeypatch.setattr(cli.models, "load_client", load_client)
    assert isinstance(cli._load_model(), _Model)


def test_live_review_adds_model_narrative_to_report_md_and_off_does_not(tmp_path, monkeypatch):
    task_path = tmp_path / "task.json"
    task = _example("task")
    criterion = next(item for item in task["quality_rubric"] if item["id"] == "documentation")
    task.update(runner_id=None, runner_config={}, operations={}, metrics={}, rules=[], quality_rubric=[criterion])
    task["score_limits"] = {"functional": 0, "quality": criterion["max_points"]}
    task_path.write_text(json.dumps(task), encoding="utf-8")
    narrative_calls = []

    class Model(_Model):
        def complete(self, messages, tools, response_schema):
            if response_schema is None:
                narrative_calls.append(messages)
                return ModelTurn(content="## 총평\n문서가 충분합니다.\n")
            finding = {
                "criterion_id": criterion["id"], "rating": "meets", "reason": "The README documents the example.",
                "source_refs": [{"path": "README.md", "start_line": 1, "end_line": 1}],
                "observation_refs": [], "feature_summary": None,
            }
            return ModelTurn(content=json.dumps({"quality_findings": [finding], "hypotheses": []}))

    monkeypatch.setattr(cli.models, "load_client", lambda env: Model())

    args, output = _invocation(tmp_path, task_path, mode="live")
    assert cli.main(args) == cli.ExitCode.OK
    text = (output / "report.md").read_text(encoding="utf-8")
    assert "문서가 충분합니다." in text
    assert f"{criterion['title']} (`documentation`)" in text
    assert json.loads(narrative_calls[0][1]["content"])["report"]["total_score"] == criterion["points_by_rating"]["meets"]

    (tmp_path / "second").mkdir()
    args, output = _invocation(tmp_path / "second", task_path, mode="off")
    narrative_calls.clear()
    assert cli.main(args) == cli.ExitCode.OK
    assert narrative_calls == []
    assert "AI 해설" not in (output / "report.md").read_text(encoding="utf-8")
