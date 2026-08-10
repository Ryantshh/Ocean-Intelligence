from __future__ import annotations

import json
from collections.abc import Iterable

from .schemas import PromptBuilderRequest


Message = dict[str, str]


def build_messages(payload: PromptBuilderRequest) -> list[Message]:
    messages: list[Message] = []

    if payload.system_prompt.strip():
        messages.append({"role": "system", "content": payload.system_prompt.strip()})

    if payload.context.strip():
        messages.append({"role": "system", "content": f"Context:\n{payload.context.strip()}"})

    if payload.variables:
        variables_text = json.dumps(payload.variables, indent=2, ensure_ascii=False)
        messages.append({"role": "system", "content": f"Variables:\n{variables_text}"})

    if payload.user_prompt.strip():
        messages.append({"role": "user", "content": payload.user_prompt.strip()})

    return messages


def build_prompt_preview(messages: Iterable[Message]) -> str:
    parts: list[str] = []
    for message in messages:
        role = message.get("role", "unknown").upper()
        content = message.get("content", "")
        parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts)
