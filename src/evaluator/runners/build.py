"""Select the launch configuration for a submission."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from uuid import uuid4

from evaluator.runners import compose as dc

_FRONTEND = "ghcr.io/railwayapp/railpack-frontend:v0.44.0"
_BASE_IMAGES = (
    "ghcr.io/railwayapp/railpack-builder:mise-2026.11.12",
    "ghcr.io/railwayapp/railpack-runtime:mise-2026.11.12",
)


class BuildError(Exception):
    def __init__(self, cause: Literal["environment", "submission"], message: str, log: str | None = None) -> None:
        super().__init__(message)
        self.cause = cause
        self.log = log


@dataclass(frozen=True)
class AppBuild:
    mode: Literal["compose", "dockerfile", "auto"]
    compose_file: Path
    project_dir: Path | None = None
    image: str | None = None

    def close(self) -> None:
        if self.image is not None:
            _remove_image(self.image)


def prepare_app(snapshot: Path, compose_file: Path, service: str, work_dir: Path) -> AppBuild:
    """Use the provided Compose file or generate a base file in the runner workspace."""
    if compose_file.is_file():
        return AppBuild("compose", compose_file)
    if compose_file.exists():
        raise BuildError("submission", "Configured Compose path is not a file")

    image = None
    if (snapshot / "Dockerfile").is_file():
        mode = "dockerfile"
        app = {"build": "."}
    else:
        mode = "auto"
        image = f"raccoon-auto-{uuid4().hex}:submission"
        _build_image(snapshot, work_dir, image)
        app = {"image": image, "pull_policy": "never"}

    generated = work_dir / "compose.generated.json"
    try:
        generated.write_text(json.dumps({"services": {service: app}}, indent=2), encoding="utf-8")
    except OSError as err:
        if image is not None:
            _remove_image(image)
        raise BuildError("environment", "Could not write generated Compose configuration", str(err)) from err
    return AppBuild(mode, generated, snapshot, image)


def _build_image(snapshot: Path, work_dir: Path, image: str) -> None:
    try:
        dc.run_command(["docker", "buildx", "version"], timeout=30)
        dc.ensure_image(_FRONTEND)
    except dc.CommandError as err:
        raise BuildError("environment", "Could not prepare Railpack and Docker Buildx", err.output) from err

    plan = work_dir / "railpack-plan.json"
    try:
        dc.run_command([
            "docker", "run", "--rm", "--entrypoint", "/railpack",
            "-v", f"{snapshot.resolve()}:/submission:ro",
            "-v", f"{work_dir.resolve()}:/out",
            _FRONTEND, "prepare", "--plan-out", "/out/railpack-plan.json", "--error-missing-start", "/submission",
        ], timeout=180)
    except dc.CommandError as err:
        if err.returncode in {-1, 125, 126, 127} or "failed to ensure mise is installed" in err.output.lower():
            raise BuildError("environment", "Could not run the Railpack planner", err.output) from err
        raise BuildError("submission", "Railpack could not determine how to build and start the submission", err.output) from err
    if not plan.is_file():
        raise BuildError("environment", "Railpack did not write the build plan")

    try:
        for base_image in _BASE_IMAGES:
            dc.ensure_image(base_image)
        dc.run_command(["docker", "buildx", "inspect", "--bootstrap"], timeout=120)
    except dc.CommandError as err:
        raise BuildError("environment", "Could not prepare the Railpack builder images or backend", err.output) from err

    try:
        dc.run_command([
            "docker", "buildx", "build", "--load", "--progress", "plain", "--tag", image,
            "--build-arg", f"BUILDKIT_SYNTAX={_FRONTEND}", "--file", str(plan), str(snapshot),
        ], timeout=900)
    except dc.CommandError as err:
        _remove_image(image)
        raise BuildError("submission", "Railpack could not build the submission image", err.output) from err
    except BaseException:
        _remove_image(image)
        raise


def _remove_image(image: str) -> None:
    try:
        dc.run_command(["docker", "image", "rm", image], timeout=60)
    except dc.CommandError:
        pass
