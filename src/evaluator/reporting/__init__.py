"""Writes report.json and report.md from the core's report object."""

from __future__ import annotations

from pathlib import Path
from typing import Any


def write(report: dict[str, Any], output_dir: Path) -> None:
    raise NotImplementedError
