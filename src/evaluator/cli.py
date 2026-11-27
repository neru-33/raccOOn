"""Command line entry point: ``python -m evaluator run ...``."""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import ChainMap
from datetime import datetime, timezone
from enum import IntEnum
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from dotenv import dotenv_values
from jsonschema import ValidationError

from evaluator import agent, contracts, core, models, reporting, review, runners, snapshot
from evaluator.runners.compose_postgres_k6 import RunnerConfigError


class ExitCode(IntEnum):
    OK = 0
    """Observation and grading completed (functional or review failures included)."""
    CONTRACT_ERROR = 2
    """Task or input contract error."""
    INCOMPLETE = 3
    """Grading not completed (mapping unresolved, execution error, ...)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluator")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="evaluate one submission")
    run.add_argument("--task", required=True, type=Path, help="path to task.json")
    run.add_argument("--submission", required=True, type=Path, help="submission directory")
    run.add_argument("--out", required=True, type=Path, help="new output directory")
    run.add_argument("--review", choices=["live", "off"], default="off")
    return parser


def _check_inputs(args: argparse.Namespace) -> str | None:
    if not args.task.is_file():
        return f"task file not found: {args.task}"
    if not args.submission.is_dir():
        return f"submission directory not found: {args.submission}"
    if args.out.exists():
        return f"output path already exists: {args.out}"
    return None


def _load_task(path: Path) -> dict:
    task = json.loads(path.read_text(encoding="utf-8-sig"))
    contracts.validate("task.schema.json", task)
    core.validate_scoring_config(task)
    return task


def _result(status: str, mapping: dict | None = None, observations: dict | None = None,
            model: models.ModelClient | None = None, error: dict | None = None) -> dict:
    return {
        "mapping_status": status,
        "probe_attempts": mapping["probe_attempts"] if mapping is not None else 0,
        "mapping_artifact": mapping,
        "observations": observations,
        "model_info": model.model_info if model is not None else None,
        "error": error,
    }


def _error(code: str, stage: str, message: str) -> dict:
    return {"code": code, "stage": stage, "message": message}


def _load_model() -> models.ModelClient:
    values = {
        key: value for key, value in dotenv_values(".env").items()
        if key.startswith("EVALUATOR_") and value is not None
    }
    return models.load_client(ChainMap(os.environ, values))


def _run_unmapped(session: runners.RunnerSession) -> dict:
    try:
        start = session.start()
        if start["status"] == "ready":
            return _result("not_required", observations=session.run_checks(None))
        detail = start["error"]
        code = "submission_start_failed" if detail["cause"] == "submission" else "execution_error"
        error = _error(code, detail["stage"], detail["message"])
        if detail.get("log_excerpt") is not None:
            error["log_excerpt"] = detail["log_excerpt"]
    except Exception:
        error = _error("execution_error", "run_checks", "Runner failed before returning observations")
    return _result("not_required", error=error)


def _run_agent(task: dict, snapshot_dir: Path, session: runners.RunnerSession,
               model: models.ModelClient) -> dict:
    try:
        return agent.orchestrate(task, snapshot_dir, session, model)
    except Exception:
        try:
            mapping = session.mapping_artifact()
        except Exception:
            mapping = None
        status = "verified" if mapping and mapping["mapping_id"] else "agent_unavailable"
        error = _error("agent_unavailable", "agent", "Evaluation agent failed before returning its result")
        return _result(status, mapping=mapping, model=model, error=error)


def _run_review(task: dict, snapshot_dir: Path, result: dict, model: models.ModelClient | None) -> dict:
    try:
        if model is None:
            model = _load_model()
        return review.review(task, snapshot_dir, result["observations"], result["mapping_artifact"], "live", model)
    except Exception:
        error = _error("review_unavailable", "review", "Quality review failed before returning its result")
        return {"status": "unavailable", "review_artifact": None, "error": error}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def _write_report(report: dict, output: Path) -> None:
    try:
        reporting.write(report, output)
    except NotImplementedError:
        # Keep the CLI usable until the Markdown renderer lands.
        _write_json(output / "report.json", report)
        lines = ["# Evaluation report", "", f"- Run: `{report['run_status']}`",
                 f"- Mapping: `{report['mapping_status']}`", f"- Review: `{report['review_status']}`"]
        for label, score, maximum in (("Functional", "base_score", "max_score"),
                                      ("Quality", "quality_score", "quality_max_score"),
                                      ("Total", "total_score", "total_max_score")):
            value = report[score]
            lines.append(f"- {label}: {value}/{report[maximum]}" if value is not None else f"- {label}: pending")
        lines.extend(f"- Error: `{error['code']}`" for error in report["errors"])
        lines.extend(["", "[Full report](report.json)", ""])
        (output / "report.md").write_text("\n".join(lines), encoding="utf-8")


def _evaluate_run(args: argparse.Namespace, task: dict, snapshot_dir: Path, run_meta: dict) -> ExitCode:
    model = None
    cleanup_error = None
    if task["runner_id"] is None:
        result = _result("not_required")
    else:
        try:
            session = runners.open_session(task, args.task.resolve().parent, snapshot_dir, run_meta)
        except (TypeError, KeyError, FileNotFoundError) as exc:
            raise RunnerConfigError("Invalid runner configuration or missing fixture") from exc
        try:
            if task["operations"]:
                try:
                    model = _load_model()
                except Exception:
                    result = _result("agent_unavailable", error=_error(
                        "agent_unavailable", "model", "Could not initialize the evaluation model",
                    ))
                else:
                    result = _run_agent(task, snapshot_dir, session, model)
            else:
                result = _run_unmapped(session)
        finally:
            try:
                session.close()
            except Exception:
                cleanup_error = _error("execution_error", "cleanup", "Runner could not clean up its resources")

    review_result = {"status": "off", "review_artifact": None, "error": None}
    report = core.evaluate(task, run_meta, result, review_result, snapshot_dir)
    if report["run_status"] == "graded" and args.review == "live":
        review_result = _run_review(task, snapshot_dir, result, model)
        report = core.evaluate(task, run_meta, result, review_result, snapshot_dir)
    if cleanup_error is not None:
        report["errors"].append(cleanup_error)

    for field, name in (("mapping_artifact", "mapping.json"), ("observations", "observations.json")):
        artifact = result[field]
        if artifact is not None and all(artifact.get(key) == value for key, value in run_meta.items()):
            _write_json(args.out / name, artifact)
    if report["review_status"] == "ok":
        _write_json(args.out / "ai_review.json", review_result["review_artifact"])
    _write_report(report, args.out)
    return ExitCode.OK if report["run_status"] == "graded" else ExitCode.INCOMPLETE


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    problem = _check_inputs(args)
    if problem:
        print(f"error: {problem}", file=sys.stderr)
        return ExitCode.CONTRACT_ERROR
    try:
        task = _load_task(args.task)
        args.out.mkdir(parents=True)
    except (OSError, ValueError, ValidationError) as exc:
        message = exc.message if isinstance(exc, ValidationError) else str(exc)
        print(f"error: {message}", file=sys.stderr)
        return ExitCode.CONTRACT_ERROR

    try:
        with TemporaryDirectory(prefix="raccoon-evaluation-") as work_dir:
            snapshot_dir, submission_sha256 = snapshot.create_snapshot(args.submission, Path(work_dir))
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            run_meta = {
                "schema_version": task["schema_version"],
                "run_id": f"run-{stamp}-{uuid4().hex}",
                "task_id": task["task_id"],
                "submission_sha256": submission_sha256,
            }
            return _evaluate_run(args, task, snapshot_dir, run_meta)
    except (RunnerConfigError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return ExitCode.CONTRACT_ERROR
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return ExitCode.INCOMPLETE
