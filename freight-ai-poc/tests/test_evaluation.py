import json
from pathlib import Path

from freight_ai.config import load_config
from freight_ai.evaluation.runner import matrix, score_intent
from freight_ai.inference.backend import ScriptedBackend
from freight_ai.training.dataset import build_dataset


def test_metrics_detect_invalid_and_wrong():
    assert not score_intent("bad", {"action": "query"})["json_valid"]
    assert not score_intent('{"action":"invent"}', {"action": "query"})["schema_valid"]
    assert not score_intent('{"action":"qa"}', {"action": "query"})["exact_match"]
    assert score_intent('{"action":"query"}', {"action": "query"})["exact_match"]


def test_six_cells_same_heldout(tmp_path):
    config = load_config(Path(__file__).resolve().parents[1] / "configs/default.yaml")
    config["training_data"] = str(tmp_path / "data")
    build_dataset(config["training_data"])
    report = matrix(
        config,
        tmp_path / "eval.json",
        adapter="test-double-only",
        backend_factory=lambda cfg: ScriptedBackend('{"action":"query"}'),
    )
    assert len(report["runs"]) == 6
    for run in report["runs"].values():
        assert [p["id"] for p in run["predictions"]] == report["held_out_ids"]
        assert run["metrics"]["exact_match"] < 1
    assert json.loads((tmp_path / "eval.json").read_text())["split"] == "test"
