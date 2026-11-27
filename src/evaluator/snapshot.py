"""Submission snapshot: copy, snapshot ID, and the read-only model tools.

Every module that reads submission files (agent, review, core) works on the
snapshot created here, never on the original submission directory. The read
tools (``list_files``, ``search_code``, ``read_source``) are shared by the agent
and review sessions; their results follow ``contracts/schemas/tools.schema.json``.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from collections.abc import Iterator
from pathlib import Path, PureWindowsPath
from typing import Any

_LIST_LIMIT = 2_000
_SEARCH_LIMIT = 100
_READ_LIMIT = 400
_TRACKED_FILE = "tracked-files.json"


class _PathDenied(ValueError):
    pass


def _excluded(name: str) -> bool:
    name = name.lower()
    return name in {".git", ".env"} or name.startswith(".env.")


def _is_link(info: os.stat_result) -> bool:
    return stat.S_ISLNK(info.st_mode) or bool(
        getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
    )


def _raise_walk_error(error: OSError) -> None:
    raise error


def _walk_files(root: Path) -> Iterator[Path]:
    for directory, subdirs, files in os.walk(root, onerror=_raise_walk_error):
        parent = Path(directory)
        subdirs[:] = [name for name in subdirs if not _excluded(name) and not _is_link((parent / name).lstat())]
        for name in files:
            if not _excluded(name):
                path = parent / name
                info = path.lstat()
                if stat.S_ISREG(info.st_mode) and not _is_link(info):
                    yield path


def _tracked_paths(submission: Path) -> list[str] | None:
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-c", "core.fsmonitor=false", "-C", str(submission),
             "ls-files", "-z", "--cached", "--", "."],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return None
    return sorted({os.fsdecode(path) for path in result.stdout.split(b"\0") if path})


def create_snapshot(submission_dir: Path, work_dir: Path) -> tuple[Path, str]:
    """Copy the submission into ``work_dir`` and return (snapshot_dir, submission_sha256).

    Excludes ``.git/``, symbolic links, ``.env`` and ``.env.*``. The snapshot ID
    follows the rule in ``contracts/README.md``. If the submission is a Git
    working tree, record the tracked file list so ``list_files`` can use it.
    """
    submission = submission_dir.resolve(strict=True)
    if not submission.is_dir():
        raise NotADirectoryError(submission)
    work_dir = work_dir.resolve()
    if work_dir.is_relative_to(submission):
        raise ValueError("Snapshot workspace must be outside the submission directory")
    work_dir.mkdir(parents=True, exist_ok=True)
    tracked = _tracked_paths(submission)
    workspace = Path(tempfile.mkdtemp(prefix="snapshot-", dir=work_dir))
    snapshot = workspace / "submission"
    try:
        snapshot.mkdir()
        hashes = {}
        for source in _walk_files(submission):
            relative = source.relative_to(submission)
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            if _is_link(target.lstat()):
                raise OSError("Submission changed while copying")
            if os.name != "nt":
                target.chmod(0o644 | (source.stat().st_mode & 0o111))
            with target.open("rb") as copied:
                hashes[relative.as_posix().encode("utf-8")] = hashlib.file_digest(copied, "sha256").hexdigest()

        digest = hashlib.sha256()
        for path, file_hash in sorted(hashes.items()):
            digest.update(path + b"\t" + file_hash.encode("ascii") + b"\n")
        if tracked is not None:
            (workspace / _TRACKED_FILE).write_text(json.dumps(tracked), encoding="utf-8")
    except BaseException:
        if workspace.resolve().parent == work_dir:
            shutil.rmtree(workspace, ignore_errors=True)
        raise
    return snapshot, digest.hexdigest()


def _windows_reserved(part: str) -> bool:
    if hasattr(os.path, "isreserved"):
        return os.path.isreserved(part)
    return part.endswith((".", " ")) or PureWindowsPath(part).is_reserved()


def _validate_path(path: str) -> None:
    if path.startswith("/") or any(char in path for char in ("\\", ":", "\0")):
        raise _PathDenied
    parts = path.lower().split("/")
    if any(
        part == ".." or _excluded(part) or part.endswith((".key", ".pem"))
        or (os.name == "nt" and part not in {"", "."} and _windows_reserved(part))
        for part in parts
    ):
        raise _PathDenied
    if "/data/private/" in f"/{'/'.join(parts).rstrip('/')}/":
        raise _PathDenied


def _source_file(snapshot: Path, path: str) -> Path:
    _validate_path(path)
    root = snapshot.resolve(strict=True)
    file = root
    for part in Path(path).parts:
        file /= part
        if _is_link(file.lstat()):
            raise _PathDenied
    file = file.resolve(strict=True)
    if not file.is_relative_to(root):
        raise _PathDenied
    _validate_path(file.relative_to(root).as_posix())
    if not file.is_file():
        raise FileNotFoundError
    return file


def _text_lines(file: Path) -> Iterator[str]:
    with file.open(encoding="utf-8-sig") as source:
        for line in source:
            if "\0" in line:
                raise UnicodeError("Binary file")
            yield line.removesuffix("\n")


def _count_lines(file: Path) -> int | None:
    try:
        return sum(1 for _ in _text_lines(file))
    except UnicodeError:
        return None


def source_line_count(snapshot_dir: Path, path: str) -> int | None:
    """Return a source file's line count using the same rules as read_source."""
    try:
        return _count_lines(_source_file(snapshot_dir, path))
    except (OSError, ValueError):
        return None


