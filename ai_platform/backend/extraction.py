"""The strict schema the extraction node constrains generation to.

Temporary. This is everything the old ``StateGraph`` needs that the table specs
do not carry, kept in one file so the agent rewrite deletes it in one move rather
than unpicking it from ``tables.py``.

``Extraction.request`` is a plain union, deliberately not a discriminated one.
Pydantic renders a discriminated union as ``oneOf``, which Groq's strict mode
rejects; a plain union renders as ``anyOf``, which it accepts. Discrimination
still happens during validation because each branch pins ``target`` to a literal.

Docstrings on these classes reach the model — pydantic copies them into the
schema as descriptions — so they stay short and describe the field rather than
the implementation. Notes like this one belong here, where they do not.
"""

from __future__ import annotations

from typing import Literal

from openai.lib._pydantic import to_strict_json_schema
from openai.types.chat import completion_create_params
from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.tables import (
    ORDERS,
    TONNAGE,
    OrderFilters,
    OrderTerms,
    TableSpec,
    VesselFilters,
    VesselTerms,
)

TABLES: dict[str, TableSpec] = {"orders": ORDERS, "tonnage": TONNAGE}


class OrdersRequest(BaseModel):
    """A question resolved to the orders table."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["orders"]
    filters: OrderFilters
    semantic: OrderTerms


class TonnageRequest(BaseModel):
    """A question resolved to the tonnage table."""

    model_config = ConfigDict(extra="forbid")

    target: Literal["tonnage"]
    filters: VesselFilters
    semantic: VesselTerms


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


def resolve_table(target: str) -> TableSpec:
    """Look up the spec for a target table.

    Parameters
    ----------
    target : str
        Table name from the extraction step.

    Returns
    -------
    TableSpec
        Spec carrying that table's columns, ranges and ``build_sql``.

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


FIELD_GUIDES = "\n\n".join(spec.guide for spec in TABLES.values())
"""Every table's prompt fragment, joined.

Each table documents its own fields next to the model that validates them, so
the two cannot drift apart.
"""
