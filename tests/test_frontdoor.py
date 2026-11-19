"""Front door: what Jordan sends from the page lands in the journal once, and answers close questions."""
import pandas as pd

from dfo.frontdoor import ingest


def test_ingest_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(ingest, "J", tmp_path)
    pd.DataFrame([dict(id="Q1", asked_at="2026-11-13", topic="results", question="Send Week 10", why_it_matters="",
                       deadline="", status="open", answer="", answered_at="")]).to_csv(tmp_path / "questions.csv", index=False)
    dump = {"responses": [{"id": "lineup-10", "verdict": "disagree", "reason": "Start Estime", "at": "2026-11-16T20:00:00Z"}],
            "answers": [{"id": "Q1", "answer": "attached", "at": "2026-11-17T12:00:00Z"}],
            "notes": [{"id": "N1", "kind": "screenshot", "text": "Week 10 final", "asset_id": "a1", "at": "2026-11-17T12:00:00Z"}]}
    dump["threads"] = [{"id": "trade-Johnny-vikings", "status": "requested", "messages": [
        {"role": "you", "text": "Johnny is a buyer", "at": "2026-11-18T20:00:00Z"},
        {"role": "claude", "text": "Holding", "at": "2026-11-18T20:00:05Z"}]}]
    dump["strategy_notes"] = [{"id": "S1", "at": "2026-11-19T18:00:00Z", "source": "Jordan (strategy tab)", "note": "Keep picks for WR3 help"}]
    assert ingest.run(dump) == dict(responses=1, answers=1, notes=1, threads=2, strategy_notes=1)
    assert ingest.run(dump) == dict(responses=0, answers=0, notes=0, threads=0, strategy_notes=0)
    q = pd.read_csv(tmp_path / "questions.csv")
    assert q.status[0] == "answered" and q.answer[0] == "attached"
    # a changed verdict later is a new journal row, the first one is kept
    dump["responses"][0].update(verdict="agree", at="2026-11-16T21:00:00Z")
    assert ingest.run(dump)["responses"] == 1
    assert len(pd.read_csv(tmp_path / "responses.csv")) == 2
