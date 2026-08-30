"""The two queryable tables, their filters and their free-text terms.

Both specs live here rather than in a module each, because they are parallel by
design and the asymmetries only read as deliberate when they are side by side:
orders has no equalities, tonnage has no weight bounds, and only tonnage carries
repeated reports that need deduplicating.

Geography and commodity are absent from both filter models on purpose. Zones hold
comma-joined sets and ports vary in spelling, so exact matching on them misses.
They are reached by similarity instead.

Some choices worth stating once:

``update_date DESC`` orders both tables because it is the only record timestamp
set on every row that carries a real clock time. ``laycan_start`` and
``open_date_start`` are business windows stored at midnight, so sorting on either
puts stale records above fresh ones.

``LATEST_ORDER`` breaks ties on ``first_date_received``: 92 vessels carry two
reports stamped with the same ``update_date`` and differing in open area, and the
report received later is the newer information.

Tonnage substitutes ``AVAILABLE`` for a null ``commercial_status`` before display.
A null means unfixed, which is 79% of the fleet, so left alone the column reads
empty for four rows in five and looks like missing data rather than the most
important thing on it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ai_platform.backend.clock import working_date
from ai_platform.backend.sql import EqualitySpec, RangeSpec, StatementBuilder


class OrderFilters(BaseModel):
    """Exact comparisons over cargo enquiries.

    Unknown fields are rejected rather than dropped. Pydantic ignores extras by
    default, which would silently discard a tonnage field aimed at this table and
    return unfiltered rows that look like an answer.
    """

    model_config = ConfigDict(extra="forbid")

    order_ids: list[int] = Field(
        default_factory=list, description="exact order numbers, when quoted"
    )
    laycan_start_from: date | None = Field(
        default=None, description="laycan first day on or after"
    )
    laycan_start_to: date | None = Field(
        default=None, description="laycan first day on or before"
    )
    laycan_end_from: date | None = Field(
        default=None, description="laycan cancelling on or after"
    )
    laycan_end_to: date | None = Field(
        default=None, description="laycan cancelling on or before"
    )
    received_from: date | None = Field(
        default=None, description="order received on or after"
    )
    received_to: date | None = Field(
        default=None, description="order received on or before"
    )
    updated_from: date | None = Field(default=None, description="last updated on or after")
    updated_to: date | None = Field(default=None, description="last updated on or before")
    weight_min: float | None = Field(
        default=None, description="whole stem at or above this, in tonnes"
    )
    weight_max: float | None = Field(
        default=None, description="whole stem at or below this, in tonnes"
    )
    include_future: bool = Field(
        default=False, description="true only when the user asks about upcoming records"
    )


class OrderTerms(BaseModel):
    """Free-text cargo terms matched by similarity rather than equality.

    Every field is optional. Null means the question did not name that column.
    """

    model_config = ConfigDict(extra="forbid")

    cargo_type: str | None = Field(default=None, description="commodity, e.g. iron ore")
    cargo_description: str | None = Field(
        default=None, description="wording from the enquiry itself"
    )
    load_port: str | None = Field(default=None, description="load port, terminal or country")
    load_zone: str | None = Field(default=None, description="load region, from the zone list")
    discharge_port: str | None = Field(
        default=None, description="discharge port, terminal or country"
    )
    discharge_parent_zone: str | None = Field(
        default=None, description="discharge region, from the zone list"
    )


class VesselFilters(BaseModel):
    """Exact comparisons over vessel positions.

    Unknown fields are rejected rather than dropped, for the same reason as
    :class:`OrderFilters`.

    ``ship_size`` and ``ship_type`` are deliberately absent — every row is
    Capesize and 99.8% are Bulk Carriers, so filtering on them narrows nothing.
    ``dwt`` is the only real size discriminator.
    """

    model_config = ConfigDict(extra="forbid")

    vessel_ids: list[str] = Field(
        default_factory=list, description="exact vessel identifiers, when quoted"
    )
    open_start_from: date | None = Field(
        default=None, description="first free date on or after"
    )
    open_start_to: date | None = Field(
        default=None, description="first free date on or before"
    )
    open_end_from: date | None = Field(
        default=None, description="open window closes on or after"
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
    ballast_laden: Literal["LADEN", "BALLAST"] | None = Field(
        default=None, description="sailing empty or with cargo"
    )
    commercial_status: Literal["FIXED", "ON SUBS", "AVAILABLE"] | None = Field(
        default=None, description="fixture status; unfixed vessels are AVAILABLE"
    )
    include_history: bool = Field(
        default=False, description="true only when past positions were asked for"
    )
    include_future: bool = Field(
        default=False, description="true only when the user asks about upcoming records"
    )


class VesselTerms(BaseModel):
    """Free-text vessel terms matched by similarity rather than equality.

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


