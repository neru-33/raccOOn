"""Runner ``compose-postgres-k6``: Docker Compose app, a provided PostgreSQL, k6 bursts.

Lifecycle: ``start`` brings up the database and the submission, ``probe_mapping``
verifies candidate calls, ``run_checks`` runs every declared scenario round,
``close`` removes everything this run created.

The runner never reads or writes the submission's tables. Test data is created
and counted through the submission's own API, using the operations named in
``runner_config.accounting``.
"""

from __future__ import annotations

import json
import secrets
import shutil
import tempfile
import time
import urllib.error
import urllib.request
from itertools import count
from pathlib import Path
from typing import Any

from evaluator.runners import compose as dc
from evaluator.runners.k6 import SummaryError, build_script, parse_summary
from evaluator.runners.probe import ProbeLog, SequenceResult, UrllibTransport, run_sequence, validate_candidate
from evaluator.runners.templates import TemplateError, render_request

RUNNER_ID = "compose-postgres-k6"
LOG_CHARS = 4000
OPENAPI_PATHS = ["/openapi.json", "/v3/api-docs", "/swagger.json", "/docs/openapi.json"]
OPENAPI_MAX_CHARS = 60_000
INPUT_SOURCES = {"scenario_capacity", "unique_user"}
SCENARIO_KINDS = {"operation_sequence", "concurrent_burst"}
COUNT_MODES = {"remaining", "confirmed"}
ACCOUNTING_KEYS = (
    "setup_operation",
    "reserve_operation",
    "confirmed_output",
    "count_operation",
    "count_output",
    "count_mode",
)


class RunnerConfigError(ValueError):
    """``runner_config`` cannot be used by this runner (contract error)."""


