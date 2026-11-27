"""Runner interface: starts the submission and records observations.

Return values follow ``contracts/schemas/tools.schema.json`` (start, probe) and
``observations.schema.json`` / ``mapping.schema.json``. The ``read_source`` tool
is not part of the runner; see ``evaluator.snapshot.read_source``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol


class RunnerSession(Protocol):
    def start(self) -> dict[str, Any]:
        """Bring up the submission and its dependencies. Returns ``start_submission_result``.

        The submission's Compose file holds only the app service. The runner
        adds the dependencies (e.g. the database) with its own override file and
        injects the variables declared in ``runner_config.app.env``.
        Check the runner's own infrastructure (containers, database reachable by
        the runner) first, then wait until ``GET runner_config.app.health_path``
        returns 200 within ``ready_timeout_seconds``. Set ``error.cause`` to
        ``environment`` or ``submission`` accordingly.
        """
        ...

    def probe_mapping(
        self,
        candidate: dict[str, Any],
        documented_operations: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Validate and probe a mapping candidate. Returns ``probe_mapping_result``.

        ``documented_operations`` holds calls the agent read from the submission's
        documents; the runner records them in ``mapping.json`` and derives
        ``probe_status`` from the probe history.
        """
        ...

    def run_checks(self, mapping_id: str | None) -> dict[str, Any]:
        """Reset data and run every required check. Returns an ``observations.json`` object.

        ``mapping_id`` is None only when the task declares no operations.
        """
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