@dataclass(frozen=True)
class TableSpec:
    """One queryable table and everything needed to search it.

    Attributes
    ----------
    table : str
        Table name in the ``public`` schema.
    order_by : str
        ORDER BY clause used when no similarity ordering applies.
    display_columns : tuple of str
        Columns selected, in reading order. Embedding columns are excluded: at
        512 floats a row they are 65x the payload and nothing on screen reads
        them.
    display_noun : str
        What a row is called, for the results panel label.
    display_defaults : dict of str to str
        Values substituted for nulls before display.
    semantic_columns : tuple of str
        Free-text columns carrying a ``{name}_embedding`` vector alongside them.
    ranges : tuple of RangeSpec
        Range comparisons this table offers.
    id_field : str
        Attribute on the filter model holding an exact-match id list.
    id_column : str
        Column that list matches against.
    equalities : tuple of EqualitySpec
        Exact matches this table offers. Empty for tables with none.
    latest_key : str or None
        Column identifying one entity across repeated reports. None when the
        table already holds one row per entity.
    latest_order : str
        ORDER BY deciding which report wins, applied within ``latest_key``.
    guide : str
        Field documentation the model reads when filling a search.
    """

    table: str
    order_by: str
    display_columns: tuple[str, ...]
    display_noun: str
    semantic_columns: tuple[str, ...]
    ranges: tuple[RangeSpec, ...]
    id_field: str
    id_column: str
    display_defaults: dict[str, str] = field(default_factory=dict)
    equalities: tuple[EqualitySpec, ...] = ()
    latest_key: str | None = None
    latest_order: str = ""
    guide: str = ""

    def build_sql(
        self,
        filters: BaseModel,
        term_vectors: Sequence[tuple[str, Sequence[float]]] = (),
    ) -> tuple[str, list[Any]]:
        """Compile filters and query vectors into a parameterised SELECT.

        Weight and deadweight bounds contain rather than overlap:
        ``weight_min=160000`` matches only stems whose own minimum reaches
        160,000, so a cargo of 153,000-187,000 is excluded even though it could
        load that size. "At least" is read literally.

        Where ``latest_key`` is set, each entity is reduced to its newest report
        before filtering. A vessel whose July report is superseded by an August
        one therefore does not answer a July question.

        Parameters
        ----------
        filters : BaseModel
            This table's filter model.
        term_vectors : Sequence of tuple
            ``(field, embedding)`` pairs. Fields not in ``semantic_columns`` are
            ignored.

        Returns
        -------
        tuple
            ``(sql, params)`` for asyncpg.
        """
        builder = StatementBuilder(
            self.table,
            self.order_by,
            self.display_columns,
            self.latest_key,
            self.latest_order,
        )
        builder.include_history = getattr(filters, "include_history", False)
        if not getattr(filters, "include_future", False):
            builder.set_horizon("update_date", working_date())

        # exact comparisons first: these decide which rows are eligible at all
        identifiers = getattr(filters, self.id_field)
        if identifiers:
            builder.add_clause(
                f"{self.id_column} = ANY({builder.bind_parameter(identifiers)})"
            )
        builder.add_ranges(filters, self.ranges)
        builder.add_equalities(filters, self.equalities)

        # a field the model invented is dropped rather than reaching a column name
        for field_name, term_vector in term_vectors:
            if field_name in self.semantic_columns:
                builder.order_by_similarity(f"{field_name}_embedding", term_vector)
        return builder.compile()


