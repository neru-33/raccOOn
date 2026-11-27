"""Independent AI quality review."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from evaluator.models import ModelClient

# Session budget. Not part of the stable contract; adjust as needed.
MAX_TURNS = 20
"""Model responses per review session, including the final structured answer."""
MAX_TOOL_CALLS = 40
"""Read-tool calls (``list_files``, ``search_code``, ``read_source``) per review session."""
TIME_LIMIT_SECONDS = 600
"""Wall-clock limit for the review session."""


def review(
    task: dict[str, Any],
    submission_dir: Path,
    observations: dict[str, Any],
    mapping_artifact: dict[str, Any],
    mode: Literal["live", "off"],
    model: ModelClient | None,
) -> dict[str, Any]:
    """Returns ``runtime.schema.json#/$defs/review_result``.

    The model explores the snapshot with the read tools and then returns the
    structured answer. When a budget runs out, the host asks once more for the
    final answer without tools; if that fails, the status is ``unavailable``.
    """
    raise NotImplementedError
