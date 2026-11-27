"""Evaluation agent: discovers the submission's API and drives the runner tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from evaluator.models import ModelClient
from evaluator.runners import RunnerSession

# Session budget. Not part of the stable contract; adjust as needed.
MAX_TURNS = 20
"""Model responses per session. One response counts as one turn even if it holds several tool calls."""
MAX_PROBES = 2
"""``probe_mapping`` calls per session. Calls rejected by argument validation do not count."""
TIME_LIMIT_SECONDS = 600
"""Wall-clock limit for model and read-tool work. Time spent inside ``run_checks`` is excluded."""


def orchestrate(
    task: dict[str, Any],
    submission_dir: Path,
    tools: RunnerSession,
    model: ModelClient,
) -> dict[str, Any]:
    """Run one agent session. Returns ``runtime.schema.json#/$defs/orchestration_result``.

    Session rules:
    - Tool calls in one response run one at a time, in order. Invalid arguments
      return ``invalid_arguments`` and use a turn but not a probe.
    - The session ends when ``run_checks`` returns.
    - If a mapping is verified but the model stops, or the turn or time budget
      runs out, before calling ``run_checks``, the host calls ``run_checks``
      with the verified ``mapping_id`` itself.
    - If no mapping is verified when the session ends, the result is
      ``mapping_unresolved``. A failed model call is ``agent_unavailable``.
    """
    raise NotImplementedError
