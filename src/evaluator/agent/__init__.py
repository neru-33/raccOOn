"""Evaluation agent: discovers the submission's API and drives the runner tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evaluator.models import ModelClient
from evaluator.runners import RunnerSession


def orchestrate(
    task: dict[str, Any],
    submission_dir: Path,
    tools: RunnerSession,
    model: ModelClient,
) -> dict[str, Any]:
    """Run one agent session. Returns ``runtime.schema.json#/$defs/orchestration_result``."""
    raise NotImplementedError