class ToolError(RuntimeError):
    """A tool call the host should turn into ``tool_error`` with ``code``."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class _StartFailure(Exception):
    def __init__(self, stage: str, cause: str, message: str, log: str | None = None) -> None:
        super().__init__(message)
        self.stage = stage
        self.cause = cause
        self.log = log


class _RoundFailure(Exception):
    def __init__(self, stage: str, message: str) -> None:
        super().__init__(message)
        self.stage = stage


def _image_version(image: str) -> str | None:
    name = image.rsplit("/", 1)[-1]
    return name.split(":", 1)[1] if ":" in name else None


class ComposePostgresK6Runner:
    def __init__(self, task: dict[str, Any], task_dir: Path, snapshot_dir: Path, run_meta: dict[str, Any]) -> None:
        self.task = task
        self.task_dir = Path(task_dir)
        self.snapshot_dir = Path(snapshot_dir)
        self.run_meta = {k: run_meta[k] for k in ("schema_version", "run_id", "task_id", "submission_sha256")}
        self.config = task.get("runner_config") or {}
        self._validate_config()

        app = self.config["app"]
        tools = {t["id"]: t for t in self.config.get("tools", [])}
        self.app_service: str = app["service"]
        self.app_port: int = int(app["port"])
        self.health_path: str = app["health_path"]
        self.ready_timeout: float = float(app.get("ready_timeout_seconds", 60))
        self.db_image: str = tools["postgres"]["image"]
        self.k6_tool: dict[str, Any] = tools["k6"]
        self.accounting: dict[str, str] = self.config["accounting"]

        self._workdir = Path(tempfile.mkdtemp(prefix="raccoon-runner-"))
        self._password = secrets.token_urlsafe(18)
        self.project = dc.ComposeProject(
            name=dc.project_name(self.run_meta["run_id"]),
            compose_file=self.snapshot_dir / app["compose_file"],
            override_file=self._workdir / "compose.override.json",
        )
        self._base_url: str | None = None
        self._db_version: str | None = None
        self._started = False
        self._closed = False
        self._log = ProbeLog()
        self._maps = count(1)
        self._users = count(1)
        self._user_token = secrets.token_hex(3)

    # ------------------------------------------------------------------ config

    def _validate_config(self) -> None:
        cfg = self.config
        ops = self.task.get("operations") or {}
        try:
            mode = cfg["database"]["mode"]
            app = cfg["app"]
            for key in ("compose_file", "service", "port", "health_path", "database_url_env"):
                app[key]
            declared = {e["name"] for e in app.get("env", [])}
            if app["database_url_env"] not in declared:
                raise RunnerConfigError("app.database_url_env must be one of app.env names")
            tool_ids = {t["id"] for t in cfg["tools"]}
            if not {"postgres", "k6"} <= tool_ids:
                raise RunnerConfigError("tools must declare postgres and k6")
            acc = cfg["accounting"]
            for key in ACCOUNTING_KEYS:
                acc[key]
            inputs = cfg.get("inputs", {})
            scenarios = cfg["scenarios"]
        except KeyError as err:
            raise RunnerConfigError(f"runner_config is missing {err}") from err
        if mode not in ("engine_managed", "external"):
            raise RunnerConfigError(f"unknown database mode {mode!r}")
        if set(inputs.values()) - INPUT_SOURCES:
            raise RunnerConfigError(f"input sources must be one of {sorted(INPUT_SOURCES)}")
        for op_name, spec in ops.items():
            for name, input_spec in spec["inputs"].items():
                if not input_spec.get("from") and name not in inputs:
                    raise RunnerConfigError(f"runner_config.inputs has no source for {op_name}.{name}")
        setup, reserve, counter = acc["setup_operation"], acc["reserve_operation"], acc["count_operation"]
        for role, op in (("setup_operation", setup), ("reserve_operation", reserve), ("count_operation", counter)):
            if op not in ops:
                raise RunnerConfigError(f"accounting.{role} {op!r} is not a task operation")
        if any(i.get("from") for i in ops[setup]["inputs"].values()):
            raise RunnerConfigError("accounting.setup_operation cannot take linked inputs")
        for op in (reserve, counter):
            for name, input_spec in ops[op]["inputs"].items():
                source = input_spec.get("from")
                if source and source.split(".", 1)[0] != setup:
                    raise RunnerConfigError(f"{op}.{name} may only link to {setup} outputs")
        if acc["confirmed_output"] not in ops[reserve]["outputs"]:
            raise RunnerConfigError(f"accounting.confirmed_output must be an output of {reserve}")
        if acc["count_output"] not in ops[counter]["outputs"]:
            raise RunnerConfigError(f"accounting.count_output must be an output of {counter}")
        if acc["count_mode"] not in COUNT_MODES:
            raise RunnerConfigError(f"accounting.count_mode must be one of {sorted(COUNT_MODES)}")
        for test_id, decl in (self.task.get("metrics") or {}).items():
            scenario = scenarios.get(test_id)
            if not scenario or scenario.get("kind") not in SCENARIO_KINDS:
                raise RunnerConfigError(f"scenario for {test_id} must set kind to one of {sorted(SCENARIO_KINDS)}")
            values = set(decl["values"])
            if scenario["kind"] == "operation_sequence":
                if set(scenario.get("metrics", {})) != values:
                    raise RunnerConfigError(f"{test_id}: scenario metrics must cover {sorted(values)}")
                if set(scenario["metrics"].values()) - set(ops):
                    raise RunnerConfigError(f"{test_id}: scenario metrics refer to unknown operations")
            else:
                if {scenario.get("response_metric"), scenario.get("persisted_metric")} != values:
                    raise RunnerConfigError(f"{test_id}: response_metric and persisted_metric must cover {sorted(values)}")

    # ------------------------------------------------------------------- start

    def _tool_status(self, available: bool) -> list[dict[str, Any]]:
        return [
            {"id": "postgres", "version": self._db_version or _image_version(self.db_image), "available": available},
            {"id": "k6", "version": _image_version(self.k6_tool["image"]), "available": available},
        ]

    def _mask(self, text: str | None) -> str | None:
        if text is None:
            return None
        return text.replace(self._password, "***")[-LOG_CHARS:]

    def start(self) -> dict[str, Any]:
        try:
            self._start()
        except _StartFailure as fail:
            return {
                "status": "error",
                "discovered": [],
                "tools": self._tool_status(False),
                "error": {
                    "stage": fail.stage,
                    "message": self._mask(str(fail)),
                    "cause": fail.cause,
                    "log_excerpt": self._mask(fail.log),
                },
            }
        self._started = True
        return {
            "status": "ready",
            "discovered": self._discover(),
            "tools": self._tool_status(True),
            "error": None,
        }

    def _start(self) -> None:
        if self.config["database"]["mode"] != "engine_managed":
            raise _StartFailure("database", "environment", "external database mode is not implemented by this runner yet")
        try:
            dc.docker_available()
        except dc.CommandError as err:
            raise _StartFailure("docker", "environment", f"docker is not available: {err}") from err
        if not self.project.compose_file.is_file():
            raise _StartFailure("compose_file", "submission", f"compose file not found: {self.config['app']['compose_file']}")
        for image in (self.db_image, self.k6_tool["image"]):
            try:
                dc.ensure_image(image)
            except dc.CommandError as err:
                raise _StartFailure("tool_image", "environment", f"cannot prepare image {image}", err.output) from err
        self.project.write_override(
            dc.build_override(
                app_service=self.app_service,
                app_port=self.app_port,
                db_image=self.db_image,
                db_password=self._password,
                db_url_env=self.config["app"]["database_url_env"],
                k6_image=self.k6_tool["image"],
            )
        )
        try:
            self.project.validate()
        except dc.CommandError as err:
            raise _StartFailure("compose_config", "submission", "compose configuration is invalid", err.output) from err
        try:
            self.project.up(dc.DB_SERVICE, wait=True, timeout=180)
            self._db_version = self.project.psql("SHOW server_version").strip() or None
        except dc.CommandError as err:
            raise _StartFailure("database", "environment", "database did not become ready", err.output) from err
        try:
            self.project.up(self.app_service, build=True, no_deps=True, timeout=900)
            host, port = self.project.port(self.app_service, self.app_port)
        except dc.CommandError as err:
            log = err.output + "\n" + self.project.logs(self.app_service)
            raise _StartFailure("app_start", "submission", "submission app did not start", log) from err
        self._base_url = f"http://{host}:{port}"
        self._wait_healthy()

    def _wait_healthy(self) -> None:
        deadline = time.monotonic() + self.ready_timeout
        last = "no response"
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(self._base_url + self.health_path, timeout=3) as resp:  # noqa: S310
                    if resp.status == 200:
                        return
                    last = f"status {resp.status}"
            except urllib.error.HTTPError as err:
                last = f"status {err.code}"
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
                last = str(getattr(err, "reason", err))
            state = self.project.service_state(self.app_service)
            if state in ("exited", "dead"):
                raise _StartFailure(
                    "app_exited",
                    "submission",
                    f"submission app container {state} before GET {self.health_path} returned 200; "
                    "runner infrastructure checks passed",
                    self.project.logs(self.app_service),
                )
            time.sleep(1)
        raise _StartFailure(
            "readiness",
            "submission",
            f"GET {self.health_path} did not return 200 within {self.ready_timeout:.0f}s ({last}); "
            "runner infrastructure checks passed",
            self.project.logs(self.app_service),
        )

    def _discover(self) -> list[dict[str, Any]]:
        for path in OPENAPI_PATHS:
            try:
                with urllib.request.urlopen(self._base_url + path, timeout=5) as resp:  # noqa: S310
                    if resp.status != 200:
                        continue
                    doc = json.loads(resp.read().decode("utf-8"))
            except (urllib.error.URLError, TimeoutError, OSError, ValueError):
                continue
            if not isinstance(doc, dict) or not ({"openapi", "swagger"} & set(doc)):
                continue
            truncated = len(json.dumps(doc)) > OPENAPI_MAX_CHARS
            if truncated:
                doc = {
                    k: doc[k] for k in ("openapi", "swagger", "info") if k in doc
                } | {"paths": {p: sorted(m) for p, m in (doc.get("paths") or {}).items() if isinstance(m, dict)}}
            return [{"kind": "openapi", "location": path, "document": doc, "truncated": truncated}]
        return []

    # ------------------------------------------------------------------- probe

    def _require_started(self) -> None:
        if not self._started:
            raise ToolError("not_started", "call start_submission first")

    def _input_factory(self, capacity: int):
        """Values for operation inputs that are not linked with ``from``."""
        sources = self.config.get("inputs", {})

        def value(op: str, name: str) -> Any:
            source = sources[name]
            if source == "scenario_capacity":
                return int(capacity)
            return f"u-{self._user_token}-{next(self._users)}"

        return value

    def _operations(self, *names: str) -> dict[str, Any]:
        ops = self.task.get("operations") or {}
        return {name: ops[name] for name in names}

    def _persisted_from(self, outputs: dict[str, Any], capacity: int) -> int:
        value = outputs[self.accounting["count_output"]]
        if self.accounting["count_mode"] == "remaining":
            return int(capacity) - int(value)
        return int(value)

    def probe_mapping(self, candidate: dict[str, Any], documented_operations: dict[str, Any] | None = None) -> dict[str, Any]:
        self._require_started()
        if documented_operations is not None:
            if not isinstance(documented_operations, dict):
                raise ToolError("invalid_arguments", "documented_operations must be an object")
            self._log.documented = documented_operations
        operations = self.task.get("operations") or {}
        problem = validate_candidate(candidate, operations)
        if problem:
            outcome = SequenceResult(ok=False, failed_stage="candidate_schema", message=problem)
            return self._log.record(candidate, outcome, None)
        capacity = int(self.config["probe"]["event_capacity"])
        transport = UrllibTransport(self._base_url)
        outcome = run_sequence(operations, candidate, transport, self._input_factory(capacity), timeout=10)
        if outcome.ok:
            self._check_count_meaning(outcome, capacity)
        mapping_id = f"map-{next(self._maps)}" if outcome.ok else None
        return self._log.record(candidate, outcome, mapping_id)

    def _check_count_meaning(self, outcome: SequenceResult, capacity: int) -> None:
        """The probe made one reservation; the count operation must reflect exactly that."""
        acc = self.accounting
        confirmed = outcome.outputs[acc["reserve_operation"]][acc["confirmed_output"]] is True
        expected = 1 if confirmed else 0
        actual = self._persisted_from(outcome.outputs[acc["count_operation"]], capacity)
        if actual != expected:
            state = "confirmed" if confirmed else "unconfirmed"
            outcome.ok = False
            outcome.failed_stage = "response_field"
            outcome.failed_operation = acc["count_operation"]
            outcome.message = (
                f"{acc['count_output']} implies {actual} confirmed reservation(s) on a new event with "
                f"capacity {capacity} after one {state} reservation; expected {expected}"
            )

    def mapping_artifact(self) -> dict[str, Any] | None:
        return self._log.artifact(self.run_meta)

    # ------------------------------------------------------------------ checks

    def run_checks(self, mapping_id: str | None) -> dict[str, Any]:
        self._require_started()
        operations = self.task.get("operations") or {}
        if operations and (mapping_id is None or mapping_id != self._log.verified_id):
            raise ToolError("unknown_mapping", f"mapping {mapping_id!r} was not verified in this run")
        if not operations and mapping_id is not None:
            raise ToolError("unknown_mapping", "this task has no operations; call run_checks with null")
        calls = self._log.verified_calls
        rows: list[dict[str, Any]] = []
        error: dict[str, Any] | None = None
        try:
            for test_id, decl in self.task["metrics"].items():
                scenario = self.config["scenarios"][test_id]
                for rnd in decl["rounds"]:
                    try:
                        if scenario["kind"] == "operation_sequence":
                            metrics = self._sequence_round(scenario, operations, calls)
                        else:
                            metrics = self._burst_round(scenario, calls)
                    except _RoundFailure as fail:
                        error = {"stage": fail.stage, "message": str(fail), "test_id": test_id, "round": rnd}
                        raise
                    rows.append({"test_id": test_id, "round": rnd, "metrics": metrics})
        except _RoundFailure:
            pass
        return {
            **self.run_meta,
            "mapping_id": mapping_id,
            "status": "execution_error" if error else "complete",
            "observations": rows,
            "error": error,
            "runner_info": {
                "runner_id": RUNNER_ID,
                "database_mode": self.config["database"]["mode"],
                # The persisted count comes from the submission's own API, not from the database.
                "count_source": {
                    "kind": "api",
                    "operation": self.accounting["count_operation"],
                    "output": self.accounting["count_output"],
                    "mode": self.accounting["count_mode"],
                },
                "tools": [
                    {"id": t["id"], "version": t["version"] or "unknown"} for t in self._tool_status(True)
                ],
            },
        }

    def _sequence_round(self, scenario, operations, calls) -> dict[str, Any]:
        """Every operation once, in order, on a new event registered through the API."""
        factory = self._input_factory(int(scenario["event_capacity"]))
        result = run_sequence(operations, calls, UrllibTransport(self._base_url), factory, timeout=10)
        if result.failed_stage == "transport":
            raise _RoundFailure("request", result.message or "no response")
        expected = scenario.get("expect_outputs", {})
        metrics = {}
        for metric, op in scenario["metrics"].items():
            outputs = result.outputs.get(op)
            metrics[metric] = outputs is not None and all(
                outputs.get(name) == value for name, value in expected.items() if name in outputs
            )
        return metrics

    def _run_step(self, stage: str, names: tuple[str, ...], calls, factory, prior=None) -> dict[str, Any]:
        """Run setup or count operations; any failure makes the round unmeasurable."""
        result = run_sequence(
            self._operations(*names), calls, UrllibTransport(self._base_url), factory, timeout=10, prior_outputs=prior
        )
        if not result.ok:
            detail = f"{result.failed_operation}: {result.message}"
            raise _RoundFailure(stage, f"{stage} failed ({result.failed_stage}) at {detail}")
        return result.outputs

    def _burst_round(self, scenario, calls) -> dict[str, Any]:
        acc = self.accounting
        capacity = int(scenario["event_capacity"])
        factory = self._input_factory(capacity)
        setup = self._run_step("setup", (acc["setup_operation"],), calls, factory)

        reserve = acc["reserve_operation"]
        call = calls[reserve]
        timeout_s = int(scenario.get("request_timeout_seconds", 10))
        base = f"http://{self.app_service}:{self.app_port}"
        requests = []
        for _ in range(int(scenario["requests"])):
            values = {}
            for name, spec in self._operations(reserve)[reserve]["inputs"].items():
                source = spec.get("from")
                if source:
                    src_op, src_out = source.split(".", 1)
                    values[name] = setup[src_op][src_out]
                else:
                    values[name] = factory(reserve, name)
            try:
                req = render_request(call, values)
            except TemplateError as err:
                raise _RoundFailure("request", str(err)) from err
            requests.append(
                {
                    "method": req.method,
                    "url": base + req.path,
                    "headers": req.headers,
                    "body": req.body.decode("utf-8") if req.body is not None else None,
                    "timeout": f"{timeout_s}s",
                }
            )
        options = self.k6_tool.get("options", {})
        script = build_script(
            requests,
            success_codes=call["success_codes"],
            confirmed_field=call["response_fields"][acc["confirmed_output"]],
            batch=int(options.get("batch", len(requests))),
            batch_per_host=int(options.get("batchPerHost", len(requests))),
        )
        try:
            out = self.project.run_tool(
                dc.K6_SERVICE, "run", "--quiet", "--no-usage-report", "-",
                input_text=script, timeout=timeout_s * 3 + 180,
            )
            burst = parse_summary(out)
        except (dc.CommandError, SummaryError) as err:
            raise _RoundFailure("load_tool", f"k6 run failed: {err}") from err
        if burst.no_response:
            raise _RoundFailure(
                "load_no_response",
                f"{burst.no_response} of {burst.total} requests got no response; "
                "the count is not used because late commits are possible",
            )
        counted = self._run_step("count", (acc["count_operation"],), calls, factory, prior=setup)
        try:
            persisted = self._persisted_from(counted[acc["count_operation"]], capacity)
        except (KeyError, TypeError, ValueError) as err:
            raise _RoundFailure("count", f"cannot read {acc['count_output']}: {err}") from err
        return {scenario["response_metric"]: burst.confirmed, scenario["persisted_metric"]: persisted}

    # ------------------------------------------------------------------- close

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            if self.project.override_file.exists():
                self.project.down()
        except dc.CommandError:
            pass
        shutil.rmtree(self._workdir, ignore_errors=True)
