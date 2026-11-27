"""Thin wrapper around ``docker compose`` for one evaluation run.

The submission's Compose file holds only the app. The runner adds its own
services (database, load tool) with an override file. JSON is valid YAML, so
the override is written as JSON and no YAML library is needed.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DB_SERVICE = "raccoon-db"
K6_SERVICE = "raccoon-k6"
DB_USER = "raccoon"
DB_NAME = "raccoon"


class CommandError(RuntimeError):
    def __init__(self, args: list[str], returncode: int, output: str) -> None:
        super().__init__(f"{' '.join(args[:6])}... exited with {returncode}: {output[-1000:]}")
        self.returncode = returncode
        self.output = output


def project_name(run_id: str) -> str:
    """Compose project names allow lowercase letters, digits, dashes and underscores."""
    return "raccoon-" + re.sub(r"[^a-z0-9_-]", "-", run_id.lower())[:50]


def database_url(password: str) -> str:
    return f"postgresql://{DB_USER}:{password}@{DB_SERVICE}:5432/{DB_NAME}"


def build_override(
    *,
    app_service: str,
    app_port: int,
    db_image: str,
    db_password: str,
    db_url_env: str,
    k6_image: str,
) -> dict[str, Any]:
    """Override file: database service, app environment and port, load tool service."""
    return {
        "services": {
            app_service: {
                "environment": {"PORT": str(app_port), db_url_env: database_url(db_password)},
                "ports": [f"127.0.0.1::{app_port}"],
            },
            DB_SERVICE: {
                "image": db_image,
                "environment": {
                    "POSTGRES_USER": DB_USER,
                    "POSTGRES_PASSWORD": db_password,
                    "POSTGRES_DB": DB_NAME,
                },
                "healthcheck": {
                    "test": ["CMD-SHELL", f"pg_isready -U {DB_USER} -d {DB_NAME}"],
                    "interval": "1s",
                    "timeout": "3s",
                    "retries": 60,
                },
            },
            K6_SERVICE: {
                "image": k6_image,
                "profiles": ["tools"],
                "entrypoint": ["k6"],
            },
        }
    }


def run_command(args: list[str], *, cwd: Path | None = None, input_text: str | None = None, timeout: float = 300) -> str:
    """Run a command and return combined stdout. Raises CommandError on non-zero exit or timeout."""
    try:
        proc = subprocess.run(
            args,
            cwd=cwd,
            input=input_text,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as err:
        raise CommandError(args, -1, f"timed out after {timeout}s") from err
    except FileNotFoundError as err:
        raise CommandError(args, -1, f"command not found: {args[0]}") from err
    if proc.returncode != 0:
        raise CommandError(args, proc.returncode, (proc.stdout or "") + (proc.stderr or ""))
    return proc.stdout


def docker_available() -> str:
    """Docker server version. Raises CommandError when the daemon is not reachable."""
    return run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=30).strip()


def ensure_image(image: str) -> None:
    try:
        run_command(["docker", "image", "inspect", image], timeout=30)
    except CommandError:
        run_command(["docker", "pull", image], timeout=600)


@dataclass
class ComposeProject:
    name: str
    compose_file: Path
    override_file: Path
    project_dir: Path | None = None

    @property
    def cwd(self) -> Path:
        return self.project_dir if self.project_dir is not None else self.compose_file.parent

    def _args(self, *args: str) -> list[str]:
        command = ["docker", "compose", "-p", self.name]
        if self.project_dir is not None:
            command.extend(["--project-directory", str(self.project_dir)])
        return [*command, "-f", str(self.compose_file), "-f", str(self.override_file), *args]

    def run(self, *args: str, input_text: str | None = None, timeout: float = 300) -> str:
        return run_command(self._args(*args), cwd=self.cwd, input_text=input_text, timeout=timeout)

    def write_override(self, override: dict[str, Any]) -> None:
        self.override_file.write_text(json.dumps(override, indent=2), encoding="utf-8")

    def validate(self) -> None:
        self.run("config", "-q", timeout=60)

    def up(self, *services: str, build: bool = False, wait: bool = False, no_deps: bool = False, timeout: float = 600) -> None:
        args = ["up", "-d"]
        if build:
            args.append("--build")
        if wait:
            args.append("--wait")
        if no_deps:
            args.append("--no-deps")
        self.run(*args, *services, timeout=timeout)

    def port(self, service: str, container_port: int) -> tuple[str, int]:
        out = self.run("port", service, str(container_port), timeout=30).strip().splitlines()
        if not out:
            raise CommandError(["docker", "compose", "port"], 1, "no published port")
        host, _, port = out[-1].rpartition(":")
        return host or "127.0.0.1", int(port)

    def service_state(self, service: str) -> str | None:
        """Container state of a service (``running``, ``exited``, ...), or None if unknown."""
        try:
            out = self.run("ps", "-a", "--format", "json", service, timeout=30).strip()
        except CommandError:
            return None
        for line in out.splitlines():
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue
            entries = entry if isinstance(entry, list) else [entry]
            for item in entries:
                if item.get("Service") == service:
                    return item.get("State")
        return None

    def logs(self, service: str, tail: int = 80) -> str:
        try:
            return self.run("logs", "--no-color", "--tail", str(tail), service, timeout=30)
        except CommandError as err:
            return err.output

    def psql(self, sql: str, variables: dict[str, Any] | None = None, timeout: float = 60) -> str:
        """Run SQL inside the database container. Variables are bound as psql variables (``:'name'``)."""
        args = ["exec", "-T", DB_SERVICE, "psql", "-X", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1",
                "-U", DB_USER, "-d", DB_NAME]
        for key, value in (variables or {}).items():
            args += ["-v", f"{key}={value}"]
        return self.run(*args, input_text=sql if sql.endswith("\n") else sql + "\n", timeout=timeout)

    def run_tool(self, service: str, *args: str, input_text: str | None = None, timeout: float = 300) -> str:
        return self.run("run", "--rm", "-T", "--no-deps", service, *args, input_text=input_text, timeout=timeout)

    def down(self) -> None:
        self.run("--profile", "tools", "down", "-v", "--remove-orphans", timeout=180)
