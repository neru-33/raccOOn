"""Snapshot identity, file access, and reading tool contracts."""

import hashlib
import shutil
import subprocess
import sys

import pytest

from evaluator.snapshot import create_snapshot, list_files, read_source, search_code, source_line_count


def test_snapshot_hashes_the_copied_files_in_path_order(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / ".git").mkdir()
    (source / ".git" / "HEAD").write_bytes(b"ignored")
    (source / ".env").write_bytes(b"ignored")
    (source / ".env.local").write_bytes(b"ignored")
    files = {"한글.txt": b"last", "README.md": b"first\r\n", "asset.bin": b"\0\xff"}
    for name, content in files.items():
        (source / name).write_bytes(content)

    snapshot, digest = create_snapshot(source, tmp_path / "work")

    manifest = b"".join(
        name + b"\t" + hashlib.sha256(content).hexdigest().encode("ascii") + b"\n"
        for name, content in sorted((name.encode("utf-8"), content) for name, content in files.items())
    )
    assert digest == hashlib.sha256(manifest).hexdigest()
    assert {path.name for path in snapshot.iterdir()} == set(files)
    assert (snapshot / "README.md").read_bytes() == b"first\r\n"
    (source / "README.md").write_bytes(b"changed")
    assert read_source(snapshot, "README.md")["content"] == "1\tfirst"


@pytest.mark.skipif(shutil.which("git") is None, reason="Requires Git")
def test_git_listing_uses_tracked_files_from_the_submission_subdirectory(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    source = tmp_path / "source"
    source.mkdir()
    (source / "tracked.txt").write_text("tracked\n", encoding="utf-8")
    (source / "untracked.txt").touch()
    (source / ".env").touch()
    (tmp_path / "outside.txt").touch()
    subprocess.run(["git", "-C", str(tmp_path), "add", "--force", "source/tracked.txt", "source/.env", "outside.txt"], check=True)

    snapshot, _ = create_snapshot(source, tmp_path / "work")

    assert list_files(snapshot) == {
        "source": "git", "files": [{"path": "tracked.txt", "lines": 1}], "truncated": False,
    }
    assert (snapshot / "untracked.txt").is_file()
    assert not (snapshot / ".env").exists()


def test_walk_lists_binary_and_filters_by_prefix(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "text.txt").write_bytes(b"one\r\ntwo\rthree")
    (tmp_path / "src" / "image.bin").write_bytes(b"\0binary")
    (tmp_path / "README.md").touch()

    assert list_files(tmp_path, "src/") == {
        "source": "walk",
        "files": [{"path": "src/image.bin", "lines": None}, {"path": "src/text.txt", "lines": 3}],
        "truncated": False,
    }
    assert source_line_count(tmp_path, "src/text.txt") == 3


def test_search_is_literal_and_capped(tmp_path):
    long_line = "[TeSt]" + "x" * 301
    (tmp_path / "a.txt").write_text((long_line + "\n") * 101, encoding="utf-8")
    (tmp_path / "a.bin").write_bytes(b"[TEST]\n\0")

    result = search_code(tmp_path, "[test]")

    assert result["truncated"] is True
    assert len(result["matches"]) == 100
    assert result["matches"][0] == {"path": "a.txt", "line": 1, "text": long_line[:300]}
    assert result["matches"][-1]["line"] == 100


def test_read_source_preserves_line_numbers_and_caps_output(tmp_path):
    (tmp_path / "source.txt").write_text("first\n" + "x" * 2_001 + "\n" + "line\n" * 420, encoding="utf-8")

    result = read_source(tmp_path, "source.txt", start_line=2)

    assert result["start_line"] == 2
    assert result["end_line"] == 401
    assert result["total_lines"] == 422
    assert result["truncated"] is True
    assert result["content"].splitlines()[0] == "2\t" + "x" * 2_000
    assert result["content"].splitlines()[-1] == "401\tline"
    assert read_source(tmp_path, "source.txt", 3, 4)["content"] == "3\tline\n4\tline"


@pytest.mark.parametrize("path", ["../file", "/tmp/file", "C:/file", "a\\file", ".env", ".env.local", ".git/config", "key.pem"])
def test_read_source_rejects_excluded_and_outside_paths(tmp_path, path):
    assert read_source(tmp_path, path)["error"]["code"] == "path_denied"
    assert source_line_count(tmp_path, path) is None


def test_snapshot_and_reader_skip_linked_directories(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "file.txt").write_text("outside contents", encoding="utf-8")
    link = source / "linked"
    try:
        if sys.platform == "win32":
            from _winapi import CreateJunction

            CreateJunction(str(outside), str(link))
        else:
            link.symlink_to(outside, target_is_directory=True)
    except PermissionError:
        pytest.skip("Link creation requires filesystem permission")

    snapshot, _ = create_snapshot(source, tmp_path / "work")

    assert list(snapshot.iterdir()) == []
    assert read_source(source, "linked/file.txt")["error"]["code"] == "path_denied"
