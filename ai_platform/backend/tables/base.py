"""Shared machinery for per-table filter modules.

Column names come from the spec tables in this package; values come from the
model but only ever as bound parameters. Nothing is interpolated into SQL, so a
bad extraction returns the wrong rows rather than executing anything.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel


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


class ClauseBuilder:
    """Accumulates WHERE clauses and their bound parameters in step."""

    def __init__(self, table: str, order_by: str) -> None:
        """Start an empty statement for one table.

        Parameters
        ----------
        table : str
            Table name. Comes from this package, never from the model.
        order_by : str
            Full ORDER BY clause including direction, e.g. ``update_date DESC``.
        """
        self.table = table
        self.order_by = order_by
        self.clauses: list[str] = []
        self.params: list[Any] = []

    def bind(self, value: Any) -> str:
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

    def add(self, clause: str) -> None:
        """Append a clause built by a caller.

        Parameters
        ----------
        clause : str
            SQL fragment, already using placeholders from :meth:`bind`.

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
                self.add(f"{spec.column} {spec.operator} {self.bind(value)}")

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
                self.add(f"{target} = {self.bind(value)}")

    def compile(self) -> tuple[str, list[Any]]:
        """Assemble the final statement.

        There is no LIMIT. The results table renders every match, and a cap on
        the way in would either hide rows from the user or hand the model an
        arbitrary slice to generalise from. A broad question therefore fails at
        the model rather than answering from a sample; retrieval narrowing is
        what fixes that, not a row cap.

        Returns
        -------
        tuple
            ``(sql, params)`` ready for asyncpg.
        """
        where = " AND ".join(self.clauses) if self.clauses else "TRUE"
        sql = (
            f"SELECT * FROM public.{self.table} WHERE {where} ORDER BY {self.order_by}"
        )
        return sql, self.params
