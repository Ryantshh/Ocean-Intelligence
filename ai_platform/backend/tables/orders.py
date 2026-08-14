"""Filters and SQL for ``public.orders`` — cargo enquiries.

Geography is deliberately absent. ``load_zone`` and ``discharge_parent_zone``
hold comma-joined sets, and ports and cargo types vary in spelling, so all of
them are ranking problems rather than filtering ones and wait for the index.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.tables.base import ClauseBuilder, RangeSpec

TABLE = "orders"
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
which are null throughout. Wider than the filterable set on purpose: ports,
zones and cargo type cannot be filtered in SQL, but the table's own search box
lets a user scan them client-side, which is the only way to reach them until the
index exists.
"""

DISPLAY_NOUN = "cargoes"
"""What a row is called, for the results panel label."""

DISPLAY_DEFAULTS: dict[str, str] = {}
"""Values substituted for nulls before display. Nothing to fill on this table."""

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

Cargo types, commodities, ports and descriptions cannot be filtered."""


def build_sql(filters: Filters) -> tuple[str, list[Any]]:
    """Compile cargo filters into a parameterised SELECT.

    Laycan and weight bounds compare against the opposite column so an
    overlapping window matches: a cargo of 150,000-180,000 tonnes satisfies
    ``weight_min=160000`` because it can lift that stem.

    Parameters
    ----------
    filters : Filters
        Spec produced by the extraction node.

    Returns
    -------
    tuple
        ``(sql, params)`` for asyncpg.
    """
    builder = ClauseBuilder(TABLE, ORDER_BY)
    if filters.order_ids:
        builder.add(f"order_id = ANY({builder.bind(filters.order_ids)})")
    builder.add_ranges(filters, RANGES)
    return builder.compile()
