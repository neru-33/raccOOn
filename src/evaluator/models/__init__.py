"""Model connection interface shared by the agent and review modules."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from importlib import import_module
from typing import Any, Literal, Protocol, cast
from urllib.parse import urlsplit


_DEFAULT_BASE_URL = "https://api.openai.com/v1"
_REQUEST_TIMEOUT_SECONDS = 120.0
_ResponseFormat = Literal["json_schema", "none"]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: dict[str, Any] | None
    """Decoded arguments, or None when the model did not return a JSON object."""


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


class ModelRequestError(RuntimeError):
    """A model request failed. The message is safe to include in an evaluation report."""


class OpenAICompatibleClient:
    """Adapter for OpenAI-compatible Chat Completions endpoints."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str = _DEFAULT_BASE_URL,
        response_format: _ResponseFormat = "json_schema",
    ) -> None:
        try:
            url = urlsplit(base_url)
            host = url.hostname
        except ValueError:
            raise ValueError("EVALUATOR_BASE_URL must be an HTTP(S) endpoint URL") from None
        if url.scheme not in {"http", "https"} or not host:
            raise ValueError("EVALUATOR_BASE_URL must be an HTTP(S) endpoint URL")

        try:
            self._sdk = import_module("openai")
        except ModuleNotFoundError as error:
            if error.name != "openai":
                raise
            raise RuntimeError('Install the model dependencies with pip install -e ".[model]"') from None

        self._client = self._sdk.OpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=_REQUEST_TIMEOUT_SECONDS,
            max_retries=0,
        )
        self._model = model
        self._host = host
        self._response_format = response_format
        self._supports_parallel_option = True

    @property
    def model_info(self) -> dict[str, Any]:
        return {
            "client": type(self).__name__,
            "endpoint_host": self._host,
            "model": self._model,
        }

    def complete(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        response_schema: dict[str, Any] | None,
    ) -> ModelTurn:
        request: dict[str, Any] = {"model": self._model, "messages": messages}
        if tools:
            request["tools"] = tools
            if self._supports_parallel_option:
                request["parallel_tool_calls"] = False

        if response_schema is not None:
            if self._response_format == "json_schema":
                request["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": "evaluation_review", "strict": True, "schema": response_schema},
                }
            else:
                request["messages"] = _messages_with_schema(messages, response_schema)

        try:
            try:
                completion = self._client.chat.completions.create(**request)
            except self._sdk.APIStatusError as error:
                if "parallel_tool_calls" not in request or not _rejects_parallel_option(error):
                    raise
                self._supports_parallel_option = False
                del request["parallel_tool_calls"]
                completion = self._client.chat.completions.create(**request)
        except self._sdk.APITimeoutError:
            raise ModelRequestError("Model request timed out") from None
        except self._sdk.APIStatusError as error:
            raise ModelRequestError(f"Model endpoint returned HTTP {error.status_code}") from None
        except self._sdk.APIError:
            raise ModelRequestError("Model endpoint request failed") from None

        if not completion.choices:
            raise ModelRequestError("Model endpoint returned no completion choices")
        message = completion.choices[0].message
        tool_calls = []
        for call in message.tool_calls or []:
            if call.type != "function":
                raise ModelRequestError("Model endpoint returned an unsupported tool call type")
            try:
                arguments = json.loads(call.function.arguments)
            except (TypeError, ValueError):
                arguments = None
            tool_calls.append(
                ToolCall(call.id, call.function.name, arguments if isinstance(arguments, dict) else None)
            )
        return ModelTurn(tool_calls=tool_calls, content=message.content)


def _rejects_parallel_option(error: Any) -> bool:
    if error.status_code not in {400, 422}:
        return False
    if error.param == "parallel_tool_calls":
        return True
    message = error.message.lower()
    return "parallel_tool_calls" in message and any(
        text in message
        for text in ("unsupported", "not supported", "unknown", "unrecognized", "not permitted", "extra inputs")
    )


def _messages_with_schema(
    messages: list[dict[str, Any]], response_schema: dict[str, Any]
) -> list[dict[str, Any]]:
    instruction = "Return only a JSON object matching this JSON Schema:\n" + json.dumps(response_schema)
    if messages and messages[0].get("role") == "system" and isinstance(messages[0].get("content"), str):
        first = {**messages[0], "content": messages[0]["content"] + "\n\n" + instruction}
        return [first, *messages[1:]]
    return [{"role": "system", "content": instruction}, *messages]


def load_client(env: Mapping[str, str]) -> ModelClient:
    """Build the client from EVALUATOR_* environment variables.

    EVALUATOR_MODEL_CLIENT=module:Class selects a registered implementation;
    otherwise the default OpenAI-compatible client is used.
    """
    registration = env.get("EVALUATOR_MODEL_CLIENT", "").strip()
    if registration:
        module, _, name = registration.partition(":")
        if not module or not name or ":" in name:
            raise ValueError("EVALUATOR_MODEL_CLIENT must use module:Class format")
        try:
            client_class = getattr(import_module(module), name)
        except (ImportError, AttributeError):
            raise ValueError("Could not load the class in EVALUATOR_MODEL_CLIENT") from None
        if not isinstance(client_class, type):
            raise ValueError("EVALUATOR_MODEL_CLIENT must name a ModelClient class")
        client = client_class()
        if not callable(getattr(client, "complete", None)) or not isinstance(getattr(client, "model_info", None), dict):
            raise ValueError("EVALUATOR_MODEL_CLIENT must implement complete and model_info")
        return cast(ModelClient, client)

    api_key = env.get("EVALUATOR_API_KEY", "").strip()
    model = env.get("EVALUATOR_MODEL", "").strip()
    if not api_key or not model:
        raise ValueError("EVALUATOR_API_KEY and EVALUATOR_MODEL are required")

    response_format = env.get("EVALUATOR_RESPONSE_FORMAT", "").strip() or "json_schema"
    if response_format not in {"json_schema", "none"}:
        raise ValueError("EVALUATOR_RESPONSE_FORMAT must be json_schema or none")
    return OpenAICompatibleClient(
        api_key=api_key,
        model=model,
        base_url=env.get("EVALUATOR_BASE_URL", "").strip() or _DEFAULT_BASE_URL,
        response_format=cast(_ResponseFormat, response_format),
    )
