"""Shared machinery for per-table filter modules.

Column names come from the spec tables in this package; values come from the
model but only ever as bound parameters. Nothing is interpolated into SQL, so a
bad extraction returns the wrong rows rather than executing anything.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

MAX_RANKED_ROWS = 50
"""Rows kept when a similarity ordering is in play.

A WHERE clause is binary, so a filter-only query needs no cap. A distance
ordering matches every row in the table, so without one it returns the whole
table sorted.
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

    def __init__(self, table: str, order_by: str, columns: Sequence[str]) -> None:
        """Start an empty statement for one table.

        Parameters
        ----------
        table : str
            Table name. Comes from this package, never from the model.
        order_by : str
            Full ORDER BY clause including direction, e.g. ``update_date DESC``.
        columns : Sequence of str
            Columns to select. Names come from this package, never the model.
        """
        # the three lists are appended in step, so placeholder numbering cannot drift
        self.table = table
        self.order_by = order_by
        self.columns = tuple(columns)
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

        Without vectors there is no LIMIT: the results table renders every match,
        and a cap on the way in would either hide rows from the user or hand the
        model an arbitrary slice to generalise from. With vectors a cap is
        unavoidable, since every row has a distance. Several vectors sum their
        distances, weighting each named field equally.

        Returns
        -------
        tuple
            ``(sql, params)`` ready for asyncpg.
        """
        # no clauses means every row is eligible and the distance does all the narrowing
        where_clause = " AND ".join(self.clauses) if self.clauses else "TRUE"
        selected_columns = ", ".join(f'"{column}"' for column in self.columns)
        sql = f"SELECT {selected_columns} FROM public.{self.table} WHERE {where_clause}"

        # a distance ordering matches every row, so it is the only shape needing a cap
        if self.similarity_terms:
            distance_expression = " + ".join(
                f"({column} <=> {placeholder}::vector)"
                for column, placeholder in self.similarity_terms
            )
            sql += f" ORDER BY {distance_expression} LIMIT {MAX_RANKED_ROWS}"
        else:
            sql += f" ORDER BY {self.order_by}"
        return sql, self.params
