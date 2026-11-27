import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from evaluator import contracts
from evaluator.models import ModelRequestError, ModelTurn, ToolCall, load_client


class FakeAPIError(Exception):
    pass


class FakeStatusError(FakeAPIError):
    def __init__(self, status_code, message, param=None):
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.param = param


class FakeTimeoutError(FakeAPIError):
    pass


class RegisteredClient:
    model_info = {"client": "RegisteredClient", "endpoint_host": None, "model": "example"}

    def complete(self, messages, tools, response_schema):
        return ModelTurn(content="registered")


@pytest.fixture
def sdk(monkeypatch):
    sdk = SimpleNamespace(
        OpenAI=Mock(),
        APIError=FakeAPIError,
        APIStatusError=FakeStatusError,
        APITimeoutError=FakeTimeoutError,
    )
    monkeypatch.setitem(sys.modules, "openai", sdk)
    return sdk


def completion(content=None, tool_calls=()):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=tool_calls))])


def test_loads_default_connection_and_requests_strict_review_schema(sdk):
    client = load_client({"EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name"})
    sdk.OpenAI.assert_called_once_with(
        api_key="test-key", base_url="https://api.openai.com/v1", timeout=120.0, max_retries=0
    )
    assert client.model_info == {
        "client": "OpenAICompatibleClient", "endpoint_host": "api.openai.com", "model": "model-name"
    }

    create = sdk.OpenAI.return_value.chat.completions.create
    create.return_value = completion('{"quality_findings": [], "hypotheses": []}')
    schema = contracts.review_output_schema()
    messages = [{"role": "user", "content": "Review the submission"}]
    turn = client.complete(messages, [], schema)

    assert json.loads(turn.content) == {"quality_findings": [], "hypotheses": []}
    create.assert_called_once_with(
        model="model-name",
        messages=messages,
        response_format={"type": "json_schema", "json_schema": {
            "name": "evaluation_review", "strict": True, "schema": schema,
        }},
    )


def test_normalizes_tool_calls_in_order_and_preserves_invalid_arguments(sdk):
    client = load_client({"EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name"})
    create = sdk.OpenAI.return_value.chat.completions.create
    create.return_value = completion(tool_calls=[
        SimpleNamespace(type="function", id=f"call_{index}", function=SimpleNamespace(name="read_source", arguments=value))
        for index, value in enumerate(('{"path":"README.md"}', '[1]', '{'))
    ])
    tools = contracts.tool_definitions("agent")

    turn = client.complete([], tools, None)

    assert turn.tool_calls == [
        ToolCall("call_0", "read_source", {"path": "README.md"}),
        ToolCall("call_1", "read_source", None),
        ToolCall("call_2", "read_source", None),
    ]
    create.assert_called_once_with(model="model-name", messages=[], tools=tools, parallel_tool_calls=False)


def test_compatibility_mode_uses_prompt_and_remembers_unsupported_parallel_option(sdk):
    client = load_client({
        "EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name",
        "EVALUATOR_BASE_URL": "https://gateway.example:8443/proxy/v1?token=private",
        "EVALUATOR_RESPONSE_FORMAT": "none",
    })
    create = sdk.OpenAI.return_value.chat.completions.create
    create.side_effect = [
        FakeStatusError(422, "extra inputs are not permitted for parallel_tool_calls"),
        completion("{}"),
        completion("{}"),
    ]
    messages = [{"role": "system", "content": "Review instructions"}]
    tools = contracts.tool_definitions("review")
    schema = {"type": "object"}

    client.complete(messages, tools, schema)
    client.complete(messages, tools, None)

    initial, retry, subsequent = [call.kwargs for call in create.call_args_list]
    assert initial["parallel_tool_calls"] is False
    assert "parallel_tool_calls" not in retry and "parallel_tool_calls" not in subsequent
    assert all("response_format" not in request for request in (initial, retry, subsequent))
    assert json.dumps(schema) in retry["messages"][0]["content"]
    assert subsequent["messages"] == messages == [{"role": "system", "content": "Review instructions"}]
    assert client.model_info["endpoint_host"] == "gateway.example"


def test_reasoning_effort_none_is_sent_when_configured(sdk):
    client = load_client({
        "EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name",
        "EVALUATOR_REASONING_EFFORT": "none",
    })
    create = sdk.OpenAI.return_value.chat.completions.create
    create.return_value = completion("{}")
    tools = contracts.tool_definitions("agent")

    client.complete([], tools, None)
    client.complete([], [], contracts.review_output_schema())

    assert [call.kwargs["reasoning_effort"] for call in create.call_args_list] == ["none", "none"]


@pytest.mark.parametrize(("error", "message"), [
    (FakeStatusError(401, "private credentials"), "Model endpoint returned HTTP 401"),
    (FakeTimeoutError("private endpoint details"), "Model request timed out"),
])
def test_request_failure_exposes_only_safe_error_message(sdk, error, message):
    client = load_client({"EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name"})
    create = sdk.OpenAI.return_value.chat.completions.create
    create.side_effect = error

    with pytest.raises(ModelRequestError) as failure:
        client.complete([], contracts.tool_definitions("agent"), None)

    assert str(failure.value) == message
    assert create.call_count == 1


def test_registered_client_uses_its_own_configuration():
    client = load_client({"EVALUATOR_MODEL_CLIENT": f"{__name__}:RegisteredClient"})
    assert isinstance(client, RegisteredClient)


@pytest.mark.parametrize(("env", "variable"), [
    ({}, "EVALUATOR_API_KEY"),
    ({"EVALUATOR_MODEL_CLIENT": "not-a-class"}, "EVALUATOR_MODEL_CLIENT"),
    ({"EVALUATOR_RESPONSE_FORMAT": "json_object"}, "EVALUATOR_RESPONSE_FORMAT"),
    ({"EVALUATOR_REASONING_EFFORT": "auto"}, "EVALUATOR_REASONING_EFFORT"),
    ({"EVALUATOR_BASE_URL": "file:///tmp/client"}, "EVALUATOR_BASE_URL"),
])
def test_invalid_configuration_fails_before_sdk_initialization(sdk, env, variable):
    if env:
        env = {"EVALUATOR_API_KEY": "test-key", "EVALUATOR_MODEL": "model-name", **env}
    with pytest.raises(ValueError, match=variable):
        load_client(env)
    sdk.OpenAI.assert_not_called()
