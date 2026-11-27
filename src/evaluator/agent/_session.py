"""Chat tool dispatch and schema validation shared by model sessions."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import cache, partial
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from referencing import Registry, Resource

from evaluator import contracts, snapshot
from evaluator.models import ModelTurn


ToolHandler = Callable[..., dict[str, Any]]


def snapshot_tools(submission_dir: Path) -> dict[str, ToolHandler]:
    return {
        "list_files": partial(snapshot.list_files, submission_dir),
        "search_code": partial(snapshot.search_code, submission_dir),
        "read_source": partial(snapshot.read_source, submission_dir),
    }


def tool_error(code: str, message: str) -> dict[str, Any]:
    return {"error": {"code": code, "message": message}}


@cache
def _tool_validators() -> dict[str, Draft202012Validator]:
    schemas = {name: contracts.load_schema(name) for name in contracts.schema_names()}
    registry = Registry().with_resources(
        (schema["$id"], Resource.from_contents(schema)) for schema in schemas.values()
    )
    tools = schemas["tools.schema.json"]
    return {
        name: Draft202012Validator({"$ref": f"{tools['$id']}#/$defs/{name}_args"}, registry=registry)
        for name in contracts.tool_names("agent")
    }


class ChatToolSession:
    def __init__(
        self,
        tool_set: contracts.ToolSet,
        messages: list[dict[str, Any]],
        handlers: dict[str, ToolHandler],
    ) -> None:
        self.messages = messages
        self.definitions = contracts.tool_definitions(tool_set)
        self._handlers = {name: handlers[name] for name in contracts.tool_names(tool_set)}

    def dispatch(self, turn: ModelTurn, *, stop: Callable[[], bool]) -> None:
        """Record a model turn and dispatch its calls in order until the session ends."""
        calls = []
        for call in turn.tool_calls:
            try:
                arguments = json.dumps(call.arguments, allow_nan=False)
            except (TypeError, ValueError):
                arguments = "null"
            calls.append({
                "id": call.id,
                "type": "function",
                "function": {"name": call.name, "arguments": arguments},
            })
        self.messages.append({"role": "assistant", "content": turn.content, "tool_calls": calls})

        for call, recorded in zip(turn.tool_calls, calls, strict=True):
            if stop():
                break
            handler = self._handlers.get(call.name)
            if handler is None:
                result = tool_error("invalid_arguments", "Tool is not available in this session")
            elif not isinstance(call.arguments, dict) or recorded["function"]["arguments"] == "null":
                result = tool_error("invalid_arguments", "Tool arguments must be a JSON object")
            else:
                error = next(_tool_validators()[call.name].iter_errors(call.arguments), None)
                if error is None:
                    result = handler(**call.arguments)
                else:
                    result = tool_error(
                        "invalid_arguments", f"{call.name}: {error.json_path} failed the {error.validator} constraint"
                    )
            self.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
