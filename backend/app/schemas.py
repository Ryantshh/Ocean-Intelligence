from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class SqlChatRequest(BaseModel):
    question: str = Field(min_length=1)


class SqlChatResponse(BaseModel):
    answer: str
    sql: str | None = None
    columns: list[str] | None = None
    rows: list[dict[str, Any]] | None = None
    row_count: int | None = None
    requires_sql: bool = True
