"""Contract checks: schemas are valid, examples conform and agree with each other."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from evaluator import cli, contracts

EXAMPLES_DIR = contracts.CONTRACTS_DIR / "examples" / "reservation"

SCHEMA_BY_PREFIX = {
    "task": "task.schema.json",
    "mapping": "mapping.schema.json",
    "observations": "observations.schema.json",
    "ai-review": "ai-review.schema.json",
    "report": "report.schema.json",
}


def _registry() -> Registry:
    resources = []
    for name in contracts.schema_names():
        schema = contracts.load_schema(name)
        resources.append((schema["$id"], Resource.from_contents(schema)))
    return Registry().with_resources(resources)


REGISTRY = _registry()


def _validator(schema_name: str) -> Draft202012Validator:
    return Draft202012Validator(contracts.load_schema(schema_name), registry=REGISTRY)


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _examples() -> list[Path]:
    return sorted(EXAMPLES_DIR.glob("*.json"))


def _example(name: str) -> dict:
    return _load(EXAMPLES_DIR / name)


def _variant(path: Path) -> tuple[str, str | None]:
    stem = path.stem
    prefix, _, variant = stem.partition(".")
    return prefix, variant or None


@pytest.mark.parametrize("name", contracts.schema_names())
def test_schema_is_valid(name: str) -> None:
    Draft202012Validator.check_schema(contracts.load_schema(name))


@pytest.mark.parametrize("path", _examples(), ids=lambda p: p.name)
def test_example_conforms(path: Path) -> None:
    prefix, _ = _variant(path)
    errors = sorted(_validator(SCHEMA_BY_PREFIX[prefix]).iter_errors(_load(path)), key=str)
    assert not errors, "\n".join(f"{list(e.absolute_path)}: {e.message}" for e in errors)


def _mutated(name: str, mutate) -> dict:
    doc = _example(name)
    mutate(doc)
    return doc


@pytest.mark.parametrize(
    ("schema_name", "doc"),
    [
        pytest.param(
            "observations.schema.json",
            _mutated(
                "observations.oversell.json",
                lambda d: d["observations"][1]["metrics"].update(persisted_confirmed="2"),
            ),
            id="string-metric-value",
        ),
        pytest.param(
            "observations.schema.json",
            _mutated("observations.oversell.json", lambda d: d.update(status="execution_error")),
            id="execution-error-without-error",
        ),
        pytest.param(
            "task.schema.json",
            _mutated("task.json", lambda d: d["runner_config"]["database"].update(mode="external")),
            id="external-db-without-url-env",
        ),
        pytest.param(
            "report.schema.json",
            _mutated("report.oversell.json", lambda d: d.update(unexpected=True)),
            id="report-unknown-key",
        ),
        pytest.param(
            "mapping.schema.json",
            _mutated(
                "mapping.oversell.json",
                lambda d: d["operations"]["create_reservation"].update(path_template="http://x/a"),
            ),
            id="absolute-url-path",
        ),
    ],
)
def test_invalid_documents_are_rejected(schema_name: str, doc: dict) -> None:
    assert list(_validator(schema_name).iter_errors(doc))


def test_task_without_database_is_accepted() -> None:
    doc = _mutated("task.json", lambda d: d["runner_config"].pop("database"))
    _validator("task.schema.json").validate(doc)


def test_review_only_task_is_accepted() -> None:
    def to_review_only(d: dict) -> None:
        d.update(runner_id=None, runner_config={}, operations={}, metrics={}, rules=[])
        d["score_limits"]["functional"] = 0

    doc = _mutated("task.json", to_review_only)
    _validator("task.schema.json").validate(doc)
    assert sum(_rule_max(r, doc) for r in doc["rules"]) == doc["score_limits"]["functional"]


def test_number_metric_with_threshold_is_accepted() -> None:
    def add_latency(d: dict) -> None:
        d["metrics"]["reservation.concurrent"]["values"]["p95_ms"] = "number"
        d["rules"][2]["conditions"].append({"metric": "p95_ms", "op": "lte", "value": 250.5})

    _validator("task.schema.json").validate(_mutated("task.json", add_latency))


def test_call_without_protocol_is_rejected() -> None:
    doc = _mutated("mapping.correct.json", lambda d: d["operations"]["create_reservation"].pop("protocol"))
    assert list(_validator("mapping.schema.json").iter_errors(doc))


def test_runtime_objects_conform() -> None:
    mapping = _example("mapping.oversell.json")
    observations = _example("observations.oversell.json")
    review_artifact = _example("ai-review.oversell.json")
    runtime_id = contracts.load_schema("runtime.schema.json")["$id"]

    def validate(def_name: str, value: dict) -> None:
        schema = {"$ref": f"{runtime_id}#/$defs/{def_name}"}
        Draft202012Validator(schema, registry=REGISTRY).validate(value)

    envelope = {k: mapping[k] for k in ("schema_version", "run_id", "task_id", "submission_sha256")}
    validate("run_meta", envelope)
    validate(
        "orchestration_result",
        {
            "mapping_status": "verified",
            "probe_attempts": 2,
            "mapping_artifact": mapping,
            "observations": observations,
            "model_info": review_artifact["model_info"],
            "error": None,
        },
    )
    validate("review_result", {"status": "ok", "review_artifact": review_artifact, "error": None})


def test_envelopes_match_within_each_run() -> None:
    by_variant: dict[str, set[tuple]] = {}
    for path in _examples():
        prefix, variant = _variant(path)
        if variant is None:
            continue
        doc = _load(path)
        key = (doc["schema_version"], doc["run_id"], doc["task_id"], doc["submission_sha256"])
        by_variant.setdefault(variant, set()).add(key)
    assert by_variant
    for variant, keys in by_variant.items():
        assert len(keys) == 1, f"{variant}: envelopes differ {keys}"


def _rule_max(rule: dict, task: dict) -> int:
    if rule["type"] == "award_if":
        return rule["points"]
    return rule["points"] * len(task["metrics"][rule["test_id"]]["rounds"])


def test_task_score_limits_match_rules_and_rubric() -> None:
    task = _example("task.json")
    assert sum(_rule_max(r, task) for r in task["rules"]) == task["score_limits"]["functional"]
    assert sum(c["max_points"] for c in task["quality_rubric"]) == task["score_limits"]["quality"]
    for criterion in task["quality_rubric"]:
        assert max(criterion["points_by_rating"].values()) <= criterion["max_points"]


def test_task_rules_reference_declared_metrics() -> None:
    task = _example("task.json")
    for rule in task["rules"]:
        declared = task["metrics"][rule["test_id"]]
        if rule["type"] == "award_if":
            assert rule["round"] in declared["rounds"]
        for cond in rule["conditions"] + rule.get("veto_if_any_round", []):
            assert cond["metric"] in declared["values"]
            if declared["values"][cond["metric"]] == "bool":
                assert cond["op"] in ("eq", "ne")
                assert isinstance(cond["value"], bool)


@pytest.mark.parametrize("variant", ["correct", "oversell"])
def test_complete_observations_cover_declared_metrics(variant: str) -> None:
    task = _example("task.json")
    obs = _example(f"observations.{variant}.json")
    assert obs["status"] == "complete"
    rows = {(o["test_id"], o["round"]): o["metrics"] for o in obs["observations"]}
    assert len(rows) == len(obs["observations"]), "test_id + round must be unique"
    for test_id, decl in task["metrics"].items():
        for rnd in decl["rounds"]:
            metrics = rows[(test_id, rnd)]
            for metric, kind in decl["values"].items():
                value = metrics[metric]
                if kind == "bool":
                    assert isinstance(value, bool)
                elif kind == "int":
                    assert isinstance(value, int) and not isinstance(value, bool)
                else:
                    assert isinstance(value, (int, float)) and not isinstance(value, bool)


@pytest.mark.parametrize("variant", ["correct", "oversell"])
def test_verified_mapping_matches_task_operations(variant: str) -> None:
    task = _example("task.json")
    mapping = _example(f"mapping.{variant}.json")
    assert set(mapping["operations"]) == set(task["operations"])
    for op_name, call in mapping["operations"].items():
        assert set(call["response_fields"]) == set(task["operations"][op_name]["outputs"])
    assert mapping["probe_history"][-1]["mapping_id"] == mapping["mapping_id"]
    assert mapping["probe_attempts"] == len(mapping["probe_history"])
    obs = _example(f"observations.{variant}.json")
    assert obs["mapping_id"] == mapping["mapping_id"]


@pytest.mark.parametrize("path", sorted(EXAMPLES_DIR.glob("report.*.json")), ids=lambda p: p.name)
def test_report_scores_are_consistent(path: Path) -> None:
    task = _example("task.json")
    report = _load(path)
    assert report["max_score"] == task["score_limits"]["functional"]
    assert report["quality_max_score"] == task["score_limits"]["quality"]
    assert report["total_max_score"] == report["max_score"] + report["quality_max_score"]
    assert report["review_status"] == report["review_result"]["status"]

    if report["base_score"] is not None:
        assert report["base_score"] == sum(r["points_awarded"] for r in report["rule_results"])
    if report["quality_score"] is not None:
        assert all(q["status"] != "insufficient_evidence" for q in report["quality_results"])
        assert report["quality_score"] == sum(q["points_awarded"] for q in report["quality_results"])
    if report["base_score"] is None or report["quality_score"] is None:
        assert report["total_score"] is None
    else:
        assert report["total_score"] == report["base_score"] + report["quality_score"]
    if report["run_status"] == "incomplete":
        assert report["base_score"] is None and report["errors"]


def test_review_findings_cite_existing_observations() -> None:
    review = _example("ai-review.oversell.json")
    obs = _example("observations.oversell.json")
    rows = {(o["test_id"], o["round"]): o["metrics"] for o in obs["observations"]}
    for finding in review["quality_findings"]:
        for ref in finding["observation_refs"]:
            assert ref["metric"] in rows[(ref["test_id"], ref["round"])]


def test_report_quality_evidence_matches_observations() -> None:
    report = _example("report.oversell.json")
    obs = _example("observations.oversell.json")
    rows = {(o["test_id"], o["round"]): o["metrics"] for o in obs["observations"]}
    for result in report["quality_results"]:
        for ev in result["observation_evidence"]:
            ref = ev["ref"]
            assert rows[(ref["test_id"], ref["round"])][ref["metric"]] == ev["observed_value"]


def test_review_model_output_example_conforms() -> None:
    review = _example("ai-review.oversell.json")
    output = {k: review[k] for k in ("quality_findings", "hypotheses")}
    Draft202012Validator(contracts.review_output_schema()).validate(output)


def _assert_strict_compatible(node: object, where: str = "$") -> None:
    if isinstance(node, dict):
        if node.get("type") == "object" and "properties" in node:
            assert node.get("additionalProperties") is False, where
            assert set(node.get("required", [])) == set(node["properties"]), where
        for key, value in node.items():
            assert key != "$ref" or str(value).startswith("#"), f"{where}: external $ref"
            _assert_strict_compatible(value, f"{where}.{key}")
    elif isinstance(node, list):
        for i, item in enumerate(node):
            _assert_strict_compatible(item, f"{where}[{i}]")


def test_review_output_schema_is_strict_compatible() -> None:
    _assert_strict_compatible(contracts.review_output_schema())


def test_review_session_has_no_tools() -> None:
    assert contracts.tool_definitions("review") == []


def test_tool_definitions_are_self_contained() -> None:
    defs = contracts.tool_definitions("agent")
    assert [d["function"]["name"] for d in defs] == contracts.tool_names("agent")
    text = json.dumps(defs)
    assert "$ref" not in text and "$defs" not in text
    for d in defs:
        assert d["type"] == "function"
        assert d["function"]["description"]
        assert d["function"]["parameters"]["type"] == "object"


def test_probe_mapping_parameters_accept_example_candidates() -> None:
    (probe,) = [d for d in contracts.tool_definitions("agent") if d["function"]["name"] == "probe_mapping"]
    validator = Draft202012Validator(probe["function"]["parameters"])
    for variant in ("correct", "oversell", "unresolved"):
        for record in _example(f"mapping.{variant}.json")["probe_history"]:
            validator.validate({"candidate": record["candidate"]})


def test_cli_rejects_missing_inputs(tmp_path: Path) -> None:
    code = cli.main(
        [
            "run",
            "--task", str(tmp_path / "missing.json"),
            "--submission", str(tmp_path),
            "--out", str(tmp_path / "out"),
        ]
    )
    assert code == cli.ExitCode.CONTRACT_ERROR


def test_cli_rejects_existing_output_dir(tmp_path: Path) -> None:
    task = tmp_path / "task.json"
    task.write_text("{}", encoding="utf-8")
    out = tmp_path / "out"
    out.mkdir()
    code = cli.main(["run", "--task", str(task), "--submission", str(tmp_path), "--out", str(out)])
    assert code == cli.ExitCode.CONTRACT_ERROR
