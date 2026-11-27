"""Reproduce the reservation report examples using fixed source line counts."""

import json
from pathlib import Path

import pytest

from evaluator.contracts import CONTRACTS_DIR
from evaluator.core import evaluate

EXAMPLES = CONTRACTS_DIR / "examples" / "reservation"


def _example(name: str) -> dict:
    return json.loads((EXAMPLES / f"{name}.json").read_text(encoding="utf-8"))


def _case(variant: str, snapshot: Path) -> tuple[dict, dict, dict, dict, Path]:
    task = _example("task")
    orchestration = _example(f"orchestration-result.{variant}")
    source = orchestration["mapping_artifact"] or orchestration["observations"]
    if source is None:
        source = _example(f"report.{variant}")
    meta = {key: source[key] for key in ("schema_version", "run_id", "task_id", "submission_sha256")}
    artifact = None
    if variant in {"correct", "evidence-gap", "oversell"}:
        artifact = _example(f"ai-review.{variant}")
        lines = (
            {"README.md": 30, "src/enrollments/service.py": 38, "tests/test_enrollments.py": 30}
            if variant == "oversell"
            else {
                "README.md": 60,
                "app/reservations/service.py": 48,
                "app/reservations/repository.py": 52,
                "app/reservations/idempotency.py": 40,
                "tests/test_capacity.py": 40 if variant == "evidence-gap" else 64,
            }
        )
        for relative_path, count in lines.items():
            path = snapshot / relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("\n" * count, encoding="utf-8")
    review = {"status": "ok" if artifact else "skipped", "review_artifact": artifact, "error": None}
    return task, meta, orchestration, review, snapshot


@pytest.mark.parametrize("variant", ["correct", "oversell", "evidence-gap", "unresolved", "start-failed"])
def test_evaluate_reproduces_report(variant: str, tmp_path: Path) -> None:
    assert evaluate(*_case(variant, tmp_path)) == _example(f"report.{variant}")


@pytest.mark.parametrize("problem", ["duplicate", "missing", "bool_as_int", "mapping_id", "other_run"])
def test_invalid_observations_do_not_score(problem: str, tmp_path: Path) -> None:
    case = _case("correct", tmp_path)
    observations = case[2]["observations"]
    rows = observations["observations"]
    if problem == "duplicate":
        rows.append(rows[0].copy())
    elif problem == "missing":
        rows.pop()
    elif problem == "bool_as_int":
        rows[1]["metrics"]["persisted_confirmed"] = True
    elif problem == "mapping_id":
        observations["mapping_id"] = "different-mapping"
    else:
        observations["run_id"] = "different-run"

    report = evaluate(*case)
    assert report["run_status"] == "incomplete"
    assert report["mapping_status"] == "verified"
    assert report["base_score"] is None
    assert report["review_status"] == "skipped"
    assert report["errors"][0]["code"] == "invalid_observations"


def test_review_metadata_does_not_affect_functional_score(tmp_path: Path) -> None:
    case = _case("correct", tmp_path)
    case[3]["review_artifact"]["run_id"] = "different-run"

    report = evaluate(*case)
    assert report["run_status"] == "graded"
    assert report["base_score"] == 40
    assert report["review_status"] == "invalid_output"
    assert report["quality_score"] is None
    assert report["total_score"] is None


def test_execution_failure_preserves_verified_mapping(tmp_path: Path) -> None:
    case = _case("correct", tmp_path)
    case[2]["observations"].update(status="execution_error", error={"stage": "load", "message": "failed"})

    report = evaluate(*case)
    assert report["mapping_status"] == "verified"
    assert report["run_status"] == "incomplete"
    assert report["errors"] == [{"code": "execution_error", "stage": "load", "message": "failed"}]


def test_extra_feature_verification_uses_configured_observations(tmp_path: Path) -> None:
    case = _case("correct", tmp_path)
    criterion = case[0]["quality_rubric"][-1]
    criterion["verification_rules"] = [{
        "test_id": "reservation.concurrent",
        "round": 2,
        "conditions": [{"metric": "persisted_confirmed", "op": "lt", "value": 1.5}],
    }]

    report = evaluate(*case)
    assert report["quality_results"][-1]["verification_level"] == "runtime_verified"


def test_missing_review_finding_is_held(tmp_path: Path) -> None:
    case = _case("correct", tmp_path)
    case[3]["review_artifact"]["quality_findings"].pop()

    report = evaluate(*case)
    assert report["review_status"] == "ok"
    assert report["quality_results"][-1]["status"] == "insufficient_evidence"
    assert report["quality_score"] is None


def test_no_runner_or_mapping_required(tmp_path: Path) -> None:
    task, meta, orchestration, _, snapshot = _case("start-failed", tmp_path)
    task.update(runner_id=None, operations={}, metrics={}, rules=[])
    task["score_limits"]["functional"] = 0
    orchestration.update(mapping_status="not_required", error=None)
    review = {"status": "off", "review_artifact": None, "error": None}

    report = evaluate(task, meta, orchestration, review, snapshot)
    assert report["run_status"] == "graded"
    assert report["mapping_status"] == "not_required"
    assert report["base_score"] == 0
