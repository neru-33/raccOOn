"""Running logical operations over HTTP: mapping probes and operation sequences.

Shared by the probe step and by ``operation_sequence`` checks. Network access
goes through a ``Transport`` so the logic can be tested without a live app.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from evaluator.runners.templates import (
    FieldError,
    TemplateError,
    read_outputs,
    render_request,
    variables_in,
)

EXCERPT_CHARS = 2000
HTTP_METHODS = {"GET", "POST", "PUT", "PATCH", "DELETE"}


@dataclass(frozen=True)
class HttpResponse:
    status: int
    body: bytes


class TransportError(RuntimeError):
    """No HTTP response: connection error or timeout."""


class Transport(Protocol):
    def send(self, method: str, path: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        ...


class UrllibTransport:
    """Sends requests to ``base_url`` with the standard library."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url.rstrip("/")

    def send(self, method: str, path: str, headers: dict[str, str], body: bytes | None, timeout: float) -> HttpResponse:
        request = urllib.request.Request(self.base_url + path, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 - local app only
                return HttpResponse(resp.status, resp.read())
        except urllib.error.HTTPError as err:
            return HttpResponse(err.code, err.read() or b"")
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as err:
            raise TransportError(str(getattr(err, "reason", err))) from err


def validate_candidate(candidate: Any, operations: dict[str, Any]) -> str | None:
    """Semantic checks on a mapping candidate. Returns an error message or None.

    Full JSON Schema validation of tool arguments is the host's job; this
    guards the runner against candidates that cannot be executed.
    """
    if not isinstance(candidate, dict):
        return "candidate must be an object keyed by operation name"
    missing = [op for op in operations if op not in candidate]
    extra = [op for op in candidate if op not in operations]
    if missing or extra:
        return f"candidate operations must match the task: missing={missing}, unknown={extra}"
    for op_name, call in candidate.items():
        spec = operations[op_name]
        if not isinstance(call, dict) or call.get("protocol") != "http":
            return f"{op_name}: protocol must be http"
        if call.get("method") not in HTTP_METHODS:
            return f"{op_name}: unsupported method {call.get('method')!r}"
        path = call.get("path_template")
        if not isinstance(path, str) or not path.startswith("/") or path.startswith("//") or "://" in path:
            return f"{op_name}: path_template must be a relative path starting with /"
        codes = call.get("success_codes")
        if not isinstance(codes, list) or not codes or not all(isinstance(c, int) and 100 <= c <= 599 for c in codes):
            return f"{op_name}: success_codes must be a non-empty list of HTTP status codes"
        fields = call.get("response_fields")
        if not isinstance(fields, dict) or set(fields) != set(spec["outputs"]):
            return f"{op_name}: response_fields must cover exactly {sorted(spec['outputs'])}"
        for name, field_spec in fields.items():
            if not isinstance(field_spec, dict) or not isinstance(field_spec.get("pointer"), str):
                return f"{op_name}: response_fields.{name}.pointer must be a string"
        used = set()
        for key in ("path_template", "query_template", "headers_template", "body_template"):
            used |= variables_in(call.get(key))
        unknown = used - set(spec["inputs"])
        if unknown:
            return f"{op_name}: unknown template variables {sorted(unknown)}"
    return None


@dataclass
class SequenceResult:
    ok: bool
    outputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    """Outputs read from each operation that succeeded."""
    failed_stage: str | None = None
    failed_operation: str | None = None
    http_status: int | None = None
    message: str | None = None
    response_excerpt: str | None = None


InputFactory = Callable[[str, str], Any]
"""(operation, input name) -> value for inputs not linked with ``from``."""


def run_sequence(
    operations: dict[str, Any],
    calls: dict[str, Any],
    transport: Transport,
    input_value: InputFactory,
    timeout: float,
) -> SequenceResult:
    """Call every operation in declaration order, linking ``from`` inputs."""
    result = SequenceResult(ok=False)
    for op_name, spec in operations.items():
        call = calls[op_name]
        values: dict[str, Any] = {}
        linked: dict[str, Any] = {}
        for input_name, input_spec in spec["inputs"].items():
            source = input_spec.get("from")
            if source:
                src_op, src_out = source.split(".", 1)
                value = result.outputs.get(src_op, {}).get(src_out)
                if value is None or value == "":
                    return _fail(result, "id_link", op_name, None, f"no value for {input_name} from {source}")
                values[input_name] = value
                linked[input_name] = value
            else:
                values[input_name] = input_value(op_name, input_name)
        try:
            request = render_request(call, values)
        except TemplateError as err:
            return _fail(result, "request", op_name, None, str(err))
        try:
            response = transport.send(request.method, request.path, request.headers, request.body, timeout)
        except TransportError as err:
            return _fail(result, "transport", op_name, None, f"{request.method} {request.path}: {err}")
        excerpt = response.body[:EXCERPT_CHARS].decode("utf-8", errors="replace")
        if response.status not in call["success_codes"]:
            stage = "id_link" if linked and response.status == 404 else "status_code"
            return _fail(
                result, stage, op_name, response.status,
                f"{request.method} {request.path} returned {response.status}; expected one of {call['success_codes']}",
                excerpt,
            )
        try:
            document = json.loads(response.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return _fail(result, "response_field", op_name, response.status, "response body is not JSON", excerpt)
        output_types = {name: out["type"] for name, out in spec["outputs"].items()}
        try:
            outputs = read_outputs(document, call["response_fields"], output_types)
        except FieldError as err:
            return _fail(result, "response_field", op_name, response.status, str(err), excerpt)
        for input_name, value in linked.items():
            if input_name in outputs and str(outputs[input_name]) != str(value):
                return _fail(
                    result, "id_link", op_name, response.status,
                    f"{input_name} in response ({outputs[input_name]!r}) differs from the linked value ({value!r})",
                    excerpt,
                )
        result.outputs[op_name] = outputs
    result.ok = True
    return result


def _fail(
    result: SequenceResult,
    stage: str,
    op: str,
    status: int | None,
    message: str,
    excerpt: str | None = None,
) -> SequenceResult:
    result.ok = False
    result.failed_stage = stage
    result.failed_operation = op
    result.http_status = status
    result.message = message
    result.response_excerpt = excerpt
    return result


def documented_probe_status(op: str, call: dict[str, Any], history: list[dict[str, Any]]) -> str:
    """Probe status of a documented call, derived from the probe history (contracts/README.md)."""
    key = (call.get("protocol"), call.get("method"), call.get("path_template"))
    status = "untested"
    for record in history:
        tried = (record.get("candidate") or {}).get(op)
        if not isinstance(tried, dict) or (tried.get("protocol"), tried.get("method"), tried.get("path_template")) != key:
            continue
        if record["result"] == "verified":
            return "verified"
        if record["failed_operation"] == op:
            status = "failed"
    return status


@dataclass
class ProbeLog:
    """Probe history of one run and the ``mapping.json`` it produces."""

    attempts: int = 0
    history: list[dict[str, Any]] = field(default_factory=list)
    documented: dict[str, Any] = field(default_factory=dict)
    verified_id: str | None = None
    verified_calls: dict[str, Any] = field(default_factory=dict)

    def record(self, candidate: Any, outcome: SequenceResult, mapping_id: str | None) -> dict[str, Any]:
        self.attempts += 1
        self.history.append(
            {
                "attempt": self.attempts,
                "candidate": candidate,
                "result": "verified" if outcome.ok else "failed",
                "failed_stage": outcome.failed_stage,
                "failed_operation": outcome.failed_operation,
                "http_status": outcome.http_status,
                "message": outcome.message,
                "mapping_id": mapping_id,
            }
        )
        if outcome.ok and mapping_id:
            self.verified_id = mapping_id
            self.verified_calls = candidate
        return {
            "status": "verified" if outcome.ok else "failed",
            "mapping_id": mapping_id,
            "probe_attempts": self.attempts,
            "failed_stage": outcome.failed_stage,
            "failed_operation": outcome.failed_operation,
            "http_status": outcome.http_status,
            "message": outcome.message,
            "response_excerpt": outcome.response_excerpt,
        }

    def artifact(self, run_meta: dict[str, Any]) -> dict[str, Any] | None:
        if self.attempts == 0:
            return None
        documented = {
            op: {**call, "probe_status": documented_probe_status(op, call, self.history)}
            for op, call in self.documented.items()
        }
        return {
            **run_meta,
            "mapping_id": self.verified_id,
            "documented_operations": documented,
            "operations": self.verified_calls if self.verified_id else {},
            "probe_attempts": self.attempts,
            "probe_history": self.history,
        }
