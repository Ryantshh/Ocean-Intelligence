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
from ai_platform.backend.sql import EqualitySpec, MatchSpec, RangeSpec, StatementBuilder


class OrderSearch(BaseModel):
    """Every field a cargo search can set.

    Flat, with no filter-versus-semantic split, because that split is not the
    model's to make: it put laycan dates under semantic and the call was
    rejected. ``TableSpec.semantic_columns`` decides which of these are embedded
    and which become exact comparisons, in the code that knows the difference.

    Unknown fields are rejected rather than dropped. Pydantic ignores extras by
    default, which would silently discard a vessel field aimed at this table and
    return unfiltered rows that look like an answer.
    """

    model_config = ConfigDict(extra="forbid")

    order_ids: list[int] | None = Field(
        default=None, description="exact order numbers, when quoted"
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
    include_future: bool | None = Field(
        default=None, description="true only when the user asks about upcoming records"
    )
    exhaustive: bool | None = Field(
        default=None,
        description="true for every match rather than the closest fifty",
    )
    cargo_type: str | None = Field(default=None, description="stored cargo type, e.g. IRON ORE; a family name finds its members")
    cargo_description: str | None = Field(
        default=None, description="free-text wording from the enquiry, the one field searched by meaning"
    )
    load_port: str | None = Field(default=None, description="load port from the port list, stored spelling")
    load_zone: str | None = Field(default=None, description="load zone from the zone list, stored spelling")
    discharge_port: str | None = Field(
        default=None, description="discharge port from the port list, stored spelling"
    )
    discharge_parent_zone: str | None = Field(
        default=None, description="discharge zone from the zone list, stored spelling"
    )


class VesselSearch(BaseModel):
    """Every field a vessel search can set.

    Flat for the same reason as :class:`OrderSearch`.

    ``ship_size`` and ``ship_type`` are deliberately absent — every row is
    Capesize and 99.8% are Bulk Carriers, so filtering on them narrows nothing.
    ``dwt`` is the only real size discriminator.
    """

    model_config = ConfigDict(extra="forbid")

    vessel_ids: list[str] | None = Field(
        default=None, description="exact vessel identifiers, when quoted"
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
    include_history: bool | None = Field(
        default=None,
        description=(
            "true only when the user asks for history; returns every report of a "
            "vessel, which repeats the same position at different update times"
        ),
    )
    include_future: bool | None = Field(
        default=None, description="true only when the user asks about upcoming records"
    )
    exhaustive: bool | None = Field(
        default=None,
        description="true for every match rather than the closest fifty",
    )
    vessel_status: str | None = Field(
        default=None, description="navigational status from the status list, stored spelling"
    )
    parent_zone: str | None = Field(
        default=None, description="zone from the zone list, stored spelling"
    )
    open_area: str | None = Field(
        default=None, description="open port or area from the port list, stored spelling"
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
        Free-text columns carrying a ``{name}_embedding`` vector alongside them,
        searched by meaning. Only prose belongs here; names go in ``matches``.
    matches : tuple of MatchSpec
        Text columns matched per comma-separated element — exact for a closed
        vocabulary, prefix for a family, contains for a port name.
    ranges : tuple of RangeSpec
        Range comparisons this table offers.
    id_field : str
        Attribute on the filter model holding an exact-match id list. The column
        it matches is the same name without the plural: ``order_ids`` to
        ``order_id``.
    equalities : tuple of EqualitySpec
        Exact matches this table offers. Empty for tables with none.
    latest_key : str or None
        Column identifying one entity across repeated reports. None when the
        table already holds one row per entity.
    latest_order : str
        ORDER BY deciding which report wins, applied within ``latest_key``.
    """

    table: str
    order_by: str
    display_columns: tuple[str, ...]
    display_noun: str
    semantic_columns: tuple[str, ...]
    ranges: tuple[RangeSpec, ...]
    id_field: str
    display_defaults: dict[str, str] = field(default_factory=dict)
    matches: tuple[MatchSpec, ...] = ()
    equalities: tuple[EqualitySpec, ...] = ()
    latest_key: str | None = None
    latest_order: str = ""

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
        builder.exhaustive = getattr(filters, "exhaustive", False)
        if not getattr(filters, "include_future", False):
            builder.set_horizon("update_date", working_date())

        # exact comparisons first: these decide which rows are eligible at all
        identifiers = getattr(filters, self.id_field)
        if identifiers:
            column = self.id_field.removesuffix("s")
            builder.clauses.append(
                f"{column} = ANY({builder.bind_parameter(identifiers)})"
            )
        builder.add_ranges(filters, self.ranges)
        builder.add_equalities(filters, self.equalities)
        builder.add_matches(filters, self.matches)

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
    semantic_columns=("cargo_description",),
    matches=(
        MatchSpec("load_zone", "load_zone", "exact"),
        MatchSpec("discharge_parent_zone", "discharge_parent_zone", "exact"),
        MatchSpec("cargo_type", "cargo_type", "prefix"),
        MatchSpec("load_port", "load_port", "contains"),
        MatchSpec("discharge_port", "discharge_port", "contains"),
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
    semantic_columns=(),
    matches=(
        MatchSpec("parent_zone", "parent_zone", "exact"),
        MatchSpec("vessel_status", "vessel_status", "exact"),
        MatchSpec("open_area", "open_area", "contains"),
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
    latest_key="vessel_id",
    latest_order="update_date DESC, first_date_received DESC",
)


class Search(BaseModel):
    """One search request, over either table or both.

    Both halves optional. Asking for both in one call runs them concurrently,
    which is why they are here together rather than as two tools — a question
    naming cargoes and vessels costs one round trip instead of two.
    """

    model_config = ConfigDict(extra="forbid")

    cargoes: OrderSearch | None = Field(
        default=None, description="search cargo enquiries; omit to skip"
    )
    vessels: VesselSearch | None = Field(
        default=None, description="search vessel positions; omit to skip"
    )


TABLES: dict[str, TableSpec] = {"orders": ORDERS, "tonnage": TONNAGE}
"""Specs by the name the search tools use."""


def resolve_table(target: str) -> TableSpec:
    """Look up a spec by table name.

    Parameters
    ----------
    target : str
        ``orders`` or ``tonnage``.

    Returns
    -------
    TableSpec
        That table's spec.

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