def _tool_error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


def _snapshot_paths(snapshot: Path) -> list[str]:
    return sorted(file.relative_to(snapshot).as_posix() for file in _walk_files(snapshot))


def _file_index(snapshot: Path) -> tuple[str, list[str]]:
    metadata = snapshot.parent / _TRACKED_FILE
    if snapshot.name == "submission" and snapshot.parent.name.startswith("snapshot-") and metadata.is_file():
        return "git", json.loads(metadata.read_text(encoding="utf-8"))
    return "walk", _snapshot_paths(snapshot)


def list_files(snapshot_dir: Path, path_prefix: str | None = None) -> dict[str, Any]:
    """``list_files`` tool. Git-tracked files when available, otherwise a directory walk."""
    if path_prefix is not None and not isinstance(path_prefix, str):
        return _tool_error("invalid_arguments", "path_prefix must be a string")
    prefix = path_prefix or ""
    try:
        _validate_path(prefix)
        source, paths = _file_index(snapshot_dir)
        files = []
        for path in paths:
            if not path.startswith(prefix):
                continue
            try:
                file = _source_file(snapshot_dir, path)
            except (_PathDenied, FileNotFoundError, NotADirectoryError):
                continue
            if len(files) == _LIST_LIMIT:
                return {"source": source, "files": files, "truncated": True}
            files.append({"path": path, "lines": _count_lines(file)})
        return {"source": source, "files": files, "truncated": False}
    except _PathDenied:
        return _tool_error("path_denied", "Path is outside the readable snapshot")
    except (OSError, ValueError):
        return _tool_error("execution_error", "Could not list snapshot files")


def search_code(snapshot_dir: Path, query: str, path_prefix: str | None = None) -> dict[str, Any]:
    """``search_code`` tool. Case-insensitive fixed-string search over text files."""
    if not isinstance(query, str) or not query or (path_prefix is not None and not isinstance(path_prefix, str)):
        return _tool_error("invalid_arguments", "query must be non-empty and path_prefix must be a string if supplied")
    prefix = path_prefix or ""
    needle = query.casefold()
    try:
        _validate_path(prefix)
        matches = []
        for path in _snapshot_paths(snapshot_dir):
            if not path.startswith(prefix):
                continue
            found = []
            try:
                file = _source_file(snapshot_dir, path)
                for number, line in enumerate(_text_lines(file), 1):
                    if len(matches) + len(found) <= _SEARCH_LIMIT and needle in line.casefold():
                        found.append({"path": path, "line": number, "text": line[:300]})
            except (_PathDenied, FileNotFoundError, NotADirectoryError, UnicodeError):
                continue
            matches.extend(found)
            if len(matches) > _SEARCH_LIMIT:
                return {"matches": matches[:_SEARCH_LIMIT], "truncated": True}
        return {"matches": matches, "truncated": False}
    except _PathDenied:
        return _tool_error("path_denied", "Path is outside the readable snapshot")
    except OSError:
        return _tool_error("execution_error", "Could not search snapshot files")


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
    if not isinstance(path, str) or not path or any(
        bound is not None and (type(bound) is not int or bound < 1) for bound in (start_line, end_line)
    ):
        return _tool_error("invalid_arguments", "Provide a path and positive integer line bounds")
    start = start_line if start_line is not None else 1
    if end_line is not None and end_line < start:
        return _tool_error("invalid_arguments", "end_line must be at least start_line")
    limit = start + _READ_LIMIT - 1
    stop = min(end_line, limit) if end_line is not None else limit
    try:
        file = _source_file(snapshot_dir, path)
        lines = []
        total = 0
        truncated = False
        for total, line in enumerate(_text_lines(file), 1):
            if start <= total <= stop:
                lines.append(f"{total}\t{line[:2_000]}")
                truncated |= len(line) > 2_000
        if start > max(total, 1):
            return _tool_error("invalid_arguments", "start_line is past the end of the file")
        requested_end = end_line if end_line is not None else total
        return {
            "path": file.relative_to(snapshot_dir.resolve()).as_posix(),
            "start_line": start,
            "end_line": min(stop, total),
            "content": "\n".join(lines),
            "total_lines": total,
            "truncated": truncated or requested_end > limit,
        }
    except _PathDenied:
        return _tool_error("path_denied", "Path is outside the readable snapshot")
    except (FileNotFoundError, NotADirectoryError, UnicodeError):
        return _tool_error("not_found", "No readable text file at that path")
    except OSError:
        return _tool_error("execution_error", "Could not read snapshot file")
