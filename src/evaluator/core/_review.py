"""Check review references and translate rubric ratings into points."""

from __future__ import annotations

from collections.abc import Callable
from functools import cache
from pathlib import Path

from evaluator.snapshot import source_line_count

from ._scoring import Rows, _METRIC_NAME, _TEST_ID, verifies_feature

_RATINGS = {"needs_improvement", "meets", "strong", "insufficient_evidence", "not_applicable"}
_FINDING_FIELDS = {"criterion_id", "rating", "reason", "source_refs", "observation_refs", "feature_summary"}


def _source_path(path: str) -> bool:
    return bool(path) and not path.startswith("/") and "\\" not in path and ".." not in path.split("/")


def _file_ref(ref: object) -> bool:
    return (
        isinstance(ref, dict) and ref.keys() == {"path", "start_line", "end_line"}
        and isinstance(ref["path"], str) and _source_path(ref["path"])
        and all(type(ref[key]) is int and ref[key] >= 1 for key in ("start_line", "end_line"))
    )


def _observation_ref(ref: object) -> bool:
    return (
        isinstance(ref, dict) and ref.keys() == {"test_id", "round", "metric"}
        and isinstance(ref["test_id"], str) and bool(_TEST_ID.fullmatch(ref["test_id"]))
        and isinstance(ref["metric"], str) and bool(_METRIC_NAME.fullmatch(ref["metric"]))
        and type(ref["round"]) is int and ref["round"] >= 1
    )


def _valid_finding(finding: object) -> bool:
    return (
        isinstance(finding, dict) and finding.keys() == _FINDING_FIELDS
        and all(isinstance(finding[key], str) for key in ("criterion_id", "rating", "reason"))
        and finding["rating"] in _RATINGS
        and all(isinstance(finding[key], list) for key in ("source_refs", "observation_refs"))
        and (finding["feature_summary"] is None or isinstance(finding["feature_summary"], str))
        and all(_file_ref(ref) for ref in finding["source_refs"])
        and all(_observation_ref(ref) for ref in finding["observation_refs"])
    )


def _valid_hypothesis(hypothesis: object) -> bool:
    return (
        isinstance(hypothesis, dict) and hypothesis.keys() == {"id", "text", "source_ref"}
        and all(isinstance(hypothesis[key], str) for key in ("id", "text"))
        and _file_ref(hypothesis["source_ref"])
    )


def _valid_review(artifact: dict) -> bool:
    return (
        artifact.keys() == {"schema_version", "run_id", "task_id", "submission_sha256",
                            "model_info", "quality_findings", "hypotheses"}
        and isinstance(model := artifact["model_info"], dict)
        and model.keys() == {"client", "model", "endpoint_host"}
        and all(isinstance(model[key], str) and model[key] for key in ("client", "model"))
        and (model["endpoint_host"] is None or isinstance(model["endpoint_host"], str))
        and isinstance(artifact["quality_findings"], list)
        and isinstance(artifact["hypotheses"], list)
        and all(_valid_finding(finding) for finding in artifact["quality_findings"])
        and all(_valid_hypothesis(hypothesis) for hypothesis in artifact["hypotheses"])
    )


def _source_issue(ref: dict, line_count: Callable[[str], int | None]) -> str | None:
    path, start, end = ref["path"], ref["start_line"], ref["end_line"]
    lines = line_count(path)
    if lines is None:
        return f"{path}: 스냅샷에서 읽을 수 있는 파일이 없다"
    if start > end:
        return f"{path}: start_line {start}가 end_line {end}를 넘는다"
    if end > lines:
        return f"{path}: end_line {end}가 파일 줄 수 {lines}을 넘는다"
    return None


def _grade_finding(criterion: dict, finding: dict, rows: Rows, line_count: Callable[[str], int | None]) -> dict:
    rating = finding.get("rating")
    refs = finding.get("source_refs", [])
    issues = [issue for ref in refs if (issue := _source_issue(ref, line_count)) is not None]
    evidence = []
    for ref in finding.get("observation_refs", []):
        test_id, round_number, metric = ref["test_id"], ref["round"], ref["metric"]
        row = rows.get((test_id, round_number))
        if row is None or metric not in row["metrics"]:
            issues.append(f"{test_id} {round_number}회차: {metric} 관측값이 없다")
        else:
            evidence.append({"ref": ref, "observed_value": row["metrics"][metric]})

    points_by_rating = criterion["points_by_rating"]
    if rating is None:
        issues.append("품질 판정이 누락되었다")
    elif rating == "insufficient_evidence":
        issues.append("리뷰가 근거 부족으로 판정했다")
    elif rating not in points_by_rating:
        issues.append(f"과제에서 허용하지 않은 판정이다: {rating}")
    elif rating != "not_applicable" and not refs:
        issues.append("코드 또는 문서 위치가 누락되었다")

    verification = None
    if issues:
        status = "insufficient_evidence"
    elif rating == "not_applicable":
        status = "not_applicable"
    else:
        status = "accepted"
        if criterion["kind"] == "extra_feature":
            verification = "runtime_verified" if verifies_feature(criterion, rows) else "static_only"

    return {
        "criterion_id": criterion["id"],
        "status": status,
        "rating": rating,
        "points_awarded": None if issues else points_by_rating[rating],
        "max_points": criterion["max_points"],
        "reason": finding.get("reason"),
        "source_refs": refs,
        "observation_evidence": evidence,
        "feature_summary": finding.get("feature_summary") if criterion["kind"] == "extra_feature" else None,
        "verification_level": verification,
        "issues": issues,
    }


def review_report(task: dict, artifact: dict, rows: Rows, snapshot: Path) -> tuple[list, list] | None:
    if not _valid_review(artifact):
        return None

    findings = {finding["criterion_id"]: finding for finding in artifact["quality_findings"]}
    criteria = task["quality_rubric"]
    if len(findings) != len(artifact["quality_findings"]) or findings.keys() - {item["id"] for item in criteria}:
        return None

    @cache
    def line_count(path: str) -> int | None:
        return source_line_count(snapshot, path)

    quality = [_grade_finding(criterion, findings.get(criterion["id"], {}), rows, line_count) for criterion in criteria]
    hypotheses = []
    for hypothesis in artifact["hypotheses"]:
        ref = hypothesis["source_ref"]
        issue = _source_issue(ref, line_count)
        hypotheses.append({
            "hypothesis_id": hypothesis["id"],
            "status": "hypothesis" if issue is None else "insufficient_evidence",
            "text": hypothesis["text"],
            "source_ref": ref,
            "reason": issue,
        })
    return quality, hypotheses
