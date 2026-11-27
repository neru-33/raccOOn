"""Grading core: validates artifacts, applies rules, checks review evidence.

Standard library only. No external process or model calls.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from ._review import review_report
from ._scoring import grade_rules, index_observations, validate_scoring_config

_ENVELOPE = ("schema_version", "run_id", "task_id", "submission_sha256")


def _same_run(artifact: dict, run_meta: dict) -> bool:
    return type(artifact.get("schema_version")) is int and all(
        artifact.get(field) == run_meta[field] for field in _ENVELOPE
    )


def evaluate(
    task: dict[str, Any],
    run_meta: dict[str, Any],
    orchestration_result: dict[str, Any],
    review_result: dict[str, Any],
    submission_dir: Path,
) -> dict[str, Any]:
    """Grade a run against a validated task using the submission snapshot.

    Inconsistent scoring configuration raises ValueError. Invalid or missing
    runtime evidence is recorded in the report with the affected scores unset.
    """
    validate_scoring_config(task)
    if task["task_id"] != run_meta["task_id"] or run_meta["schema_version"] != 1:
        raise ValueError("Task and run metadata do not match")

    mapping_status = orchestration_result["mapping_status"]
    probe_attempts = orchestration_result["probe_attempts"]
    mapping = orchestration_result["mapping_artifact"]
    observations = orchestration_result["observations"]
    error = orchestration_result["error"]
    model_info = orchestration_result["model_info"]
    calls = None
    mapping_id = None
    rule_results = []
    base_score = None
    rows = {}

    if mapping is not None and not _same_run(mapping, run_meta):
        error = _error("contract_error", "mapping", "Mapping belongs to a different run")
        mapping_status = "not_reached"
    elif mapping_status == "verified" and (
        mapping is None or not mapping["mapping_id"] or not task["operations"]
        or mapping["operations"].keys() != task["operations"].keys()
        or mapping["probe_attempts"] != probe_attempts
    ):
        error = _error("contract_error", "mapping", "Verified mapping is missing or inconsistent")
        mapping_status = "not_reached"
    elif mapping is not None:
        if mapping_status == "verified":
            mapping_id = mapping["mapping_id"]
        calls = {
            "documented_operations": mapping["documented_operations"],
            "operations": mapping["operations"] if mapping_id is not None else {},
        }

    if error is None:
        if mapping_status == "not_required" and task["operations"]:
            error = _error("contract_error", "mapping", "Task requires a verified mapping")
        elif mapping_status not in {"verified", "not_required"}:
            code = mapping_status if mapping_status != "not_reached" else "execution_error"
            error = _error(code, "mapping", "No verified mapping is available")
        elif task["runner_id"] is None:
            base_score = 0
        elif observations is None:
            error = _error("invalid_observations", "run_checks", "Missing observations")
        elif (
            not _same_run(observations, run_meta)
            or "mapping_id" not in observations
            or observations["mapping_id"] != mapping_id
        ):
            error = _error("invalid_observations", "run_checks", "Observation metadata does not match")
        else:
            try:
                rows = index_observations(task, observations)
            except ValueError as exc:
                error = _error("invalid_observations", "run_checks", str(exc))
            else:
                if observations["status"] == "execution_error":
                    failure = observations["error"]
                    error = _error("execution_error", failure["stage"], failure["message"])
                else:
                    rule_results = grade_rules(task, rows)
                    base_score = sum(rule["points_awarded"] for rule in rule_results)

    review_artifact = None
    review_error = None
    quality_score = None
    quality_results = []
    hypothesis_results = []
    review_status = "skipped" if base_score is None else review_result["status"]
    if review_status == "ok":
        artifact = review_result["review_artifact"]
        if isinstance(artifact, dict) and _same_run(artifact, run_meta):
            graded_review = review_report(task, artifact, rows, submission_dir)
            if graded_review is not None:
                quality_results, hypothesis_results = graded_review
                if all(item["points_awarded"] is not None for item in quality_results):
                    quality_score = sum(item["points_awarded"] for item in quality_results)
                review_artifact = "ai_review.json"
                model_info = model_info if model_info is not None else artifact["model_info"]
        if review_artifact is None:
            review_status = "invalid_output"
            review_error = _error("review_invalid_output", "review", "Invalid review artifact or run metadata")
    elif review_status in {"invalid_output", "unavailable"}:
        review_error = review_result["error"]

    max_score = task["score_limits"]["functional"]
    quality_max_score = task["score_limits"]["quality"]
    return deepcopy({
        **{field: run_meta[field] for field in _ENVELOPE},
        "run_status": "graded" if base_score is not None else "incomplete",
        "mapping_status": mapping_status,
        "mapping_id": mapping_id,
        "probe_attempts": probe_attempts,
        "calls": calls,
        "base_score": base_score,
        "max_score": max_score,
        "rule_results": rule_results,
        "quality_score": quality_score,
        "quality_max_score": quality_max_score,
        "quality_results": quality_results,
        "total_score": base_score + quality_score if quality_score is not None else None,
        "total_max_score": max_score + quality_max_score,
        "review_status": review_status,
        "review_result": {
            "status": review_status,
            "review_artifact": review_artifact,
            "error": review_error,
        },
        "hypothesis_results": hypothesis_results,
        "model_info": model_info,
        "errors": [item for item in (error, review_error) if item is not None],
    })


def _error(code: str, stage: str, message: str) -> dict:
    return {"code": code, "stage": stage, "message": message}
