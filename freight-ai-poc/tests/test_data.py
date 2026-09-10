from pathlib import Path

import pytest
from freight_ai.data.ingest import ingest, inspect_sources, load_records, preprocess
from freight_ai.data.models import Intent

SOURCE = Path(__file__).resolve().parents[2] / "test_data"


def test_actual_sources(tmp_path):
    report = inspect_sources(SOURCE)
    assert [report[k]["rows"] for k in ("orders", "tonnage")] == [3, 3]
    preprocess(SOURCE, tmp_path)
    orders = load_records(tmp_path, "orders")
    vessels = load_records(tmp_path, "tonnage")
    assert len(orders) == len(vessels) == 3
    assert orders[2].cargo_weight_max is None
    assert vessels[1].commercial_status is None
    assert orders[0].laycan_start.isoformat() == "2026-09-01"
    assert vessels[0].eta_date_start.isoformat() == "2026-09-01"
    assert orders[0].record_id == ingest(SOURCE, "orders")[0][0].record_id


@pytest.mark.parametrize(
    "payload",
    [
        {"action": "query", "text_filters": {"dwt": "3"}},
        {"action": "query", "min_tonnes": 2, "max_tonnes": 1},
        {"action": "match"},
        {"action": "query", "sql": "DROP TABLE orders"},
    ],
)
def test_invalid_intent(payload):
    with pytest.raises(ValueError):
        Intent(**payload)
