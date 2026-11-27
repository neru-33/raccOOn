"""Validate observation rows and apply task-defined scoring rules."""

from __future__ import annotations

import math
import operator
import re

_COMPARE = {
    "eq": operator.eq,
    "ne": operator.ne,
    "gt": operator.gt,
    "gte": operator.ge,
    "lt": operator.lt,
    "lte": operator.le,
}
_TEST_ID = re.compile(r"[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)*")
_METRIC_NAME = re.compile(r"[a-z][a-z0-9_]*")
Rows = dict[tuple[str, int], dict]


def _matches_type(value: object, kind: str) -> bool:
    if kind == "bool":
        return type(value) is bool
    return type(value) is int or (
        kind == "number" and type(value) is float and math.isfinite(value)
    )


def _rule_rounds(task: dict, rule: dict) -> list[int]:
    declared = task["metrics"][rule["test_id"]]["rounds"]
    return [rule["round"]] if rule["type"] == "award_if" else declared


def validate_scoring_config(task: dict) -> None:
    if task["runner_id"] is None and any(task[key] for key in ("operations", "metrics", "rules")):
        raise ValueError("A task without a runner cannot define operations, metrics, or rules")

    def check_conditions(test_id: str, rounds: list[int], conditions: list[dict]) -> None:
        declaration = task["metrics"].get(test_id)
        if declaration is None or not set(rounds).issubset(declaration["rounds"]):
            raise ValueError(f"Undeclared test or round in scoring rule: {test_id}")
        for condition in conditions:
            kind = declaration["values"].get(condition["metric"])
            comparison_type = "bool" if kind == "bool" else "number"
            if kind is None or not _matches_type(condition["value"], comparison_type):
                raise ValueError(f"Invalid comparison value for {test_id}.{condition['metric']}")
            if kind == "bool" and condition["op"] not in {"eq", "ne"}:
                raise ValueError(f"Invalid boolean comparison for {test_id}.{condition['metric']}")

    functional_max = 0
    for rule in task["rules"]:
        if rule["test_id"] not in task["metrics"]:
            raise ValueError(f"Undeclared test in scoring rule: {rule['test_id']}")
        rounds = _rule_rounds(task, rule)
        check_conditions(rule["test_id"], rounds, rule["conditions"])
        check_conditions(rule["test_id"], rounds, rule.get("veto_if_any_round", []))
        functional_max += rule["points"] * len(rounds)

    quality_max = 0
    for criterion in task["quality_rubric"]:
        if max(criterion["points_by_rating"].values()) > criterion["max_points"]:
            raise ValueError(f"Rating exceeds max_points for {criterion['id']}")
        for rule in criterion.get("verification_rules", []):
            check_conditions(rule["test_id"], [rule["round"]], rule["conditions"])
        quality_max += criterion["max_points"]

    for field, maximum in (("functional", functional_max), ("quality", quality_max)):
        if task["score_limits"][field] != maximum:
            raise ValueError(f"Rules do not match the {field} score limit")
    for entries in (task["rules"], task["quality_rubric"]):
        if len({entry["id"] for entry in entries}) != len(entries):
            raise ValueError("Duplicate scoring IDs")


def index_observations(task: dict, artifact: dict) -> Rows:
    status = artifact.get("status")
    failure = artifact.get("error")
    if status == "execution_error":
        if (not isinstance(failure, dict) or not isinstance(failure.get("stage"), str)
                or not failure["stage"] or not isinstance(failure.get("message"), str)):
            raise ValueError("Execution failure requires a stage and message")
    elif status != "complete" or failure is not None or "error" not in artifact:
        raise ValueError("Invalid observation status or error")

    observations = artifact.get("observations")
    if not isinstance(observations, list):
        raise ValueError("Expected a list of observation rows")

    rows = {}
    for row in observations:
        if not isinstance(row, dict) or row.keys() != {"test_id", "round", "metrics"}:
            raise ValueError("Invalid observation row")
        test_id, round_number, values = row["test_id"], row["round"], row["metrics"]
        if (not isinstance(test_id, str) or not _TEST_ID.fullmatch(test_id)
                or type(round_number) is not int or round_number < 1 or not isinstance(values, dict)):
            raise ValueError("Invalid observation test ID, round, or metrics")
        key = (test_id, round_number)
        if key in rows:
            raise ValueError(f"Duplicate observation for {test_id}, round {round_number}")
        for metric, value in values.items():
            if not isinstance(metric, str) or not _METRIC_NAME.fullmatch(metric) or not (
                type(value) is bool or _matches_type(value, "number")
            ):
                raise ValueError(f"Invalid metric in {test_id}, round {round_number}")
        rows[key] = row

    if status == "complete":
        for test_id, declaration in task["metrics"].items():
            for round_number in declaration["rounds"]:
                row = rows.get((test_id, round_number))
                if row is None:
                    raise ValueError(f"Missing observation for {test_id}, round {round_number}")
                for metric, kind in declaration["values"].items():
                    if not _matches_type(row["metrics"].get(metric), kind):
                        raise ValueError(f"Missing or invalid {metric} for {test_id}, round {round_number}")
    return rows


def _apply_conditions(row: dict, conditions: list[dict], kind: str) -> list[dict]:
    applied = []
    for condition in conditions:
        observed = row["metrics"][condition["metric"]]
        applied.append({
            **condition,
            "kind": kind,
            "round": row["round"],
            "observed": observed,
            "passed": _COMPARE[condition["op"]](observed, condition["value"]),
        })
    return applied


def grade_rules(task: dict, rows: Rows) -> list[dict]:
    results = []
    for rule in task["rules"]:
        evidence = [rows[rule["test_id"], number] for number in _rule_rounds(task, rule)]
        veto = rule.get("veto_if_any_round", [])
        applied = []
        vetoed = False
        points = 0
        if veto:
            for row in evidence:
                conditions = _apply_conditions(row, veto, "veto")
                applied.extend(conditions)
                vetoed |= all(condition["passed"] for condition in conditions)
        if not vetoed:
            for row in evidence:
                conditions = _apply_conditions(row, rule["conditions"], "award")
                applied.extend(conditions)
                if all(condition["passed"] for condition in conditions):
                    points += rule["points"]
        results.append({
            "rule_id": rule["id"],
            "points_awarded": points,
            "points_available": len(evidence) * rule["points"],
            "applied_conditions": applied,
            "evidence": evidence,
        })
    return results


def verifies_feature(criterion: dict, rows: Rows) -> bool:
    for rule in criterion.get("verification_rules", []):
        row = rows[rule["test_id"], rule["round"]]
        if all(item["passed"] for item in _apply_conditions(row, rule["conditions"], "award")):
            return True
    return False
