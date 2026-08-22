"""Filters and SQL for ``public.order_test`` — cargo enquiries.

Geography and commodity are absent from the filters on purpose. ``load_zone`` and
``discharge_parent_zone`` hold comma-joined sets, and ports and cargo types vary
in spelling, so exact matching on them misses. They are reached by similarity
instead, through :class:`SemanticTerms`.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.tables.base import RangeSpec, StatementBuilder

TABLE = "order_test"
ORDER_BY = "update_date DESC"
"""Newest amendment first.

``update_date`` is the only record timestamp that is set on every row and carries
a real clock time. ``laycan_start`` is a business window stored at midnight, so
sorting on it puts stale enquiries above fresh ones.
"""

DISPLAY_COLUMNS: tuple[str, ...] = (
    "order_id",
    "cargo_type",
    "cargo_weight_min",
    "cargo_weight_max",
    "load_port",
    "load_zone",
    "discharge_port",
    "discharge_parent_zone",
    "laycan_start",
    "laycan_end",
    "date_received",
    "update_date",
    "cargo_description",
)
"""Columns shown in the results table, in reading order.

Every column the table holds except ``assigned`` and ``assigned_vessel_name``,
which are null throughout. Embedding columns are excluded: at 512 floats a row
they are 65x the payload and nothing on screen reads them.
"""

SEMANTIC_COLUMNS: tuple[str, ...] = (
    "cargo_type",
    "cargo_description",
    "load_port",
    "load_zone",
    "discharge_port",
    "discharge_parent_zone",
)
"""Free-text columns carrying a ``{name}_embedding`` vector alongside them."""

DISPLAY_NOUN = "cargoes"
"""What a row is called, for the results panel label."""

DISPLAY_DEFAULTS: dict[str, str] = {}
"""Values substituted for nulls before display. Nothing to fill on this table."""

# bounds compare against the opposite column so an overlapping window matches
RANGES: tuple[RangeSpec, ...] = (
    RangeSpec("laycan_from", "laycan_end", ">="),
    RangeSpec("laycan_to", "laycan_start", "<="),
    RangeSpec("received_from", "date_received", ">="),
    RangeSpec("received_to", "date_received", "<="),
    RangeSpec("updated_from", "update_date", ">="),
    RangeSpec("updated_to", "update_date", "<="),
    RangeSpec("weight_min", "cargo_weight_max", ">="),
    RangeSpec("weight_max", "cargo_weight_min", "<="),
)



class Filters(BaseModel):
    """Deterministic filter over cargo enquiries.

    Unknown fields are rejected rather than dropped. Pydantic ignores extras by
    default, which would silently discard a tonnage field aimed at this table
    and return unfiltered rows that look like an answer.
    """

    model_config = ConfigDict(extra="forbid")

    order_ids: list[int] = Field(
        default_factory=list, description="exact order numbers, when quoted"
    )
    laycan_from: date | None = Field(default=None, description="laycan window start")
    laycan_to: date | None = Field(default=None, description="laycan window end")
    received_from: date | None = Field(default=None, description="order received on or after")
    received_to: date | None = Field(default=None, description="order received on or before")
    updated_from: date | None = Field(default=None, description="last updated on or after")
    updated_to: date | None = Field(default=None, description="last updated on or before")
    weight_min: float | None = Field(default=None, description="cargo tonnes, lower bound")
    weight_max: float | None = Field(default=None, description="cargo tonnes, upper bound")


class SemanticTerms(BaseModel):
    """Free-text terms matched by similarity rather than equality.

    Every field is optional. Null means the question did not name that column.
    """

    model_config = ConfigDict(extra="forbid")

    cargo_type: str | None = Field(default=None, description="commodity, e.g. iron ore")
    cargo_description: str | None = Field(
        default=None, description="wording from the enquiry itself"
    )
    load_port: str | None = Field(default=None, description="named load port")
    load_zone: str | None = Field(default=None, description="load region or country")
    discharge_port: str | None = Field(default=None, description="named discharge port")
    discharge_parent_zone: str | None = Field(
        default=None, description="discharge region or country"
    )


FIELD_GUIDE = """orders — cargo enquiries. Fields:
  order_ids       list of integers, only when the user quotes order numbers
  laycan_from     ISO date, laycan window start
  laycan_to       ISO date, laycan window end
  received_from   ISO date, when the enquiry arrived, on or after
  received_to     ISO date, when the enquiry arrived, on or before
  updated_from    ISO date, last amended on or after
  updated_to      ISO date, last amended on or before
  weight_min      cargo tonnes, lower bound
  weight_max      cargo tonnes, upper bound

Semantic fields, matched by meaning rather than exact text. Copy the user's
words in; do not normalise them:
  cargo_type              commodity, e.g. iron ore, coal, bauxite
  cargo_description       wording from the enquiry itself
  load_port               named load port
  load_zone               load region or country, e.g. Brazil, West Australia
  discharge_port          named discharge port
  discharge_parent_zone   discharge region or country"""


def build_sql(
    filters: Filters,
    term_vectors: Sequence[tuple[str, Sequence[float]]] = (),
) -> tuple[str, list[Any]]:
    """Compile cargo filters and query vectors into a parameterised SELECT.

    Laycan and weight bounds compare against the opposite column so an
    overlapping window matches: a cargo of 150,000-180,000 tonnes satisfies
    ``weight_min=160000`` because it can lift that stem.

    Parameters
    ----------
    filters : Filters
        Spec produced by the extraction node.
    vectors : Sequence of tuple
        ``(field, embedding)`` pairs. Fields not in ``SEMANTIC_COLUMNS`` are ignored.

    Returns
    -------
    tuple
        ``(sql, params)`` for asyncpg.
    """
    builder = StatementBuilder(TABLE, ORDER_BY, DISPLAY_COLUMNS)

    # exact comparisons first: these decide which rows are eligible at all
    if filters.order_ids:
        builder.add_clause(
            f"order_id = ANY({builder.bind_parameter(filters.order_ids)})"
        )
    builder.add_ranges(filters, RANGES)
    # a field the model invented is dropped rather than reaching a column name
    for field_name, term_vector in term_vectors:
        if field_name in SEMANTIC_COLUMNS:
            builder.order_by_similarity(f"{field_name}_embedding", term_vector)
    return builder.compile()
