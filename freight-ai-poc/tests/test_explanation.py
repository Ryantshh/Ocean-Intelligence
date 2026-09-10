import pytest
from freight_ai.inference.explanation import EvidenceSelection, render_evidence


def test_explanation_rejects_invented_evidence_and_preserves_tonnes():
    result = {
        "total_count": 1,
        "records": [
            {
                "record_id": "order-1",
                "load_port": "A",
                "discharge_port": "B",
                "cargo_type": "ORE",
                "cargo_weight_min": 170000,
                "cargo_weight_max": 180000,
                "laycan_start": "2026-09-01",
                "laycan_end": "2026-09-10",
            }
        ],
    }
    rendered = render_evidence(result, EvidenceSelection(record_ids=["order-1"]))
    assert "170000–180000 metric tonnes" in rendered
    assert "loaded" not in rendered and "kg" not in rendered
    with pytest.raises(ValueError, match="absent"):
        render_evidence(result, EvidenceSelection(record_ids=["invented"]))
    with pytest.raises(ValueError):
        EvidenceSelection.model_validate_json('{"narrative":"It loaded yesterday"}')
