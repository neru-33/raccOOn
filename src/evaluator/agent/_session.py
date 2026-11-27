"""Chat tool dispatch and schema validation shared by model sessions."""

from __future__ import annotations

import json
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from jsonschema import ValidationError

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

    def dispatch(self, turn: ModelTurn, *, stop: Callable[[], bool], limit: int | None = None) -> int:
        """Dispatch calls in order and complete the tool history, including skipped calls."""
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

        dispatched = 0
        for call, recorded in zip(turn.tool_calls, calls, strict=True):
            if stop() or (limit is not None and dispatched >= limit):
                result = tool_error("budget_exhausted", "The session can no longer execute tools")
            else:
                dispatched += 1
                arguments = call.arguments if recorded["function"]["arguments"] != "null" else None
                result = self._dispatch_call(call.name, arguments)
            self.messages.append({
                "role": "tool",
                "tool_call_id": call.id,
                "content": json.dumps(result, ensure_ascii=False),
            })
        return dispatched

    def _dispatch_call(self, name: str, arguments: dict[str, Any] | None) -> dict[str, Any]:
        handler = self._handlers.get(name)
        if handler is None:
            return tool_error("invalid_arguments", "Tool is not available in this session")
        if not isinstance(arguments, dict):
            return tool_error("invalid_arguments", "Tool arguments must be a JSON object")
        try:
            contracts.validate(f"tools.schema.json#/$defs/{name}_args", arguments)
        except ValidationError as error:
            return tool_error("invalid_arguments", f"{name}: {error.json_path} failed the {error.validator} constraint")
        return handler(**arguments)
