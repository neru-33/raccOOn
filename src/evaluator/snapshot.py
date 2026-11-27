"""Submission snapshot: copy, snapshot ID, and the read-only model tools.

Every module that reads submission files (agent, review, core) works on the
snapshot created here, never on the original submission directory. The read
tools (``list_files``, ``search_code``, ``read_source``) are shared by the agent
and review sessions; their results follow ``contracts/schemas/tools.schema.json``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def create_snapshot(submission_dir: Path, work_dir: Path) -> tuple[Path, str]:
    """Copy the submission into ``work_dir`` and return (snapshot_dir, submission_sha256).

    Excludes ``.git/``, symbolic links, ``.env`` and ``.env.*``. The snapshot ID
    follows the rule in ``contracts/README.md``. If the submission is a Git
    working tree, record the tracked file list so ``list_files`` can use it.
    """
    raise NotImplementedError


def list_files(snapshot_dir: Path, path_prefix: str | None = None) -> dict[str, Any]:
    """``list_files`` tool. Git-tracked files when available, otherwise a directory walk."""
    raise NotImplementedError


def search_code(snapshot_dir: Path, query: str, path_prefix: str | None = None) -> dict[str, Any]:
    """``search_code`` tool. Case-insensitive fixed-string search over text files."""
    raise NotImplementedError


def read_source(
    snapshot_dir: Path,
    path: str,
    start_line: int | None = None,
    end_line: int | None = None,
) -> dict[str, Any]:
    """``read_source`` tool.

    Returns ``read_source_result`` with each line as ``"<line number>\\t<text>"``
    (1-based, original file numbering), at most 400 lines per call, or a
    ``tool_error`` with ``path_denied`` (absolute path, ``..``, excluded or
    secret file) or ``not_found``.
    """
    raise NotImplementedError
