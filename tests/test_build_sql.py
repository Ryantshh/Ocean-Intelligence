"""Self-check for the three statement shapes ``StatementBuilder.compile`` emits.

Runnable directly with ``uv run python tests/test_build_sql.py``. No database and
no API key: everything here is string assembly.
"""

from __future__ import annotations

from ai_platform.backend.tables import orders, tonnage
from ai_platform.backend.tables.base import MAX_RANKED_ROWS

QUERY_VECTOR = [0.1] * 512


def test_filters_only_have_no_limit() -> None:
    """A filter-only statement orders by recency and returns every match.

    Returns
    -------
    None
    """
    sql, params = orders.build_sql(orders.Filters(weight_min=160_000))
    assert "::vector" not in sql
    assert "LIMIT" not in sql
    assert sql.endswith(f"ORDER BY {orders.ORDER_BY}")
    assert params == [160_000]


def test_vectors_only_order_by_distance() -> None:
    """A vector-only statement matches every row and must therefore cap.

    Returns
    -------
    None
    """
    sql, params = orders.build_sql(orders.Filters(), [("load_zone", QUERY_VECTOR)])
    assert "WHERE TRUE" in sql
    assert "(load_zone_embedding <=> $1::vector)" in sql
    assert sql.endswith(f"LIMIT {MAX_RANKED_ROWS}")
    assert len(params) == 1


def test_filters_and_vectors_bind_in_order() -> None:
    """Filter parameters are bound before vectors, and distances sum.

    Returns
    -------
    None
    """
    sql, params = tonnage.build_sql(
        tonnage.Filters(dwt_min=185_000),
        [("parent_zone", QUERY_VECTOR), ("open_area", QUERY_VECTOR)],
    )
    assert "dwt >= $1" in sql
    assert (
        "ORDER BY (parent_zone_embedding <=> $2::vector) "
        "+ (open_area_embedding <=> $3::vector)" in sql
    )
    assert len(params) == 3


def test_unknown_semantic_field_never_reaches_sql() -> None:
    """A field the model invented is dropped rather than interpolated.

    Returns
    -------
    None
    """
    sql, params = orders.build_sql(orders.Filters(), [("order_id", QUERY_VECTOR)])
    assert "::vector" not in sql
    assert params == []


if __name__ == "__main__":
    for name, check in sorted(globals().items()):
        if name.startswith("test_"):
            check()
            print(f"ok  {name}")
