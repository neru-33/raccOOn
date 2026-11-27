"""Presentation for interactive evaluation runs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from time import monotonic
from typing import Literal, TextIO

from rich.cells import cell_len
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


@dataclass
class Stage:
    status: Literal["DONE", "FAIL", "SKIP"] = "DONE"


class TerminalDisplay:
    def __init__(self, stream: TextIO) -> None:
        self._console: Console | None = None
        if stream.isatty() and not os.environ.get("CI"):
            console = Console(file=stream, markup=False, highlight=False)
            if not console.is_dumb_terminal:
                self._console = console

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
    def stage(self, label: str) -> Iterator[Stage]:
        state = Stage()
        if self._console is None:
            yield state
            return

        start = monotonic()
        progress = Progress(
            SpinnerColumn("line", style="cyan"),
            TextColumn("{task.description}"),
            TimeElapsedColumn(),
            console=self._console,
            transient=True,
            refresh_per_second=8,
            redirect_stdout=False,
            redirect_stderr=False,
        )
        progress.add_task(label, total=None)
        try:
            with suppress(OSError, UnicodeError):
                progress.start()
            yield state
        except BaseException:
            state.status = "FAIL"
            raise
        finally:
            with suppress(OSError, UnicodeError):
                progress.stop()
            style = {"DONE": "green", "FAIL": "red", "SKIP": "yellow"}[state.status]
            self._print(f"{state.status:4}  {label}  ({monotonic() - start:.1f}s)", style=style)

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
