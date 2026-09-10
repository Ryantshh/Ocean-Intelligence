from datetime import date

from freight_ai.inference.chat import respond
from test_chat import FakeBackend, config


def test_unseen_numeric_threshold(tmp_path):
    answer = respond(
        FakeBackend(),
        "List vessels with at least 190,000 tonnes DWT.",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert answer["intent"]["dataset"] == "tonnage"
    assert answer["intent"]["min_tonnes"] == 190000
    assert all(r["dwt"] >= 190000 for r in answer["tool_result"]["records"])


def test_unknown_port_is_not_unfiltered(tmp_path):
    answer = respond(
        FakeBackend(),
        "Show orders loading at Nowhere.",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert answer["tool_result"]["total_count"] == 0


def test_missing_order_clarifies_before_dwt_definition(tmp_path):
    answer = respond(
        FakeBackend(),
        "Would ALPHA fit at 93% of DWT?",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert answer["kind"] == "clarification"
    assert "order" in answer["content"]


def test_compound_search(tmp_path):
    answer = respond(
        FakeBackend(),
        "Show vessels open in Singapore. Then explain whether one can load the Tubarao order.",
        config(tmp_path),
        date(2026, 9, 1),
    )
    assert "ALPHA" in answer["content"]
    assert "Arrival" in answer["content"]


def test_model_hash_detects_tampering(tmp_path):
    import importlib.util
    from pathlib import Path

    spec = importlib.util.spec_from_file_location(
        "verify_model", Path(__file__).parents[1] / "scripts/verify_model.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / "weights"
    target.write_bytes(b"abc")
    expected = "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert module.check_file(target, expected, "sha256")["passed"]
    target.write_bytes(b"abd")
    assert not module.check_file(target, expected, "sha256")["passed"]
