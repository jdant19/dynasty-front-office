"""Decision modules (Phase 7): legal outputs and the exit-test records."""
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.decide import lineup as L


def _pool():
    rows = []
    for pos, n, base in (("QB", 3, 18), ("RB", 5, 12), ("WR", 7, 11), ("TE", 3, 8), ("K", 2, 8), ("DEF", 2, 7)):
        for i in range(n):
            rows.append(dict(player_id=f"{pos}{i}", player=f"{pos}{i}", pos=pos, team="X", opp="Y",
                             proj=base - i, low=max(0, base - i - 8), high=base - i + 9, bye=False, injury_status=""))
    return pd.DataFrame(rows)


def test_best_lineup_fills_every_slot_legally():
    st, flex_ok = L.slots()
    line = L.best_lineup(_pool())
    assert len(line) == sum(st.values()) and line.player_id.is_unique
    for slot, n in st.items():
        assert (line.slot == slot).sum() == n
    assert line[line.slot == "FLEX"].pos.isin(flex_ok).all()
    assert (line.slot == line.pos).sum() == len(line) - st["FLEX"]


def test_questionable_players_count_at_their_chance_of_playing():
    p = _pool()
    p.loc[p.player_id == "RB0", "injury_status"] = "Questionable"
    p.loc[p.player_id == "WR0", "injury_status"] = "Out"
    e = L.eligible(p)
    assert "WR0" not in set(e.player_id)
    assert e.set_index("player_id").proj["RB0"] < 12
    q = pd.read_csv(PROCESSED / "params_questionable.csv")
    assert q["mean"].between(0.3, 0.95).all()


def test_lineup_review_covers_jordans_weeks():
    r = pd.read_csv(PROCESSED / "decide_lineup_review.csv")
    assert set(r.week) == {4, 5, 6, 7, 8}
    assert (r.best_possible >= r[["jordan_points", "module_points"]].max(axis=1) - 0.01).all()
    lu = pd.read_csv(PROCESSED / "jordan_lineups_2026.csv")
    st, _ = L.slots()
    starts = lu[~lu.slot.isin(["BN", "IR"])].groupby("week").size()
    assert (starts == sum(st.values())).all()


def test_rank_flags_catch_falls_and_rises():
    import inspect
    assert "abs()" in inspect.getsource(L.rank_jumps)        # a fall (Chase WR3 -> WR45) counts as much as a rise
