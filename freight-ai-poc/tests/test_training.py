import pytest
from freight_ai.training.dataset import (
    audit_splits,
    build_dataset,
    demonstrations,
    read_split,
    verify_dataset,
)


def test_reproducible_leakage_free_data(tmp_path):
    first = build_dataset(tmp_path)
    assert first == build_dataset(tmp_path) == verify_dataset(tmp_path)
    assert first["source_records_used"] is False
    assert [d["split"] for d in demonstrations(tmp_path)] == ["train"] * 3
    splits = {s: read_split(tmp_path, s) for s in ("train", "validation", "test")}
    splits["test"][0]["question"] = splits["train"][0]["question"]
    with pytest.raises(ValueError):
        audit_splits(splits)
    with (tmp_path / "test.jsonl").open("a") as f:
        f.write("{}\n")
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_dataset(tmp_path)
