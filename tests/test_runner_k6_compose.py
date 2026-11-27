from __future__ import annotations

import json

import pytest

from evaluator.runners import compose, k6


def test_build_script_embeds_requests_and_field() -> None:
    script = k6.build_script(
        [{"method": "POST", "url": "http://app:8080/r", "headers": {}, "body": "{}", "timeout": "10s"}],
        success_codes=[201],
        confirmed_field={"pointer": "/data/state", "equals": "accepted"},
        batch=50,
        batch_per_host=50,
    )
    assert "batch: 50" in script and "batchPerHost: 50" in script
    assert '"has_equals": true' in script and '"equals": "accepted"' in script
    assert "http://app:8080/r" in script
    assert "__" not in script.replace("__proto__", "")


def test_parse_summary_takes_last_summary_line() -> None:
    out = "noise\n" + json.dumps({"raccoon": {"total": 50, "responses": 50, "no_response": 0, "confirmed": 2}}) + "\n"
    assert k6.parse_summary(out) == k6.BurstResult(total=50, responses=50, no_response=0, confirmed=2)


@pytest.mark.parametrize("out", ["", "no json here", '{"raccoon": {"total": "x"}}'])
def test_parse_summary_errors(out: str) -> None:
    with pytest.raises(k6.SummaryError):
        k6.parse_summary(out)


def test_project_name_is_compose_safe() -> None:
    name = compose.project_name("run-20261127T140000Z-A1B2C3")
    assert name == "raccoon-run-20261127t140000z-a1b2c3"


def test_override_injects_database_and_publishes_app_port() -> None:
    override = compose.build_override(
        app_service="app",
        app_port=8080,
        db_image="postgres:17-alpine",
        db_password="pw",
        db_url_env="DATABASE_URL",
        k6_image="grafana/k6:1.0.0",
    )
    app = override["services"]["app"]
    assert app["environment"]["DATABASE_URL"] == "postgresql://raccoon:pw@raccoon-db:5432/raccoon"
    assert app["ports"] == ["127.0.0.1::8080"]
    assert override["services"][compose.DB_SERVICE]["healthcheck"]
    assert override["services"][compose.K6_SERVICE]["profiles"] == ["tools"]
