"""Submission snapshot: copy, snapshot ID, and the ``read_source`` tool.

Every module that reads submission files (agent, review, core) works on the
snapshot created here, never on the original submission directory.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_snapshot(submission_dir: Path, work_dir: Path) -> tuple[Path, str]:
    """Copy the submission into ``work_dir`` and return (snapshot_dir, submission_sha256).

    Excludes ``.git/``, symbolic links, ``.env`` and ``.env.*``. The snapshot ID
    follows the rule in ``contracts/README.md``.
    """
    raise NotImplementedError


def read_source(snapshot_dir: Path, path: str) -> dict[str, Any]:
    """Implementation of the ``read_source`` model tool.

    Returns ``tools.schema.json#/$defs/read_source_result``, or a ``tool_error``
    with ``path_denied`` (absolute path, ``..``, excluded or secret file) or
    ``not_found``. Large files are cut and marked ``truncated``.
    """
    raise NotImplementedError
