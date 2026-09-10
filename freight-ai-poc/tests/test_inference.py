import pytest
from freight_ai.inference.backend import ScriptedBackend
from freight_ai.inference.prompts import build_messages, extract


def test_strategies_and_strict_parse():
    demo = {
        "split": "train",
        "question": "List orders",
        "expected": {"action": "query"},
    }
    assert [
        len(build_messages("x", s, [demo] * 3)) for s in ("zero", "one", "few")
    ] == [2, 4, 8]
    assert extract(ScriptedBackend('{"action":"query"}'), "x")[0].action == "query"
    with pytest.raises(ValueError):
        extract(ScriptedBackend('```json\n{"action":"query"}\n```'), "x")
    with pytest.raises(ValueError):
        build_messages("x", "one", [{**demo, "split": "test"}])
