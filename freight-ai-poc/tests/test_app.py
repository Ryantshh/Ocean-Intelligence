from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_streamlit_start_and_deterministic_query():
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app/streamlit_app.py")
    )
    app.run(timeout=20)
    assert not app.exception
    next(b for b in app.button if b.label == "Run search").click().run()
    assert not app.exception
    assert app.session_state["response"]["tool_result"]["total_count"] == 1
    next(b for b in app.button if b.label == "Screen vessels").click().run()
    assert not app.exception
    assert app.session_state["screening"]["candidate_count"] == 1


def test_chat_submission_without_model_loading(monkeypatch):
    from freight_ai.inference.backend import HFBackend

    def fail_load(*args, **kwargs):
        raise AssertionError("Deterministic searches must not load the model")

    monkeypatch.setattr(HFBackend, "__init__", fail_load)
    app = AppTest.from_file(
        str(Path(__file__).resolve().parents[1] / "app/streamlit_app.py")
    ).run()
    for question in [
        "List vessels with at least 190,000 tonnes DWT",
        "Show orders loading at Nowhere.",
    ]:
        app.chat_input[0].set_value(question).run(timeout=20)
        assert not app.exception
        assert not app.error
        assert app.session_state["chat_messages"][-1]["role"] == "assistant"
    assert "0 matching" in app.session_state["chat_messages"][-1]["content"]
