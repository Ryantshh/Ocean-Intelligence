import json
from datetime import date
from pathlib import Path

from freight_ai.config import load_config
from freight_ai.data.ingest import preprocess
from freight_ai.inference.chat import respond
from freight_ai.training.dataset import build_dataset, demonstrations


class FakeBackend:
    def __init__(self, *responses):
        self.responses = list(responses)

    def generate(self, messages):
        return self.responses.pop(0)


def config(tmp_path):
    value = load_config(Path(__file__).parents[1] / "configs/default.yaml")
    value["processed"] = str(tmp_path)
    preprocess(value["sources"], tmp_path)
    training = tmp_path / "training"
    build_dataset(training)
    value["training_data"] = str(training)
    return value


def test_general_shipping_question_returns_english_prose(tmp_path):
    backend = FakeBackend()
    result = respond(backend, "What is DWT?", config(tmp_path), date(2026, 9, 1))
    assert result["kind"] == "general"
    assert "Deadweight tonnage" in result["content"]
    assert not result["content"].startswith("{")


def test_canonical_concept_bypasses_unreliable_route_model(tmp_path):
    backend = FakeBackend()
    result = respond(
        backend,
        "What does laycan mean in a voyage charter?",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert result["kind"] == "general"
    assert result["diagnostics"]["routing"] == "curated project definition"


def test_dwt_uses_reviewed_definition_without_model_hallucination(tmp_path):
    result = respond(FakeBackend(), "What is DWT?", config(tmp_path), date(2026, 9, 1))
    assert "fuel" in result["content"]
    assert "usable cargo capacity" in result["content"]
    assert "gross tonnage minus net tonnage" not in result["content"]


def test_data_question_returns_detailed_source_grounded_answer(tmp_path):
    backend = FakeBackend(
        '{"route":"data","question":"List orders loading at Tubarao."}',
        json.dumps(
            {
                "action": "query",
                "dataset": "orders",
                "text_filters": {"load_port": "Tubarao"},
            }
        ),
    )
    cfg = config(tmp_path)
    result = respond(
        backend,
        "Which cargoes load at Tubarao?",
        cfg,
        date(2026, 9, 1),
        demonstrations=demonstrations(cfg["training_data"]),
    )
    assert result["kind"] == "data"
    assert "Tubarao" in result["content"]
    assert "metric tonnes" in result["content"]
    assert result["tool_result"]["total_count"] == 1


def test_route_failure_is_safe_clarification(tmp_path):
    result = respond(
        FakeBackend("not-json"), "Tell me something", config(tmp_path), date(2026, 9, 1)
    )
    assert result["kind"] == "clarification"
    assert "clarify" in result["content"].lower()


def test_ballast_laden_general_question_is_not_record_lookup(tmp_path):
    result = respond(
        FakeBackend(
            "Ballast means the vessel is sailing without cargo; laden means it is carrying cargo."
        ),
        "What is the difference between a ballast vessel and a laden vessel?",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert result["kind"] == "general"
    assert "without cargo" in result["content"]


def test_common_matching_question_bypasses_route_model(tmp_path):
    cfg = config(tmp_path)
    result = respond(
        FakeBackend(),
        "Which vessels could fit the iron ore order from Tubarao to Qingdao?",
        cfg,
        date(2026, 9, 1),
    )
    assert result["kind"] == "data"
    assert result["intent"]["action"] == "match"
    assert result["tool_result"]["candidate_count"] == 1


def test_percentage_persists_across_order_change_and_resets(tmp_path):
    cfg = config(tmp_path)
    first = respond(
        FakeBackend(), "Screen the Tubarao order at 90% of DWT.", cfg, date(2026, 9, 1)
    )
    second = respond(
        FakeBackend(),
        "Now screen the Santos order.",
        cfg,
        date(2026, 9, 1),
        conversation_state=first["conversation_state"],
    )
    assert second["conversation_state"]["cargo_fraction"] == 0.9
    for row in second["tool_result"]["candidates"] + second["tool_result"]["excluded"]:
        if row["assumed_capacity_tonnes"] is not None:
            from freight_ai.service import current_records

            vessel = next(
                v
                for v in current_records(cfg)["tonnage"]
                if v.record_id == row["vessel_record_id"]
            )
            assert row["assumed_capacity_tonnes"] == vessel.dwt * 0.9
    assert "90%" in second["content"]
    reset = respond(
        FakeBackend(),
        "Reset the capacity assumption.",
        cfg,
        date(2026, 9, 1),
        conversation_state=second["conversation_state"],
    )
    assert not reset["conversation_state"]["fraction_overridden"]
    invalid = respond(
        FakeBackend(),
        "Use 120% of DWT.",
        cfg,
        date(2026, 9, 1),
        conversation_state=second["conversation_state"],
    )
    assert invalid["kind"] == "clarification"
    assert invalid["conversation_state"]["cargo_fraction"] == 0.9


def test_negative_and_fractional_percentages():
    import pytest
    from freight_ai.inference.conversation import ConversationState

    with pytest.raises(ValueError):
        ConversationState().updated_assumption("Use -5% of DWT")
    assert (
        ConversationState().updated_assumption("Use 92.5% of DWT").cargo_fraction
        == 0.925
    )
