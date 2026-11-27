"""report.md rendering and the model-written narrative."""

import json

import pytest

from evaluator import contracts, reporting
from evaluator.models import ModelRequestError, ModelTurn

EXAMPLES = contracts.CONTRACTS_DIR / "examples" / "reservation"
VARIANTS = ["correct", "oversell", "evidence-gap", "start-failed", "unresolved"]
NARRATIVE = "## 총평\n좋음\n\n## 점수 근거\n근거\n\n## 확인 질문과 응답 수준\n질문\n"


def _load(name):
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


TASK = _load("task")


class FakeModel:
    model_info = {"client": "fake", "endpoint_host": None, "model": "fake-model"}

    def __init__(self, content=NARRATIVE, error=None):
        self.content = content
        self.error = error
        self.calls = []

    def complete(self, messages, tools, response_schema):
        self.calls.append((messages, tools, response_schema))
        if self.error:
            raise self.error
        return ModelTurn(content=self.content)


@pytest.mark.parametrize("variant", VARIANTS)
def test_summary_renders_every_example_report(variant, tmp_path):
    report = _load(f"report.{variant}")
    reporting.write(report, tmp_path, task=TASK)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert json.loads((tmp_path / "report.json").read_text(encoding="utf-8")) == report
    assert f"`{report['run_status']}`" in text
    assert "AI 해설" not in text
    if report["total_score"] is None:
        assert "| 합계 | 보류 |" in text
    else:
        assert f"| 합계 | {report['total_score']}/{report['total_max_score']} |" in text


def test_ids_are_replaced_with_task_names_when_task_is_given(tmp_path):
    report = _load("report.oversell")
    reporting.write(report, tmp_path, task=TASK)
    with_task = (tmp_path / "report.md").read_text(encoding="utf-8")
    rule = next(rule for rule in TASK["rules"] if rule["id"] == "functional.create")
    assert f"{rule['description']} (`functional.create`)" in with_task

    other = tmp_path / "plain"
    other.mkdir()
    reporting.write(report, other)
    assert "| `functional.create` | 8 | 8 |" in (other / "report.md").read_text(encoding="utf-8")


def test_unavailable_quality_points_are_marked_as_held(tmp_path):
    reporting.write(_load("report.evidence-gap"), tmp_path, task=TASK)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| insufficient_evidence" not in text
    assert "보류" in text


def test_errors_keep_log_excerpt_inside_a_safe_fence(tmp_path):
    report = _load("report.start-failed")
    report["errors"][0]["log_excerpt"] = "before\n```\ninjected: [x](y)\n```\nafter"
    reporting.write(report, tmp_path, task=TASK)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "\n````\nbefore\n```\ninjected: [x](y)\n```\nafter\n````\n" in text


def test_table_cells_escape_pipes(tmp_path):
    report = _load("report.oversell")
    task = json.loads(json.dumps(TASK))
    task["rules"][0]["description"] = "a | b\nc"
    reporting.write(report, tmp_path, task=task)
    assert "a \\| b c" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_graded_report_gets_a_model_narrative_below_the_summary(tmp_path):
    report = _load("report.oversell")
    model = FakeModel()
    reporting.write(report, tmp_path, task=TASK, model=model)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert text.index("## 점수") < text.index("fake-model") < text.index("## 총평")
    assert text.rstrip().endswith("질문")

    (messages, tools, schema), = model.calls
    assert tools == [] and schema is None
    context = json.loads(messages[1]["content"])
    assert context["report"] == report
    assert context["task"]["quality_rubric"] == TASK["quality_rubric"]
    assert "reservation" not in messages[0]["content"].lower()
    for heading in ("## 총평", "## 점수 근거", "## 확인 질문과 응답 수준"):
        assert heading in messages[0]["content"]


@pytest.mark.parametrize("variant", ["start-failed", "unresolved"])
def test_incomplete_runs_do_not_call_the_model(variant, tmp_path):
    model = FakeModel()
    reporting.write(_load(f"report.{variant}"), tmp_path, task=TASK, model=model)
    assert model.calls == []
    assert "AI 해설" not in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_model_failure_keeps_summary_and_shows_the_request_error(tmp_path):
    model = FakeModel(error=ModelRequestError("Model endpoint returned HTTP 429"))
    reporting.write(_load("report.correct"), tmp_path, task=TASK, model=model)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "| 합계 | 88/100 |" in text
    assert "AI 해설을 만들지 못했습니다: Model endpoint returned HTTP 429" in text


def test_unexpected_model_errors_do_not_leak_their_message(tmp_path):
    model = FakeModel(error=RuntimeError("sk-secret-token"))
    reporting.write(_load("report.correct"), tmp_path, task=TASK, model=model)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "AI 해설을 만들지 못했습니다" in text
    assert "sk-secret-token" not in text


def test_empty_model_answer_is_reported_as_failure(tmp_path):
    reporting.write(_load("report.correct"), tmp_path, task=TASK, model=FakeModel(content="  "))
    assert "AI 해설을 만들지 못했습니다" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_markdown_code_fence_around_the_answer_is_removed(tmp_path):
    model = FakeModel(content="```markdown\n" + NARRATIVE + "```")
    reporting.write(_load("report.correct"), tmp_path, task=TASK, model=model)
    text = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "```" not in text
    assert "## 총평" in text


def test_oversized_context_drops_calls_and_evidence_but_keeps_results(tmp_path):
    report = _load("report.oversell")
    report["calls"] = {"documented_operations": {"x": "y" * 70_000}, "operations": {}}
    model = FakeModel()
    reporting.write(report, tmp_path, task=TASK, model=model)
    context = json.loads(model.calls[0][0][1]["content"])["report"]
    assert context["calls"] is None
    assert all("evidence" not in item for item in context["rule_results"])
    assert context["total_score"] == report["total_score"]
    assert context["quality_results"] == report["quality_results"]
