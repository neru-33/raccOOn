"""Presentation for interactive evaluation runs."""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from time import monotonic
from typing import TextIO

from rich.cells import cell_len
from rich.console import Console
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn


@dataclass
class Stage:
    failed: bool = False


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
            state.failed = True
            raise
        finally:
            with suppress(OSError, UnicodeError):
                progress.stop()
            status = "FAIL" if state.failed else "DONE"
            self._print(f"{status:4}  {label}  ({monotonic() - start:.1f}s)",
                        style="red" if state.failed else "green")

    def summary(self, report: dict, output: Path) -> None:
        if self._console is None:
            return
        graded = report["run_status"] == "graded"
        self._print("")
        self._print("Evaluation complete" if graded else "Evaluation incomplete",
                    style="bold green" if graded else "bold yellow")
        scores = []
        for label, key, limit in (("Functional", "base_score", "max_score"),
                                  ("Quality", "quality_score", "quality_max_score"),
                                  ("Total", "total_score", "total_max_score")):
            value = report[key]
            scores.append(f"{label}: {value}/{report[limit]}" if value is not None else f"{label}: pending")
        self._print("  |  ".join(scores))
        if report["review_status"] != "ok":
            self._print(f"Review: {report['review_status']}", style="dim")
        for error in report["errors"]:
            self._print(f"{error['stage']}: {error['code']}", style="yellow")
        self._print(f"Report: {output / 'report.md'}", soft_wrap=True)
