"""Runner interface: starts the submission and records observations.

Return values follow ``contracts/schemas/tools.schema.json`` (start, probe) and
``observations.schema.json`` / ``mapping.schema.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RunnerSession(Protocol):
    def start(self) -> dict[str, Any]:
        """Bring up app, database and tools. Returns ``start_submission_result``."""
        ...

    def probe_mapping(self, candidate: dict[str, Any]) -> dict[str, Any]:
        """Validate and probe a mapping candidate. Returns ``probe_mapping_result``."""
        ...

    def run_checks(self, mapping_id: str) -> dict[str, Any]:
        """Reset data and run every required check. Returns an ``observations.json`` object."""
        ...

    def mapping_artifact(self) -> dict[str, Any] | None:
        """``mapping.json`` object, or None if no probe was started."""
        ...

    def close(self) -> None:
        """Remove every resource this run created. Must be safe to call more than once."""
        ...


def open_session(
    task: dict[str, Any],
    task_dir: Path,
    snapshot_dir: Path,
    run_meta: dict[str, Any],
) -> RunnerSession:
    """Create the runner registered for ``task['runner_id']``."""
    raise NotImplementedError
