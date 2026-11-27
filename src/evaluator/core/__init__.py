"""Grading core: validates artifacts, applies rules, checks review evidence.

Standard library only. No external process or model calls.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def evaluate(
    task: dict[str, Any],
    run_meta: dict[str, Any],
    orchestration_result: dict[str, Any],
    review_result: dict[str, Any],
    submission_dir: Path,
) -> dict[str, Any]:
    """Returns a ``report.json`` object."""
    raise NotImplementedError
