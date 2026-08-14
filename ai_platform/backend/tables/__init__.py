"""Registry of queryable tables, and the schema the model is constrained to.

Each module exposes the same three names — ``Filters``, ``FIELD_GUIDE`` and
``build_sql`` — so callers resolve a target once and never branch on it.

``Extraction.request`` is a plain union, deliberately not a discriminated one.
Pydantic renders a discriminated union as ``oneOf``, which Groq's strict mode
rejects; a plain union renders as ``anyOf``, which it accepts. Discrimination
still happens during validation because each branch pins ``target`` to a literal.

Docstrings on these classes reach the model — pydantic copies them into the
schema as descriptions — so they stay short and describe the field rather than
the implementation. Notes like this one belong here, where they do not.
"""

from __future__ import annotations

from types import ModuleType
from typing import Literal

from openai.lib._pydantic import to_strict_json_schema
from openai.types.chat import completion_create_params
from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.tables import orders, tonnage

TABLES: dict[str, ModuleType] = {
    "orders": orders,
    "tonnage": tonnage,
}


class OrdersRequest(BaseModel):
    """A question resolved to the orders table."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["orders"]
    filters: orders.Filters


class TonnageRequest(BaseModel):
    """A question resolved to the tonnage table."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["tonnage"]
    filters: tonnage.Filters


class Extraction(BaseModel):
    """What to return: a table and its filters, or a question to ask instead."""

    model_config = ConfigDict(extra="forbid")

    request: OrdersRequest | TonnageRequest
    needs_clarification: bool = Field(
        description="true when nothing filterable was named"
    )
    clarifying_question: str | None = Field(
        description="one short question, null unless needs_clarification"
    )


EXTRACTION_RESPONSE_FORMAT: completion_create_params.ResponseFormat = {
    "type": "json_schema",
    "json_schema": {
        "name": "Extraction",
        "schema": to_strict_json_schema(Extraction),
        "strict": True,
    },
}
"""Constrains generation to :class:`Extraction`.

Built with the SDK's converter, not by hand. It sets ``additionalProperties:
false`` and lists every property in ``required``, both of which Groq demands and
neither of which pydantic emits on its own.

Because every field must be present, the prompt has to tell the model to write
null for anything unasked. Telling it to *omit* fields instead is a contradiction
the model resolves by inventing values.
"""




def resolve(target: str) -> ModuleType:
    """Look up the module handling a target table.

    Parameters
    ----------
    target : str
        Table name from the extraction step.

    Returns
    -------
    ModuleType
        Module exposing ``Filters``, ``FIELD_GUIDE`` and ``build_sql``.

    Raises
    ------
    ValueError
        If the target is not a known table.
    """
    try:
        return TABLES[target]
    except KeyError:
        raise ValueError(
            f"Unknown target table {target!r}. Expected one of {tuple(TABLES)}."
        ) from None


FIELD_GUIDES = "\n\n".join(module.FIELD_GUIDE for module in TABLES.values())
"""Every table's prompt fragment, joined.

Each table documents its own fields next to the model that validates them, so
the two cannot drift apart.
"""