ORDERS = TableSpec(
    table="order_test",
    order_by="update_date DESC",
    display_columns=(
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
    ),
    display_noun="cargoes",
    semantic_columns=(
        "cargo_type",
        "cargo_description",
        "load_port",
        "load_zone",
        "discharge_port",
        "discharge_parent_zone",
    ),
    ranges=(
        RangeSpec("laycan_start_from", "laycan_start", ">="),
        RangeSpec("laycan_start_to", "laycan_start", "<="),
        RangeSpec("laycan_end_from", "laycan_end", ">="),
        RangeSpec("laycan_end_to", "laycan_end", "<="),
        RangeSpec("received_from", "date_received", ">="),
        RangeSpec("received_to", "date_received", "<="),
        RangeSpec("updated_from", "update_date", ">="),
        RangeSpec("updated_to", "update_date", "<="),
        RangeSpec("weight_min", "cargo_weight_min", ">="),
        RangeSpec("weight_max", "cargo_weight_max", "<="),
    ),
    id_field="order_ids",
    id_column="order_id",
    guide="""orders — cargo enquiries. Fields:
  order_ids       list of integers, only when the user quotes order numbers
  laycan_start_from ISO date, laycan first day on or after
  laycan_start_to   ISO date, laycan first day on or before
  laycan_end_from   ISO date, laycan cancels on or after — still open at that date
  laycan_end_to     ISO date, laycan cancels on or before — must be fixed by then
  received_from   ISO date, when the enquiry arrived, on or after
  received_to     ISO date, when the enquiry arrived, on or before
  updated_from    ISO date, last amended on or after
  updated_to      ISO date, last amended on or before
  weight_min      cargo tonnes, floor. The stem's smallest size must reach it, so
                  a cargo of 153,000-187,000 does not answer "at least 160,000"
  weight_max      cargo tonnes, ceiling. The stem's largest size must fit under it
  include_future  true only when the user asks about upcoming or forward-dated records

Semantic fields:
  cargo_type              commodity, e.g. iron ore, coal, bauxite
  cargo_description       wording from the enquiry itself
  load_port               load port, terminal or country
  load_zone               broad load region containing it, from the zone list
  discharge_port          discharge port, terminal or country
  discharge_parent_zone   broad discharge region containing it, from the zone list""",
)


TONNAGE = TableSpec(
    table="tonnage_test",
    order_by="update_date DESC",
    display_columns=(
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
    ),
    display_noun="vessels",
    display_defaults={"commercial_status": "AVAILABLE"},
    semantic_columns=(
        "vessel_status",
        "parent_zone",
        "open_area",
    ),
    ranges=(
        RangeSpec("open_start_from", "open_date_start", ">="),
        RangeSpec("open_start_to", "open_date_start", "<="),
        RangeSpec("open_end_from", "open_date_end", ">="),
        RangeSpec("open_end_to", "open_date_end", "<="),
        RangeSpec("updated_from", "update_date", ">="),
        RangeSpec("updated_to", "update_date", "<="),
        RangeSpec("received_from", "first_date_received", ">="),
        RangeSpec("received_to", "first_date_received", "<="),
        RangeSpec("dwt_min", "dwt", ">="),
        RangeSpec("dwt_max", "dwt", "<="),
    ),
    equalities=(
        EqualitySpec("ballast_laden", "ballast_laden"),
        EqualitySpec(
            "commercial_status", "commercial_status", "COALESCE({column}, 'AVAILABLE')"
        ),
    ),
    id_field="vessel_ids",
    id_column="vessel_id",
    latest_key="vessel_id",
    latest_order="update_date DESC, first_date_received DESC",
    guide="""tonnage — vessel positions. Fields:
  vessel_ids        list of strings, copied verbatim including the prefix, e.g.
                    "VESSEL 0001". Never strip the word VESSEL or drop leading zeros
  open_start_from   ISO date, first free date on or after
  open_start_to     ISO date, first free date on or before
  open_end_from     ISO date, window closes on or after — still open at that date
  open_end_to       ISO date, window closes on or before — must be fixed by then
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
  parent_zone       broad region containing it, from the zone list only""",
)


class OrderSearch(OrderFilters, OrderTerms):
    """Every cargo search field in one flat object.

    The agent fills one object rather than choosing between a filters nest and a
    semantic nest — a distinction it got wrong, putting laycan dates under
    semantic where they are rejected. ``TableSpec.build_sql`` reads the exact
    fields it knows and ignores the rest, so the split happens in the code that
    understands it.
    """


class VesselSearch(VesselFilters, VesselTerms):
    """Every vessel search field in one flat object."""
