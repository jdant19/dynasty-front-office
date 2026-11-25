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


def test_page_files_are_strict_json():
    """The page parses these in the browser: NaN or Infinity anywhere makes the whole brief unreadable."""
    import json
    from dfo.common import ROOT

    def bad(c):
        raise ValueError(f"non-JSON constant {c}")
    files = [ROOT / "data/frontdoor" / f for f in ("brief.json", "season.json", "future.json")]
    files += sorted((ROOT / "data/frontdoor/pack").glob("*.json"))
    for f in files:
        if f.exists():
            json.loads(f.read_text(), parse_constant=bad)


def test_strict_dumps_drops_nan():
    import json
    import numpy as np
    from dfo.frontdoor import strict
    out = json.loads(strict.dumps({"slot": float("nan"), "n": np.int64(3), "x": [np.float64("inf"), 1.5]}))
    assert out == {"slot": None, "n": 3, "x": [None, 1.5]}


def test_waiver_deadline_after_tuesday_close():
    import datetime as dt
    from dfo.frontdoor.export import waiver_deadline
    utc = dt.timezone.utc
    # Tuesday 2026-11-24 20:00 PT: FAAB still open
    assert waiver_deadline(dt.datetime(2026, 11, 25, 4, 0, tzinfo=utc)) == "Tuesday 11:59pm Pacific"
    # Wednesday 2026-11-25 14:30 PT: waivers have run, $0 adds only
    assert "$0 add" in waiver_deadline(dt.datetime(2026, 11, 25, 22, 30, tzinfo=utc))


def test_scorecard_freezes_until_kickoff():
    import datetime as dt
    from dfo.projections import scorecard
    kick = pd.Series([pd.Timestamp("2026-11-29 13:00", tz="UTC"), pd.Timestamp("2026-11-26 13:00", tz="UTC")],
                     index=pd.MultiIndex.from_tuples([(12, "MIN"), (12, "DET")]))
    base = dict(season=2026, week=12, pos="WR", bye=False, low=1.0, high=9.0, made_at="t")
    old = pd.DataFrame([dict(base, player_id="a", team="MIN", proj=5.0), dict(base, player_id="b", team="DET", proj=5.0)])
    new = pd.DataFrame([dict(base, player_id="a", team="MIN", proj=7.0), dict(base, player_id="b", team="DET", proj=9.0)])
    out = scorecard._merge(old, new, pd.Timestamp("2026-11-27 12:00", tz="UTC"), kick).set_index("player_id").proj
    assert out.to_dict() == {"a": 7.0, "b": 5.0}          # Detroit played Thursday: its row stays frozen
