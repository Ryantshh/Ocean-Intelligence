"""Assembles one parameterised SELECT from a table spec and a filter.

Column names come from the table specs in ``tables.py``; values come from the
model but only ever as bound parameters. Nothing is interpolated into SQL, so a
bad extraction returns the wrong rows rather than executing anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any

from pydantic import BaseModel

MAX_RANKED_ROWS = 50
"""Backstop on a similarity ordering.

A WHERE clause is binary, so a filter-only query needs no cap. A distance
ordering matches every row in the table, so without one it returns the whole
table sorted. Reached only when ``DISTANCE_TOLERANCE`` admits more than this.
"""

DISTANCE_TOLERANCE = 1.15
"""How much further than the best match a row may sit and still be returned.

Relative rather than absolute because cosine distance is not calibrated: an exact
``IRON ORE`` match scores 0.29 and an exact ``West Australia`` 0.44, so no single
threshold serves both. A real match set separates sharply — West Australia's six
rows sit at 0.4365 and the next zone at 0.6216 — and anything from 1.05 to 1.4
cuts that cleanly. Free-text fields separate less, so this errs loose.
"""


@dataclass(frozen=True)
class RangeSpec:
    """One side of a range comparison.

    Attributes
    ----------
    field : str
        Attribute on the filter model holding the bound.
    column : str
        Database column to compare against. Deliberately crossed for windowed
        values — ``laycan_from`` compares to ``laycan_end`` so an overlapping
        window matches rather than only a contained one.
    operator : str
        ``>=`` or ``<=``.
    """

    field: str
    column: str
    operator: str


@dataclass(frozen=True)
class EqualitySpec:
    """An exact match on a single-valued column.

    Attributes
    ----------
    field : str
        Attribute on the filter model.
    column : str
        Database column.
    expression : str or None
        Optional SQL wrapping the column, with ``{column}`` as placeholder.
        Used for ``COALESCE(commercial_status, 'AVAILABLE')``.
    """

    field: str
    column: str
    expression: str | None = None


class StatementBuilder:
    """Accumulates WHERE clauses, similarity terms and their bound parameters in step."""

    def __init__(
        self,
        table: str,
        order_by: str,
        columns: Sequence[str],
        latest_key: str | None = None,
        latest_order: str = "",
    ) -> None:
        """Start an empty statement for one table.

        Parameters
        ----------
        table : str
            Table name. Comes from this package, never from the model.
        order_by : str
            Full ORDER BY clause including direction, e.g. ``update_date DESC``.
        columns : Sequence of str
            Columns to select. Names come from this package, never the model.
        latest_key : str or None
            Column identifying one real-world entity across repeated reports. Set
            it and every row but the newest per entity is dropped before filtering.
            None for tables already holding one row per entity.
        latest_order : str
            ORDER BY deciding which report is newest, applied within ``latest_key``.
            Required when ``latest_key`` is set.
        """
        # the three lists are appended in step, so placeholder numbering cannot drift
        self.table = table
        self.order_by = order_by
        self.columns = tuple(columns)
        self.latest_key = latest_key
        self.latest_order = latest_order
        self.include_history = False
        self.horizon: str = ""
        self.clauses: list[str] = []
        self.params: list[Any] = []
        self.similarity_terms: list[tuple[str, str]] = []

    def bind_parameter(self, value: Any) -> str:
        """Bind a value and return its placeholder.

        Appending and numbering happen together, so a clause can never
        reference a parameter that was not added.

        Parameters
        ----------
        value : Any
            Value to pass to the driver.

        Returns
        -------
        str
            Positional placeholder such as ``$3``.
        """
        self.params.append(value)
        return f"${len(self.params)}"

    def add_clause(self, clause: str) -> None:
        """Append a clause built by a caller.

        Parameters
        ----------
        clause : str
            SQL fragment, already using placeholders from :meth:`bind_parameter`.

        Returns
        -------
        None
        """
        self.clauses.append(clause)

    def add_ranges(self, filters: BaseModel, specs: tuple[RangeSpec, ...]) -> None:
        """Apply every range bound that was set.

        Parameters
        ----------
        filters : BaseModel
            The table's filter model.
        specs : tuple of RangeSpec
            Range definitions for that table.

        Returns
        -------
        None
        """
        for spec in specs:
            value = getattr(filters, spec.field, None)
            if value is not None:
                self.add_clause(
                    f"{spec.column} {spec.operator} {self.bind_parameter(value)}"
                )

    def add_equalities(self, filters: BaseModel, specs: tuple[EqualitySpec, ...]) -> None:
        """Apply every exact match that was set.

        Parameters
        ----------
        filters : BaseModel
            The table's filter model.
        specs : tuple of EqualitySpec
            Equality definitions for that table.

        Returns
        -------
        None
        """
        for spec in specs:
            value = getattr(filters, spec.field, None)
            if value is not None:
                target = (
                    spec.expression.format(column=spec.column)
                    if spec.expression
                    else spec.column
                )
                self.add_clause(f"{target} = {self.bind_parameter(value)}")

    def set_horizon(self, column: str, cutoff: date) -> None:
        """Hide rows stamped after a cutoff.

        Applied inside the newest-per-entity subquery when there is one, so the
        dedup picks the newest row that is not in the future rather than dropping
        the entity outright — 881 of 1,037 vessels carry a latest report beyond
        the working date.

        Parameters
        ----------
        column : str
            Timestamp column. Comes from this package, never from the model.
        cutoff : date
            Latest date a row may carry and still be returned.

        Returns
        -------
        None
        """
        self.horizon = f"{column} <= {self.bind_parameter(cutoff)}"

    def order_by_similarity(self, column: str, query_vector: Sequence[float]) -> None:
        """Order results by distance from a query vector.

        The vector is bound as its literal text and cast with ``::vector`` in the
        clause, which needs no type registration on the connection — ``fetch_rows``
        opens a fresh one per call, so there would be nowhere to register it.

        Parameters
        ----------
        column : str
            Embedding column. Comes from this package, never from the model.
        query_vector : Sequence of float
            Query-side embedding to compare against.

        Returns
        -------
        None
        """
        # postgres parses this text back into a vector, so no driver support is needed
        vector_literal = (
            "[" + ",".join(repr(float(value)) for value in query_vector) + "]"
        )
        self.similarity_terms.append((column, self.bind_parameter(vector_literal)))

    def compile(self) -> tuple[str, list[Any]]:
        """Assemble the final statement.

        Columns are listed rather than selected with ``*``. The gold tables carry
        a ``vector(512)`` column per embedded field, and ``*`` fetches all of them
        only for the caller to discard: 200 rows of ``order_test`` is 7.2M
        characters and 18 seconds that way, against 110k and 0.6s listed.

        A table with ``latest_key`` set is read through a subquery that keeps only
        the newest row per entity, so filters apply to current state rather than to
        any point in a history.

        Without vectors there is no LIMIT: the results table renders every match,
        and a cap on the way in would either hide rows from the user or hand the
        model an arbitrary slice to generalise from.

        With vectors every row has a distance, so the set is cut two ways: rows
        further than ``DISTANCE_TOLERANCE`` from the best are dropped, and
        ``MAX_RANKED_ROWS`` backstops whatever survives. Several vectors sum their
        distances, weighting each named field equally.

        Returns
        -------
        tuple
            ``(sql, params)`` ready for asyncpg.
        """
        selected_columns = ", ".join(f'"{column}"' for column in self.columns)

        if self.latest_key and not self.include_history:
            # embedding columns ride through the CTE so the outer ORDER BY can see
            # them, but stay out of the outer select: 512 floats a row, never read
            carried = ", ".join(
                f'"{column}"' for column, _ in self.similarity_terms
            )
            cte_columns = f"{selected_columns}, {carried}" if carried else selected_columns
            cutoff = f"WHERE {self.horizon} " if self.horizon else ""
            source = (
                f"(SELECT DISTINCT ON (\"{self.latest_key}\") {cte_columns} "
                f"FROM public.{self.table} {cutoff}"
                f"ORDER BY \"{self.latest_key}\", {self.latest_order}) AS latest"
            )
        else:
            source = f"public.{self.table}"
            if self.horizon:
                self.clauses.append(self.horizon)

        # computed after the source, since a table with no dedup takes the horizon here
        where_clause = " AND ".join(self.clauses) if self.clauses else "TRUE"

        if not self.similarity_terms:
            return (
                f"SELECT {selected_columns} FROM {source} "
                f"WHERE {where_clause} ORDER BY {self.order_by}"
            ), self.params

        # a distance ordering matches every row, so this is the only shape needing a cap
        distance_expression = " + ".join(
            f'("{column}" <=> {placeholder}::vector)'
            for column, placeholder in self.similarity_terms
        )
        sql = (
            f"WITH ranked AS (SELECT {selected_columns}, {distance_expression} "
            f"AS distance FROM {source} WHERE {where_clause}) "
            f"SELECT {selected_columns} FROM ranked "
            f"WHERE distance <= (SELECT min(distance) FROM ranked) * {DISTANCE_TOLERANCE} "
            f"ORDER BY distance LIMIT {MAX_RANKED_ROWS}"
        )
        return sql, self.params
