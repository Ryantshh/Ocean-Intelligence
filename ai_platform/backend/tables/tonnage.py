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
    "destination",
    "parent_zone",
    "open_area",
)
"""Free-text columns carrying a ``{name}_embedding`` vector alongside them."""

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
    RangeSpec("eta_from", "eta", ">="),
    RangeSpec("eta_to", "eta", "<="),
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
    open_from: date | None = Field(default=None, description="open window start")
    open_to: date | None = Field(default=None, description="open window end")
    eta_from: date | None = Field(default=None, description="ETA on or after")
    eta_to: date | None = Field(default=None, description="ETA on or before")
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


class SemanticTerms(BaseModel):
    """Free-text terms matched by similarity rather than equality.

    Every field is optional. Null means the question did not name that column.
    """

    model_config = ConfigDict(extra="forbid")

    vessel_status: str | None = Field(
        default=None, description="navigational status wording"
    )
    destination: str | None = Field(default=None, description="where the vessel is bound")
    parent_zone: str | None = Field(
        default=None, description="broad region the vessel opens in"
    )
    open_area: str | None = Field(default=None, description="specific open area or port")


FIELD_GUIDE = """tonnage — vessel positions. Fields:
  vessel_ids        list of strings, copied verbatim including the prefix, e.g.
                    "VESSEL 0001". Never strip the word VESSEL or drop leading zeros
  open_from         ISO date, vessel free on or after
  open_to           ISO date, vessel free on or before
  eta_from          ISO date, arriving on or after
  eta_to            ISO date, arriving on or before
  updated_from      ISO date, position last updated on or after
  updated_to        ISO date, position last updated on or before
  received_from     ISO date, position first reported on or after
  received_to       ISO date, position first reported on or before
  dwt_min           deadweight tonnes, lower bound
  dwt_max           deadweight tonnes, upper bound
  ballast_laden     LADEN or BALLAST
  commercial_status FIXED, ON SUBS, or AVAILABLE. Unfixed vessels are AVAILABLE

Every vessel here is Capesize, dwt 160,000 to 190,000. Smaller classes do not
appear at all. Ship type and ship size cannot be filtered or searched.

Semantic fields, matched by meaning rather than exact text. Copy the user's
words in; do not normalise them:
  vessel_status     navigational status wording, e.g. underway, at anchor
  destination       where the vessel is bound
  parent_zone       broad region the vessel opens in, e.g. Far East, Atlantic
  open_area         specific open area or port"""


def build_sql(
    filters: Filters,
    term_vectors: Sequence[tuple[str, Sequence[float]]] = (),
) -> tuple[str, list[Any]]:
    """Compile vessel filters and query vectors into a parameterised SELECT.

    Open dates compare against the opposite column so an overlapping window
    matches: a vessel free 25 Sep to 15 Oct satisfies an October query.

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
