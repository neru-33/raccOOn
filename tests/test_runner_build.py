"""Build selection and environment vs submission failure classification."""

import json

import pytest

from evaluator.runners import build, compose


def test_dockerfile_uses_generated_compose_outside_the_snapshot(tmp_path):
    source = tmp_path / "source"
    work = tmp_path / "work"
    source.mkdir()
    work.mkdir()
    (source / "Dockerfile").touch()

    result = build.prepare_app(source, source / "compose.yaml", "server", work)

    assert result.mode == "dockerfile"
    assert result.project_dir == source
    assert result.compose_file.parent == work
    assert json.loads(result.compose_file.read_text(encoding="utf-8")) == {"services": {"server": {"build": "."}}}
    assert not (source / "compose.yaml").exists()


def test_frontend_pull_failure_is_an_environment_error(tmp_path, monkeypatch):
    monkeypatch.setattr(compose, "run_command", lambda *args, **kwargs: "")

    def unavailable(image):
        raise compose.CommandError(["docker", "pull", image], 1, "registry unavailable")

    monkeypatch.setattr(compose, "ensure_image", unavailable)
    with pytest.raises(build.BuildError) as exc:
        build.prepare_app(tmp_path, tmp_path / "compose.yaml", "app", tmp_path)
    assert exc.value.cause == "environment"
    assert exc.value.log == "registry unavailable"


def test_build_failure_is_a_submission_error_and_removes_temporary_tag(tmp_path, monkeypatch):
    calls = []

    def run(args, **kwargs):
        calls.append(args)
        if args[:2] == ["docker", "run"]:
            (tmp_path / "railpack-plan.json").write_text("{}", encoding="utf-8")
        if args[:3] == ["docker", "buildx", "build"]:
            raise compose.CommandError(args, 1, "dependency could not be installed")
        return ""

    monkeypatch.setattr(compose, "run_command", run)
    with pytest.raises(build.BuildError) as exc:
        build.prepare_app(tmp_path, tmp_path / "compose.yaml", "app", tmp_path)

    assert exc.value.cause == "submission"
    assert exc.value.log == "dependency could not be installed"
    build_args = calls[-2]
    assert calls[-1] == ["docker", "image", "rm", build_args[build_args.index("--tag") + 1]]


def test_compose_file_falls_back_to_default_names_in_lookup_order(tmp_path):
    for name in ("docker-compose.yml", "compose.yml"):
        (tmp_path / name).write_text("services: {}\n", encoding="utf-8")
    assert build.find_compose_file(tmp_path, tmp_path / "compose.yaml") == tmp_path / "compose.yml"
    (tmp_path / "compose.yaml").write_text("services: {}\n", encoding="utf-8")
    assert build.find_compose_file(tmp_path, tmp_path / "compose.yaml") == tmp_path / "compose.yaml"


def test_no_compose_file_is_none(tmp_path):
    assert build.find_compose_file(tmp_path, tmp_path / "compose.yaml") is None


@pytest.mark.parametrize(
    ("services", "expected"),
    [
        ({"app": {"build": "."}, "web": {"build": "."}}, "app"),
        ({"api": {"build": {"context": "."}}, "db": {"image": "postgres:17"}}, "api"),
        ({"api": {"image": "acme/api:1"}, "cache": {"image": "redis:7"}, "db": {"image": "docker.io/library/postgres"}}, "api"),
        ({"server": {"image": "acme/server"}}, "server"),
    ],
)
def test_app_service_selection(services, expected):
    assert build.select_app_service(services, "app") == expected


def test_ambiguous_app_service_is_a_submission_error():
    with pytest.raises(build.BuildError) as raised:
        build.select_app_service({"api": {"build": "."}, "worker": {"build": "."}}, "app")
    assert raised.value.cause == "submission"
    assert "api" in str(raised.value) and "worker" in str(raised.value)


def test_compose_mode_reports_detected_service(tmp_path, monkeypatch):
    (tmp_path / "docker-compose.yml").write_text("services: {}\n", encoding="utf-8")
    config = {"services": {"api": {"build": {"context": "."}}, "db": {"image": "postgres:17"}}}
    monkeypatch.setattr(compose, "run_command", lambda *args, **kwargs: json.dumps(config))

    result = build.prepare_app(tmp_path, tmp_path / "compose.yaml", "app", tmp_path / "work")

    assert (result.mode, result.service, result.compose_file.name) == ("compose", "api", "docker-compose.yml")
