"""Request templates and response field interpretation for HTTP operation calls.

Implements the substitution and JSON Pointer rules in ``contracts/README.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

_VAR = re.compile(r"\{([a-z][a-z0-9_]*)\}")
_BRACED = re.compile(r"\$?\{+[^{}\s\"']+\}+")
"""Brace tokens that look like a placeholder: ``{x}``, ``{{x}}``, ``${x}``, ``{a.b}``."""


class TemplateError(ValueError):
    """A template references an unknown variable or cannot be rendered."""


def variables_in(template: Any) -> set[str]:
    """Names of every ``{variable}`` used anywhere in a template value."""
    if isinstance(template, str):
        return set(_VAR.findall(template))
    if isinstance(template, dict):
        return set().union(*(variables_in(v) for v in template.values())) if template else set()
    if isinstance(template, list):
        return set().union(*(variables_in(v) for v in template)) if template else set()
    return set()


def malformed_placeholders(template: Any) -> set[str]:
    """Placeholder-like tokens that are not a ``{variable}``, such as ``{{a}}``, ``${a}`` or ``{a.b}``.

    These would otherwise be sent as literal text.
    """
    if isinstance(template, str):
        return {m.group() for m in _BRACED.finditer(template) if not _VAR.fullmatch(m.group())}
    if isinstance(template, dict):
        return set().union(*(malformed_placeholders(v) for v in template.values())) if template else set()
    if isinstance(template, list):
        return set().union(*(malformed_placeholders(v) for v in template)) if template else set()
    return set()


def _lookup(name: str, values: dict[str, Any]) -> Any:
    if name not in values:
        raise TemplateError(f"unknown variable {{{name}}}")
    return values[name]


def substitute(template: Any, values: dict[str, Any]) -> Any:
    """Render a JSON template.

    A string that is exactly ``"{name}"`` becomes the variable's value with its
    original type. A variable inside a longer string is inserted as text.
    """
    if isinstance(template, str):
        whole = _VAR.fullmatch(template)
        if whole:
            return _lookup(whole.group(1), values)
        return _VAR.sub(lambda m: _as_text(_lookup(m.group(1), values)), template)
    if isinstance(template, dict):
        return {k: substitute(v, values) for k, v in template.items()}
    if isinstance(template, list):
        return [substitute(v, values) for v in template]
    return template


def _as_text(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


@dataclass(frozen=True)
class RenderedRequest:
    method: str
    path: str
    """Path with query string, relative to the app base URL."""
    headers: dict[str, str]
    body: bytes | None


def render_request(call: dict[str, Any], values: dict[str, Any]) -> RenderedRequest:
    """Build the HTTP request for an ``http`` operation call."""
    path = _VAR.sub(lambda m: quote(_as_text(_lookup(m.group(1), values)), safe=""), call["path_template"])
    query = substitute(call.get("query_template") or {}, values)
    if query:
        path = f"{path}?{urlencode({k: _as_text(v) for k, v in query.items()})}"
    headers = {k: _as_text(v) for k, v in substitute(call.get("headers_template") or {}, values).items()}
    body_template = call.get("body_template")
    body = None
    if body_template is not None:
        body = json.dumps(substitute(body_template, values), ensure_ascii=False).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    headers.setdefault("Accept", "application/json")
    return RenderedRequest(call["method"], path, headers, body)


_MISSING = object()


def resolve_pointer(document: Any, pointer: str) -> Any:
    """Resolve an RFC 6901 JSON Pointer. Returns ``_MISSING`` when it does not resolve."""
    if pointer == "":
        return document
    if not pointer.startswith("/"):
        return _MISSING
    node = document
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(node, dict):
            if token not in node:
                return _MISSING
            node = node[token]
        elif isinstance(node, list):
            if not token.isdigit() or (len(token) > 1 and token.startswith("0")):
                return _MISSING
            index = int(token)
            if index >= len(node):
                return _MISSING
            node = node[index]
        else:
            return _MISSING
    return node


def is_missing(value: Any) -> bool:
    return value is _MISSING


class FieldError(ValueError):
    """A response field did not resolve or has the wrong type."""


def read_outputs(
    document: Any,
    response_fields: dict[str, Any],
    output_types: dict[str, str],
) -> dict[str, Any]:
    """Interpret response fields according to the operation's declared output types."""
    result: dict[str, Any] = {}
    for name, spec in response_fields.items():
        raw = resolve_pointer(document, spec["pointer"])
        if is_missing(raw):
            raise FieldError(f"pointer {spec['pointer']} did not resolve for output {name}")
        kind = output_types.get(name)
        if "equals" in spec:
            value: Any = raw == spec["equals"] and type(raw) is type(spec["equals"])
        else:
            value = raw
        if kind == "id":
            if isinstance(value, bool) or not isinstance(value, (str, int)) or value == "":
                raise FieldError(f"output {name} is not a non-empty string or integer id")
        elif kind == "bool":
            if not isinstance(value, bool):
                raise FieldError(f"output {name} is not a boolean (set equals to compare a value)")
        elif kind == "string":
            if not isinstance(value, str):
                raise FieldError(f"output {name} is not a string")
        elif kind == "integer":
            if isinstance(value, bool) or not isinstance(value, int):
                raise FieldError(f"output {name} is not an integer")
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise FieldError(f"output {name} is not a number")
        result[name] = value
    return result
