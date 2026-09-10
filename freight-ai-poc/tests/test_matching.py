from datetime import date

import pytest
from freight_ai.data.ingest import ingest
from freight_ai.data.models import Intent
from freight_ai.matching.engine import Coordinate, MatchingConfig, haversine_nm, match
from freight_ai.matching.query import latest_vessels, query

from test_data import SOURCE


def test_actual_matching_conservative():
    orders, _ = ingest(SOURCE, "orders")
    vessels, _ = ingest(SOURCE, "tonnage")
    result = match(orders[0], vessels, MatchingConfig(), date(2026, 9, 1))
    assert result["candidate_count"] == 1
    assert result["candidates"][0]["vessel_name"] == "TEST VESSEL ALPHA"
    assert result["candidates"][0]["status"] == "needs_review"
    assert len(result["excluded"]) == 2
    assert all(not r["confirmed_match"] for r in result["candidates"])
    assert (
        match(
            orders[0], vessels, MatchingConfig(cargo_fraction=0.95), date(2026, 9, 1)
        )["candidate_count"]
        == 0
    )
    assert (
        match(orders[0], vessels, MatchingConfig(), date(2026, 9, 11))[
            "candidate_count"
        ]
        == 0
    )


def test_filters_and_aggregation():
    orders, _ = ingest(SOURCE, "orders")
    result = query(
        orders, Intent(action="query", text_filters={"load_port": " TUBARAO "})
    )
    assert result["total_count"] == 1
    result = query(orders, Intent(action="query", min_tonnes=66000, max_tonnes=66000))
    assert result["total_count"] == 1  # inclusive overlap at upper boundary
    result = query(orders, Intent(action="query", aggregation="sum_tonnes", limit=1))
    assert result["aggregation"]["cargo_weight_max"] == {
        "known_sum": 246000,
        "missing_count": 1,
    }
    assert result["truncated"]


def test_latest_future_and_conflicting_reports():
    vessels, _ = ingest(SOURCE, "tonnage")
    v = vessels[0]
    future = v.model_copy(
        update={"record_id": "future", "update_date": v.update_date.replace(year=2027)}
    )
    tie = v.model_copy(update={"record_id": "zz-conflict", "open_area": "Different"})
    rows, conflicts = latest_vessels([v, future, tie], date(2026, 9, 1))
    assert len(rows) == 1 and rows[0].record_id == "zz-conflict"
    assert conflicts == {"zz-conflict"}


def test_geography_and_config():
    a = Coordinate(latitude=0, longitude=0, source="test")
    b = Coordinate(latitude=0, longitude=1, source="test")
    assert haversine_nm(a, b) == pytest.approx(60.04, abs=0.01)
    with pytest.raises(ValueError):
        MatchingConfig(cargo_fraction=1.1)
    with pytest.raises(ValueError):
        MatchingConfig(weights={"capacity": 0, "window": 0, "geography": 0})
