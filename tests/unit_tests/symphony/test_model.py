"""Model response compatibility for lightweight expert planning."""

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model
from openjiuwen.symphony import ExpertCandidate, plan_experts
from openjiuwen.symphony.orchestration.model import invoke_json


@pytest.mark.asyncio
async def test_planner_calls_current_native_model_once():
    # Exercise the existing Model.invoke with only its external client replaced.
    model = Model.__new__(Model)
    client = SimpleNamespace(invoke=AsyncMock(return_value=SimpleNamespace(content='{"status": "no_plan"}')))
    model._client = client

    plan = await plan_experts("整理资料", [ExpertCandidate("a", "资料专家", "整理资料")], model, timeout=3.5)

    assert plan == {"status": "no_plan"}
    client.invoke.assert_awaited_once()
    request = client.invoke.await_args.kwargs
    assert request["timeout"] == 3.5
    assert [message["role"] for message in request["messages"]] == ["system", "user"]
    assert json.loads(request["messages"][1]["content"])["experts"] == [
        {"id": "a", "name": "资料专家", "description": "整理资料", "skills": []}
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        {"content": '  {"status": "no_plan"}  '},
        SimpleNamespace(content='  {"status": "no_plan"}  '),
        {"content": ["{", {"text": '"status": '}, {"content": '"no_plan"'}, "}"]},
        SimpleNamespace(content=[SimpleNamespace(text='{"status": '), SimpleNamespace(content='"no_plan"}')]),
        {"content": [None, 3, {"text": 4}, {"content": False}, '{"status": "no_plan"}']},
        {"content": "```json\n{status: 'no_plan',}\n```"},
    ],
)
async def test_response_text_is_extracted_and_json_is_repaired(response):
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))

    result = await invoke_json(model, system_prompt="规则", user_content="任务")

    assert json.loads(result) == {"status": "no_plan"}
    model.invoke.assert_awaited_once_with(
        messages=[{"role": "system", "content": "规则"}, {"role": "user", "content": "任务"}]
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("response", [{}, {"content": " "}, {"content": []}, {"content": b"bytes"}])
async def test_empty_or_unsupported_content_keeps_error_context(response):
    model = SimpleNamespace(invoke=AsyncMock(return_value=response))

    with pytest.raises(
        RuntimeError, match="Symphony expert planning request failed: response content is empty"
    ) as error:
        await plan_experts("整理资料", [ExpertCandidate("a", "资料专家", "整理资料")], model)

    assert isinstance(error.value.__cause__, ValueError)
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [TimeoutError("timeout"), ValueError("bad request"), RuntimeError("upstream")])
async def test_request_errors_are_wrapped_once_without_retry(failure):
    model = SimpleNamespace(invoke=AsyncMock(side_effect=failure))

    with pytest.raises(RuntimeError, match="Symphony expert planning request failed:") as error:
        await plan_experts("整理资料", [ExpertCandidate("a", "资料专家", "整理资料")], model)

    assert error.value.__cause__ is failure
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_native_framework_error_is_propagated_unchanged():
    failure = BaseError(StatusCode.ERROR, msg="native model failure")
    model = SimpleNamespace(invoke=AsyncMock(side_effect=failure))

    with pytest.raises(BaseError) as error:
        await invoke_json(model, system_prompt="规则", user_content="任务")

    assert error.value is failure
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancellation_is_propagated_unchanged():
    failure = asyncio.CancelledError()
    model = SimpleNamespace(invoke=AsyncMock(side_effect=failure))

    with pytest.raises(asyncio.CancelledError) as error:
        await invoke_json(model, system_prompt="规则", user_content="任务")

    assert error.value is failure
    model.invoke.assert_awaited_once()


@pytest.mark.asyncio
async def test_invalid_repaired_json_remains_a_value_error():
    model = SimpleNamespace(invoke=AsyncMock(return_value={"content": "not json"}))

    with pytest.raises(json.JSONDecodeError):
        await plan_experts("整理资料", [ExpertCandidate("a", "资料专家", "整理资料")], model)

    model.invoke.assert_awaited_once()
