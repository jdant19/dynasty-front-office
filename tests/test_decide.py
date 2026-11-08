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


def test_waiver_bids_are_zero_or_one_dollar():
    from dfo.decide import waivers as W
    best = pd.DataFrame(dict(net_payout=[3.9, 0.99, 0.0, 1.0]))
    assert list(W.bid(best)) == [1, 0, 0, 1]          # every 2026 add went for $0 or $1


def test_waiver_moves_add_free_agents_and_drop_own_players():
    w = sorted(PROCESSED.glob("decide_waivers_w*.csv"))
    assert w, "run python -m dfo.decide.waivers"
    best = pd.read_csv(w[-1])
    ros = pd.read_csv(PROCESSED / "site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    c = ros.merge(ids[["site_player_id", "canonical_id"]], left_on="player_id", right_on="site_player_id")
    mine = set(c[c.owner == "Jordan"].canonical_id)
    assert not best["add"].isin(set(c.canonical_id)).any()
    assert best["drop"].isin(mine).all()
    assert best["add"].is_unique


def test_waiver_review_scores_lineup_points():
    r = pd.read_csv(PROCESSED / "decide_waivers_review.csv")
    assert set(r.week) == {7, 8, 9}
    for col in ("lineup_that_week_jordan", "lineup_that_week_module", "lineup_through_jordan"):
        assert col in r
    # Jordan's roster in Week 7 is the one he actually had: its best lineup is the review's best possible
    lr = pd.read_csv(PROCESSED / "decide_lineup_review.csv").set_index("week")
    assert abs(r.set_index("week").lineup_that_week_jordan[7] - lr.best_possible[7]) < 0.5


def test_actual_lineup_uses_legal_slots():
    from dfo.decide.waivers import _actual_lineup
    pts = pd.DataFrame(dict(player_id=["a", "b", "c"], pos=["QB", "QB", "K"], week=[1, 1, 1], points=[20.0, 30.0, 5.0]))
    assert _actual_lineup({"a", "b", "c"}, pts, [1]) == 35.0   # one QB slot


def test_over_the_cap_means_zero_dollar_bids_only():
    from dfo.decide import waivers as W
    from dfo.rules.cap import cap_room
    c = cap_room()
    assert (c.max_bid >= 0).all() and (c.max_bid == c.room.clip(lower=0)).all()
    assert load_config("league")["cap"]["at_or_over_cap"] == "only_zero_dollar_adds"
    best = pd.DataFrame(dict(net_payout=[3.9, 1.5]))
    assert list(W.bid(best, max_bid=0)) == [0, 0]


def test_stream_adjustments_only_where_backtest_beat_rate():
    from dfo.decide.streaming import adjustments
    p = pd.read_csv(PROCESSED / "params_stream_matchup.csv")
    used = p[p.use]
    assert (used.backtest_gain > 2 * used.gain_se).all()
    assert set(used.k) <= {2, 3}                     # the coming week uses the weekly projection
    a = adjustments(2026, 9)
    assert a.adj.abs().max() < 6
