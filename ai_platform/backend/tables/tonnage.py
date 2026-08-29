"""Filters and SQL for ``public.tonnage_test`` — vessel positions.

Geography is absent from the filters on purpose: ``parent_zone`` is comma-joined
and ``open_area`` too granular, so exact matching on either misses.
``destination`` is clean but open-ended and goes the same way. All three are
reached by similarity instead, through :class:`SemanticTerms`.

``ship_size`` and ``ship_type`` are not offered either — every row is Capesize
and 99.8% are Bulk Carriers, so filtering on them narrows nothing. ``dwt`` is
the only real size discriminator.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.clock import working_date
from ai_platform.backend.tables.base import (
    EqualitySpec,
    RangeSpec,
    StatementBuilder,
)

TABLE = "tonnage_test"
ORDER_BY = "update_date DESC"
"""Newest amendment first.

``update_date`` is the only record timestamp that is set on every row and carries
a real clock time. ``open_date_start`` is a business window stored at midnight, so
sorting on it puts stale positions above fresh ones.
"""

DISPLAY_COLUMNS: tuple[str, ...] = (
    "vessel_id",
    "dwt",
    "ship_size",
    "ship_type",
    "vessel_status",
    "ballast_laden",
    "commercial_status",
    "open_area",
    "parent_zone",
    "open_date_start",
    "open_date_end",
    "destination",
    "eta",
    "update_date",
    "first_date_received",
    "order_id",
)
"""Columns shown in the results table, in reading order.

Every column the table holds except ``assignment``, which is null throughout.
Embedding columns are excluded: at 512 floats a row they are 65x the payload and
nothing on screen reads them.
"""

SEMANTIC_COLUMNS: tuple[str, ...] = (
    "vessel_status",
    "parent_zone",
    "open_area",
)
"""Free-text columns carrying a ``{name}_embedding`` vector alongside them."""

LATEST_KEY = "vessel_id"
"""Column identifying one ship across its repeated position reports."""

LATEST_ORDER = "update_date DESC, first_date_received DESC"
"""Which report wins per vessel.

92 vessels carry two reports stamped with the same ``update_date``, differing in
open area and dates. The report received later is the newer information.
"""

DISPLAY_NOUN = "vessels"
"""What a row is called, for the results panel label."""

DISPLAY_DEFAULTS: dict[str, str] = {"commercial_status": "AVAILABLE"}
"""Values substituted for nulls before display.

