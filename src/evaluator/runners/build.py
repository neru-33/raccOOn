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


COMPOSE_FILENAMES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")
"""Docker Compose's default file names, in its lookup order."""

INFRA_IMAGES = frozenset(
    {"postgres", "postgis", "mysql", "mariadb", "mongo", "redis", "valkey", "memcached", "rabbitmq", "kafka", "zookeeper"}
)
"""Image names treated as dependencies, never as the app service."""


@dataclass(frozen=True)
class AppBuild:
    mode: Literal["compose", "dockerfile", "auto"]
    compose_file: Path
    service: str
    project_dir: Path | None = None
    image: str | None = None

    def close(self) -> None:
        if self.image is not None:
            _remove_image(self.image)


def find_compose_file(snapshot: Path, configured: Path) -> Path | None:
    """The configured Compose file, else the first default Compose file name in the snapshot root."""
    if configured.is_file():
        return configured
    if configured.exists():
        raise BuildError("submission", "Configured Compose path is not a file")
    for name in COMPOSE_FILENAMES:
        candidate = snapshot / name
        if candidate.is_file():
            return candidate
    return None


def _image_name(image: str) -> str:
    """``docker.io/library/postgres:17-alpine`` -> ``postgres``."""
    return image.rsplit("/", 1)[-1].split("@", 1)[0].split(":", 1)[0].lower()


def select_app_service(services: dict, preferred: str) -> str:
    """Pick the service that runs the submission.

    Order: the preferred name if present; the only service that builds from
    source; the only service that is not a known dependency image; the only
    service. Anything else is ambiguous and fails as a submission problem.
    """
    if preferred in services:
        return preferred
    building = [name for name, spec in services.items() if (spec or {}).get("build")]
    if len(building) == 1:
        return building[0]
    not_infra = [
        name for name, spec in services.items() if _image_name((spec or {}).get("image", "")) not in INFRA_IMAGES
    ]
    if len(not_infra) == 1:
        return not_infra[0]
    if len(services) == 1:
        return next(iter(services))
    raise BuildError(
        "submission",
        f"Cannot tell which Compose service runs the app; services: {sorted(services)}. "
        "Use a single application service or one that builds from source.",
    )


def _compose_services(compose_file: Path) -> dict:
    try:
        out = dc.run_command(
            ["docker", "compose", "-f", str(compose_file), "config", "--format", "json"],
            cwd=compose_file.parent,
            timeout=60,
        )
        services = json.loads(out).get("services") or {}
    except dc.CommandError as err:
        raise BuildError("submission", "Compose configuration is invalid", err.output) from err
    except json.JSONDecodeError as err:
        raise BuildError("environment", "Could not read the Compose configuration", str(err)) from err
    if not services:
        raise BuildError("submission", "Compose file defines no services")
    return services


def prepare_app(snapshot: Path, compose_file: Path, service: str, work_dir: Path) -> AppBuild:
    """Use the submission's Compose file or generate a base file in the runner workspace.

    ``compose_file`` and ``service`` are preferences from the task; a Compose
    file under any default name is accepted and its app service is detected.
    """
    found = find_compose_file(snapshot, compose_file)
    if found is not None:
        return AppBuild("compose", found, select_app_service(_compose_services(found), service))

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
    return AppBuild(mode, generated, service, snapshot, image)


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
