"""Command line entry point: ``python -m evaluator run ...``."""

from __future__ import annotations

import argparse
import sys
from enum import IntEnum
from pathlib import Path


class ExitCode(IntEnum):
    OK = 0
    """Observation and grading completed (functional or review failures included)."""
    CONTRACT_ERROR = 2
    """Task or input contract error."""
    INCOMPLETE = 3
    """Grading not completed (mapping unresolved, execution error, ...)."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="evaluator")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="evaluate one submission")
    run.add_argument("--task", required=True, type=Path, help="path to task.json")
    run.add_argument("--submission", required=True, type=Path, help="submission directory")
    run.add_argument("--out", required=True, type=Path, help="new output directory")
    run.add_argument("--review", choices=["live", "off"], default="off")
    return parser


def _check_inputs(args: argparse.Namespace) -> str | None:
    if not args.task.is_file():
        return f"task file not found: {args.task}"
    if not args.submission.is_dir():
        return f"submission directory not found: {args.submission}"
    if args.out.exists():
        return f"output path already exists: {args.out}"
    return None


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "run":
        problem = _check_inputs(args)
        if problem:
            print(f"error: {problem}", file=sys.stderr)
            return ExitCode.CONTRACT_ERROR
        print("error: evaluation pipeline is not implemented yet", file=sys.stderr)
        return ExitCode.INCOMPLETE
    return ExitCode.CONTRACT_ERROR
