import json
from datetime import date
from pathlib import Path

import pytest
from freight_ai.config import load_config
from freight_ai.data.ingest import preprocess
from freight_ai.data.models import Intent
from freight_ai.evaluation.tools import result_signature
from freight_ai.matching.engine import MatchingConfig
from freight_ai.service import current_records, execute


def test_manual_source_acceptance_cases_and_tampering(tmp_path):
    root = Path(__file__).resolve().parents[1]
    config = load_config(root / "configs/default.yaml")
    config["processed"] = str(tmp_path)
    preprocess(config["sources"], tmp_path)
    records = current_records(config)
    for case in json.loads((root / "configs/tool_cases.json").read_text()):
        result = execute(
            Intent(**case["expected"]), records, MatchingConfig(), date(2026, 9, 1)
        )
        assert (
            result_signature(result, case["expected_result"]) == case["expected_result"]
        )
    with pytest.raises(ValueError, match="ignore query constraints"):
        execute(
            Intent(action="qa", text_filters={"load_port": "X"}),
            records,
            MatchingConfig(),
            date(2026, 9, 1),
        )
    with (tmp_path / "orders.jsonl").open("a") as f:
        f.write("\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        current_records(config)
