"""Internal helpers for invoking the native openJiuwen model from orchestration."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from json_repair import repair_json

from openjiuwen.core.common.exception.errors import BaseError
from openjiuwen.core.foundation.llm import Model


async def invoke_json(
    model: Model,
    *,
    system_prompt: str,
    user_content: str,
    timeout: int | float | None = None,
    error_context: str = "Model",
) -> str:
    """Invoke ``Model`` once and return repaired JSON text."""

    invoke_kwargs = {}
    if timeout is not None:
        invoke_kwargs["timeout"] = timeout
    try:
        response = await model.invoke(
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
            **invoke_kwargs,
        )
        content = _extract_text_content(response)
        if not content:
            raise ValueError("response content is empty")
        return repair_json(content, return_objects=False)
    except BaseError:
        raise
    except Exception as exc:
        raise RuntimeError(f"{error_context} request failed: {exc}") from exc


def _extract_text_content(response: Any) -> str:
    content = getattr(response, "content", None)
    if content is None and isinstance(response, Mapping):
        content = response.get("content")
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes, bytearray)):
        return "".join(_content_part_text(item) for item in content).strip()
    return ""


def _content_part_text(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, Mapping):
        value = item.get("text") or item.get("content") or ""
    else:
        value = getattr(item, "text", None) or getattr(item, "content", None) or ""
    return value if isinstance(value, str) else ""
