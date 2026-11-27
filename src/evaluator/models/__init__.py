"""Model connection interface shared by the agent and review modules."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ModelTurn:
    """Either tool calls to execute or a final text/JSON content."""

    tool_calls: list[ToolCall] = field(default_factory=list)
    content: str | None = None


class ModelClient(Protocol):
    @property
    def model_info(self) -> dict[str, Any]:
        """``model_info`` contract object: client, endpoint_host, model. Never the API key."""
        ...

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        response_schema: dict[str, Any] | None,
    ) -> ModelTurn:
        """Send OpenAI Chat-format messages and tool definitions, return one turn."""
        ...


def load_client(env: Mapping[str, str]) -> ModelClient:
    """Build the client from EVALUATOR_* environment variables.

    EVALUATOR_MODEL_CLIENT=module:Class selects a registered implementation;
    otherwise the default OpenAI-compatible client is used.
    """
    raise NotImplementedError
