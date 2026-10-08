"""The Streamlit demo, driven headlessly. Skipped when Streamlit is not installed."""

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

APP = str(Path(__file__).resolve().parents[1] / "demo" / "app.py")


def _metrics(at):
    return {m.label: m.value for m in at.metric}


def _sample(at):
    at.run()
    next(b for b in at.button if b.label == "Try the sample statement").click().run()
    assert not at.exception, at.exception
    return at


def test_demo_without_a_key_runs_rules_only(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    at = _sample(AppTest.from_file(APP, default_timeout=60))
    m = _metrics(at)
    assert m["Bookings"] == "15"
    assert m["🔧 By rule"] == "9" and m["✨ By Gemini"] == "0" and m["❔ Needs review"] == "6"
    assert any("Gemini is off" in i.value for i in at.info)


def test_demo_with_gemini_labels_the_rows_it_decided(monkeypatch):
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    calls = []

    def fake_generator(model):
        def generate(prompt):
            calls.append(prompt)
            items = prompt.split("Transactions (id: text):\n")[1].split("\n\n")[0].splitlines()
            return json.dumps([{"id": int(line.split(": ", 1)[0]), "main_category": "Health",
                                "subcategory": "Pharmacy"} for line in items])
        return generate

    monkeypatch.setattr("kubera.categorize.gemini_generator", fake_generator)
    at = _sample(AppTest.from_file(APP, default_timeout=60))
    m = _metrics(at)
    assert m["✨ By Gemini"] == "6" and m["❔ Needs review"] == "0"
    assert len(calls) == 1                       # six texts, one batch
    assert "REWE" not in calls[0]                # rule-decided merchants never reach Gemini

    at.run()                                     # a redraw must not call Gemini again
    assert len(calls) == 1
