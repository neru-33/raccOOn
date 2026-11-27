"""Presentation for interactive evaluation runs."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from time import monotonic
from typing import Any, Literal, TextIO

from rich.cells import cell_len
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn

from evaluator.models import ModelClient, ModelTurn
from evaluator.runners import CheckProgress, RunnerSession


@dataclass
class Stage:
    label: str
    status: Literal["DONE", "FAIL", "SKIP"] = "DONE"
    retain: bool = True


class TerminalDisplay:
    def __init__(self, stream: TextIO) -> None:
        self._console: Console | None = None
        self._update_detail: Callable[[str], None] | None = None
        if stream.isatty() and not os.environ.get("CI"):
            console = Console(file=stream, markup=False, highlight=False)
            if not console.is_dumb_terminal:
                self._console = console

    @property
    def enabled(self) -> bool:
        return self._console is not None

    def _print(self, message: str, **options) -> None:
        if self._console is not None:
            with suppress(OSError, UnicodeError):
                self._console.print(message, **options)

    def header(self, task_id: str, submission: Path) -> None:
        console = self._console
        if console is None:
            return
        logo = "raccOOn"
        with suppress(OSError, UnicodeError):
            path = files("evaluator").joinpath("assets", "raccOOn-cli_ascii_logo.txt")
            art = path.read_text(encoding="utf-8").rstrip("\r\n")
            if art and max(map(cell_len, art.splitlines())) <= console.width:
                art.encode(console.encoding)
                logo = art
        self._print(logo, style="bold magenta", soft_wrap=True)
        self._print(f"Task: {task_id}  |  Submission: {submission.name or submission}")
        self._print("")

    @contextmanager
    def stage(self, label: str, *, retain: bool = True) -> Iterator[Stage]:
        state = Stage(label=label, retain=retain)
        if self._console is None:
            yield state
            return

        start = monotonic()
        progress = Progress(
            SpinnerColumn("line", style="cyan"),
            TextColumn("{task.description}", markup=False),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
            refresh_per_second=8,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        task_id = progress.add_task(label, total=None)
        previous_update = self._update_detail
        self._update_detail = lambda detail: progress.update(task_id, description=f"{label} - {detail}", refresh=True)
        try:
            with suppress(OSError, UnicodeError):
                progress.start()
            yield state
        except BaseException:
            state.status = "FAIL"
            raise
        finally:
            self._update_detail = previous_update
            with suppress(OSError, UnicodeError):
                progress.stop()
            if state.retain:
                style = {"DONE": "green", "FAIL": "red", "SKIP": "yellow"}[state.status]
                self._print(f"{state.status:4}  {state.label}  ({monotonic() - start:.1f}s)", style=style)

    def check_progress(self, check: CheckProgress) -> None:
        if self._update_detail is None:
            return
        detail = f"{check.test_id}, round {check.position}/{check.total}"
        if check.round_id != check.position:
            detail += f" (ID {check.round_id})"
        with suppress(OSError, UnicodeError):
            self._update_detail(detail)

    def summary(self, report: dict, output: Path, *, review_enabled: bool) -> None:
        if self._console is None:
            return

        review_status = report["review_status"]
        if report["run_status"] == "incomplete":
            heading = "Evaluation incomplete"
        elif review_status in {"invalid_output", "unavailable"}:
            heading = "Functional scoring complete - Quality review failed"
        elif not review_enabled:
            heading = "Functional scoring complete"
        elif report["quality_score"] is None:
            heading = "Functional scoring complete - Quality score unavailable"
        else:
            heading = "Evaluation complete"

        self._print("")
        self._print(heading, style="bold green" if report["total_score"] is not None else "bold yellow")
        quality_without_score = "Unavailable"
        if not review_enabled:
            quality_without_score = "Disabled"
        elif review_status == "skipped":
            quality_without_score = "Skipped"
        scores = []
        for label, key, limit, missing in (("Functional", "base_score", "max_score", "Unavailable"),
                                           ("Quality", "quality_score", "quality_max_score", quality_without_score),
                                           ("Total", "total_score", "total_max_score", "Unavailable")):
            value = report[key]
            score = f"{value}/{report[limit]}" if value is not None else missing
            scores.append(f"{label}: {score}")
        self._print("  |  ".join(scores))
        for error in report["errors"]:
            message = " ".join(error["message"].split())
            message = "".join(character for character in message if character.isprintable())
            self._print(f"{error['stage']}: {error['code']} - {message}", style="yellow", soft_wrap=True)
        self._print(f"Report: {output / 'report.md'}", soft_wrap=True)


class TerminalRunner:
    """Show the runner's tool calls without altering their results or order."""

    def __init__(self, runner: RunnerSession, display: TerminalDisplay) -> None:
        self._runner = runner
        self._display = display

    def start(self) -> dict[str, Any]:
        with self._display.stage("Prepare and start submission") as stage:
            result = self._runner.start()
            if not isinstance(result, dict) or result.get("status") != "ready":
                stage.status = "FAIL"
            return result

    def probe_mapping(
        self, candidate: dict[str, Any], documented_operations: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with self._display.stage("Verify API mapping", retain=False) as stage:
            result = self._runner.probe_mapping(candidate, documented_operations)
            attempt = result.get("probe_attempts") if isinstance(result, dict) else None
            if type(attempt) is int and result.get("status") in ("verified", "failed"):
                stage.label += f" (attempt {attempt})"
                stage.retain = True
                if result["status"] == "failed":
                    stage.status = "FAIL"
            return result

    def run_checks(self, mapping_id: str | None) -> dict[str, Any]:
        with self._display.stage("Run submission checks") as stage:
            result = self._runner.run_checks(mapping_id)
            if not isinstance(result, dict) or result.get("status") != "complete":
                stage.status = "FAIL"
            return result

    def mapping_artifact(self) -> dict[str, Any] | None:
        return self._runner.mapping_artifact()

    def close(self) -> None:
        self._runner.close()


class TerminalAgentModel:
    """Show the time spent waiting for the evaluation agent between tool calls."""

    def __init__(self, model: ModelClient, display: TerminalDisplay) -> None:
        self._model = model
        self._display = display

    @property
    def model_info(self) -> dict[str, Any]:
        return self._model.model_info

    def complete(
        self, messages: list[dict[str, Any]], tools: list[dict[str, Any]], response_schema: dict[str, Any] | None,
    ) -> ModelTurn:
        with self._display.stage("Wait for evaluation agent", retain=False):
            return self._model.complete(messages, tools, response_schema=response_schema)
