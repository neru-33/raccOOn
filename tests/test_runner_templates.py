from __future__ import annotations

import json

import pytest

from evaluator.runners.templates import (
    FieldError,
    TemplateError,
    is_missing,
    read_outputs,
    render_request,
    resolve_pointer,
    substitute,
    variables_in,
)


def test_whole_string_variable_keeps_type() -> None:
    assert substitute({"n": "{count}", "s": "id-{count}"}, {"count": 3}) == {"n": 3, "s": "id-3"}


def test_nested_templates_and_lists() -> None:
    tpl = {"a": ["{x}", {"b": "{y}"}], "c": None, "d": 1}
    assert substitute(tpl, {"x": True, "y": "v"}) == {"a": [True, {"b": "v"}], "c": None, "d": 1}
    assert variables_in(tpl) == {"x", "y"}


def test_unknown_variable_raises() -> None:
    with pytest.raises(TemplateError):
        substitute("{missing}", {})


def test_render_request_encodes_path_and_query() -> None:
    call = {
        "method": "GET",
        "path_template": "/items/{item_id}",
        "query_template": {"q": "{term}", "page": 2},
        "headers_template": {"Idempotency-Key": "k-{item_id}"},
        "body_template": None,
    }
    req = render_request(call, {"item_id": "a/b c", "term": "x&y"})
    assert req.path == "/items/a%2Fb%20c?q=x%26y&page=2"
    assert req.headers["Idempotency-Key"] == "k-a/b c"
    assert req.body is None


def test_render_request_json_body() -> None:
    call = {"method": "POST", "path_template": "/r", "body_template": {"session": "{event_id}"}}
    req = render_request(call, {"event_id": "evt-1"})
    assert json.loads(req.body) == {"session": "evt-1"}
    assert req.headers["Content-Type"] == "application/json"


@pytest.mark.parametrize(
    ("pointer", "expected"),
    [("", {"a": [{"b/c": 1, "d~e": 2}]}), ("/a/0/b~1c", 1), ("/a/0/d~0e", 2)],
)
def test_resolve_pointer(pointer: str, expected: object) -> None:
    doc = {"a": [{"b/c": 1, "d~e": 2}]}
    assert resolve_pointer(doc, pointer) == expected


@pytest.mark.parametrize("pointer", ["/x", "/a/1", "/a/01", "a", "/a/0/b~1c/z"])
def test_resolve_pointer_missing(pointer: str) -> None:
    assert is_missing(resolve_pointer({"a": [{"b/c": 1}]}, pointer))


def test_read_outputs_with_equals_and_id() -> None:
    doc = {"data": {"reference": "r-1", "state": "accepted"}}
    fields = {
        "reservation_id": {"pointer": "/data/reference"},
        "confirmed": {"pointer": "/data/state", "equals": "accepted"},
    }
    assert read_outputs(doc, fields, {"reservation_id": "id", "confirmed": "bool"}) == {
        "reservation_id": "r-1",
        "confirmed": True,
    }
    doc["data"]["state"] = "waiting"
    assert read_outputs(doc, fields, {"reservation_id": "id", "confirmed": "bool"})["confirmed"] is False


@pytest.mark.parametrize(
    ("doc", "fields", "types"),
    [
        ({"id": ""}, {"rid": {"pointer": "/id"}}, {"rid": "id"}),
        ({"id": True}, {"rid": {"pointer": "/id"}}, {"rid": "id"}),
        ({"ok": "yes"}, {"ok": {"pointer": "/ok"}}, {"ok": "bool"}),
        ({}, {"ok": {"pointer": "/ok", "equals": 1}}, {"ok": "bool"}),
    ],
)
def test_read_outputs_rejects_bad_values(doc: dict, fields: dict, types: dict) -> None:
    with pytest.raises(FieldError):
        read_outputs(doc, fields, types)


def test_equals_does_not_match_bool_against_int() -> None:
    out = read_outputs({"s": True}, {"ok": {"pointer": "/s", "equals": 1}}, {"ok": "bool"})
    assert out["ok"] is False