A null ``commercial_status`` means unfixed, which is 79% of the fleet. Left
alone the column reads empty for four rows in five and looks like missing data
rather than the most important thing on it.
"""

# bounds compare against the opposite column so an overlapping window matches
RANGES: tuple[RangeSpec, ...] = (
    RangeSpec("open_from", "open_date_end", ">="),
    RangeSpec("open_to", "open_date_start", "<="),
    RangeSpec("open_start_from", "open_date_start", ">="),
    RangeSpec("open_start_to", "open_date_start", "<="),
    RangeSpec("open_end_to", "open_date_end", "<="),
    RangeSpec("updated_from", "update_date", ">="),
    RangeSpec("updated_to", "update_date", "<="),
    RangeSpec("received_from", "first_date_received", ">="),
    RangeSpec("received_to", "first_date_received", "<="),
    RangeSpec("dwt_min", "dwt", ">="),
    RangeSpec("dwt_max", "dwt", "<="),
)

# a null commercial_status means unfixed, which is 79% of the fleet
EQUALITIES: tuple[EqualitySpec, ...] = (
    EqualitySpec("ballast_laden", "ballast_laden"),
    EqualitySpec(
        "commercial_status", "commercial_status", "COALESCE({column}, 'AVAILABLE')"
    ),
)


class Filters(BaseModel):
    """Deterministic filter over vessel positions.

    Unknown fields are rejected rather than dropped. Pydantic ignores extras by
    default, which would silently discard an orders field aimed at this table
    and return unfiltered rows that look like an answer.
    """

    model_config = ConfigDict(extra="forbid")

    vessel_ids: list[str] = Field(
        default_factory=list, description="exact vessel identifiers, when quoted"
    )
    open_from: date | None = Field(
        default=None, description="available at any point on or after"
    )
    open_to: date | None = Field(
        default=None, description="available at any point on or before"
    )
    open_start_from: date | None = Field(
        default=None, description="first free date on or after"
    )
    open_start_to: date | None = Field(
        default=None, description="first free date on or before"
    )
    open_end_to: date | None = Field(
        default=None, description="open window closes on or before"
    )
    updated_from: date | None = Field(
        default=None, description="position last updated on or after"
    )
    updated_to: date | None = Field(
        default=None, description="position last updated on or before"
    )
    received_from: date | None = Field(
        default=None, description="position first reported on or after"
    )
    received_to: date | None = Field(
        default=None, description="position first reported on or before"
    )
    dwt_min: float | None = Field(default=None, description="deadweight, lower bound")
    dwt_max: float | None = Field(default=None, description="deadweight, upper bound")
    ballast_laden: Literal["LADEN", "BALLAST"] | None = None
    commercial_status: Literal["FIXED", "ON SUBS", "AVAILABLE"] | None = None
    include_history: bool = Field(
        default=False, description="true only when past positions were asked for"
    )
    include_future: bool = Field(
        default=False, description="true only when the user asks about upcoming records"
    )


class SemanticTerms(BaseModel):
    """Free-text terms matched by similarity rather than equality.

    Every field is optional. Null means the question did not name that column.
    """

    model_config = ConfigDict(extra="forbid")

    vessel_status: str | None = Field(
        default=None, description="navigational status, from the status list"
    )
    parent_zone: str | None = Field(
        default=None, description="broad region, from the zone list"
    )
    open_area: str | None = Field(
        default=None, description="specific open area, port or country"
    )


FIELD_GUIDE = """tonnage — vessel positions. Fields:
  vessel_ids        list of strings, copied verbatim including the prefix, e.g.
                    "VESSEL 0001". Never strip the word VESSEL or drop leading zeros
  open_from         ISO date, available at any point on or after
  open_to           ISO date, available at any point on or before
  open_start_from   ISO date, first free date on or after
  open_start_to     ISO date, first free date on or before
  open_end_to       ISO date, open window closes on or before — must be fixed by then
  updated_from      ISO date, position last updated on or after
  updated_to        ISO date, position last updated on or before
  received_from     ISO date, position first reported on or after
  received_to       ISO date, position first reported on or before
  dwt_min           deadweight tonnes, lower bound
  dwt_max           deadweight tonnes, upper bound
  ballast_laden     LADEN or BALLAST
  commercial_status FIXED, ON SUBS, or AVAILABLE. Unfixed vessels are AVAILABLE
  include_history   true only when the user asks for past positions or history
  include_future    true only when the user asks about upcoming or forward-dated records

Every vessel here is Capesize, dwt 160,000 to 190,000. Smaller classes do not
appear at all. Ship type and ship size cannot be filtered or searched.

Semantic fields:
  vessel_status     navigational status, from the status list only
  open_area         specific open area, port or country
  parent_zone       broad region containing it, from the zone list only"""


def build_sql(
    filters: Filters,
    term_vectors: Sequence[tuple[str, Sequence[float]]] = (),
) -> tuple[str, list[Any]]:
    """Compile vessel filters and query vectors into a parameterised SELECT.

    Open dates compare against the opposite column so an overlapping window
    matches: a vessel free 25 Sep to 15 Oct satisfies an October query.

    Each vessel is reduced to its newest report before filtering, unless
    ``include_history`` is set. A vessel whose July report is superseded by an
    August one therefore does not answer a July question.

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
    builder = StatementBuilder(
        TABLE, ORDER_BY, DISPLAY_COLUMNS, LATEST_KEY, LATEST_ORDER
    )
    builder.include_history = filters.include_history
    if not filters.include_future:
        builder.set_horizon("update_date", working_date())

    # exact comparisons first: these decide which rows are eligible at all
    if filters.vessel_ids:
        builder.add_clause(
            f"vessel_id = ANY({builder.bind_parameter(filters.vessel_ids)})"
        )
    builder.add_ranges(filters, RANGES)
    builder.add_equalities(filters, EQUALITIES)
    # a field the model invented is dropped rather than reaching a column name
    for field_name, term_vector in term_vectors:
        if field_name in SEMANTIC_COLUMNS:
            builder.order_by_similarity(f"{field_name}_embedding", term_vector)
    return builder.compile()
