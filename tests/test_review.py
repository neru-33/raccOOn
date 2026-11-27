from __future__ import annotations

import json
from copy import deepcopy

import pytest

from evaluator import contracts, review
from evaluator.models import ModelRequestError, ModelTurn, ToolCall


def example(name):
    path = contracts.CONTRACTS_DIR / "examples" / "reservation" / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def model_output():
    artifact = example("ai-review.correct")
    return {name: artifact[name] for name in ("quality_findings", "hypotheses")}


class ReviewModel:
    model_info = {"client": "mock-review", "endpoint_host": None, "model": "offline"}

    def __init__(self, *turns):
        self.turns = iter(turns)
        self.history = []

    def complete(self, messages, tools, response_schema):
        self.history.append((deepcopy(messages), [tool["function"]["name"] for tool in tools]))
        assert response_schema == contracts.review_output_schema()
        turn = next(self.turns)
        if isinstance(turn, Exception):
            raise turn
        return turn() if callable(turn) else turn


def live_review(snapshot_dir, model):
    return review.review(example("task"), snapshot_dir, example("observations.correct"), example("mapping.correct"), "live", model)


def tool_replies(messages):
    return {item["tool_call_id"]: json.loads(item["content"]) for item in messages if item["role"] == "tool"}


def test_independent_review_reads_snapshot_and_attaches_host_run_metadata(tmp_path):
    (tmp_path / "README.md").write_text("Run the app\n", encoding="utf-8")
    observation = example("observations.correct")
    model = ReviewModel(
        ModelTurn([
            ToolCall("list", "list_files", {}),
            ToolCall("read", "read_source", {"path": "README.md"}),
            ToolCall("execution", "run_checks", {"mapping_id": "map-1"}),
            ToolCall("blocked", "read_source", {"path": "../.env"}),
        ]),
        ModelTurn(content=json.dumps(model_output())),
    )

    result = live_review(tmp_path, model)

    first_messages, tools = model.history[0]
    assert [message["role"] for message in first_messages] == ["system", "user"]
    assert tools == ["list_files", "search_code", "read_source"]
    context = json.loads(first_messages[1]["content"])
    assert context["observations"] == observation["observations"]
    assert context["mapping"]["documented_operations"] == example("mapping.correct")["documented_operations"]
    assert "points_by_rating" not in context["quality_rubric"][0]
    replies = tool_replies(model.history[1][0])
    assert replies["list"]["files"] == [{"path": "README.md", "lines": 1}]
    assert replies["read"]["content"] == "1\tRun the app"
    assert replies["execution"]["error"]["code"] == "invalid_arguments"
    assert replies["blocked"]["error"]["code"] == "path_denied"

    assert result["status"] == "ok"
    assert result["error"] is None
    artifact = result["review_artifact"]
    contracts.validate("ai-review.schema.json", artifact)
    assert artifact == {
        **{key: observation[key] for key in ("schema_version", "run_id", "task_id", "submission_sha256")},
        **model_output(),
        "model_info": model.model_info,
    }


@pytest.mark.parametrize("content", ["{", '{"quality_findings":[]}', '{"quality_findings":[],"hypotheses":[],"run_id":"other"}'])
def test_malformed_model_output_never_produces_a_review_artifact(tmp_path, content):
    model = ReviewModel(ModelTurn(content=content))

    result = live_review(tmp_path, model)

    assert result["status"] == "invalid_output"
    assert result["review_artifact"] is None
    assert result["error"]["code"] == "review_invalid_output"


def test_rejects_evidence_from_another_run_before_calling_model(tmp_path):
    model = ReviewModel()

    result = review.review(
        example("task"), tmp_path, example("observations.correct"), example("mapping.oversell"), "live", model,
    )

    assert result["status"] == "invalid_output"
    assert result["error"]["stage"] == "metadata"
    assert model.history == []


def test_off_and_incomplete_modes_do_not_call_model(tmp_path):
    task = example("task")
    model = ReviewModel()
    observations = {**example("observations.correct"), "status": "execution_error"}

    assert review.review(task, tmp_path, observations, None, "off", model)["status"] == "off"
    assert review.review(task, tmp_path, observations, None, "live", model)["status"] == "skipped"
    assert model.history == []


def test_model_timeout_is_reported_as_unavailable(tmp_path):
    result = live_review(tmp_path, ReviewModel(ModelRequestError("Model request timed out")))

    assert result["status"] == "unavailable"
    assert result["review_artifact"] is None
    assert result["error"] == {"code": "review_unavailable", "stage": "model", "message": "Model request timed out"}


def test_tool_budget_fallback_completes_all_pending_tool_replies(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "MAX_TOOL_CALLS", 1)
    (tmp_path / "README.md").write_text("Run the app\n", encoding="utf-8")
    model = ReviewModel(
        ModelTurn([
            ToolCall("first", "read_source", {"path": "README.md"}),
            ToolCall("second", "read_source", {"path": "README.md"}),
        ]),
        ModelTurn(content=json.dumps(model_output())),
    )

    result = live_review(tmp_path, model)

    assert result["status"] == "ok"
    messages, tools = model.history[1]
    assert tools == []
    replies = tool_replies(messages)
    assert replies["first"]["content"] == "1\tRun the app"
    assert replies["second"]["error"]["code"] == "budget_exhausted"


@pytest.mark.parametrize("limit", ["turns", "time"])
def test_failed_final_request_after_budget_is_unavailable(tmp_path, monkeypatch, limit):
    first = ModelTurn([ToolCall("list", "list_files", {})])
    if limit == "turns":
        monkeypatch.setattr(review, "MAX_TURNS", 1)
    else:
        clock = [0]
        monkeypatch.setattr(review, "monotonic", lambda: clock[0])

        def late_turn():
            clock[0] = review.TIME_LIMIT_SECONDS
            return first

    model = ReviewModel(first if limit == "turns" else late_turn, ModelTurn(content="{}"))

    result = live_review(tmp_path, model)

    assert result["status"] == "unavailable"
    assert result["review_artifact"] is None
    assert model.history[1][1] == []
    assert len(model.history) == 2
    if limit == "time":
        assert tool_replies(model.history[1][0]) == {}
