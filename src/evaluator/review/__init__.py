"""Independent AI quality review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from evaluator.models import ModelClient


def review(
    task: dict[str, Any],
    submission_dir: Path,
    observations: dict[str, Any],
    mapping_artifact: dict[str, Any],
    mode: Literal["live", "off"],
    model: ModelClient | None,
) -> dict[str, Any]:
    """Returns ``runtime.schema.json#/$defs/review_result``."""
    raise NotImplementedError
