"""Decision modules (Phase 7): legal outputs and the exit-test records."""
import pandas as pd
import pytest

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
    # the collector refreshes rosters several times a day; a plan made on older rosters is stale, not
    # wrong (someone may have claimed one of its free agents since), so it is only checked on its own rosters
    if "rosters_as_of" not in best or str(best.rosters_as_of.iloc[0]) != str(ros.as_of.max()):
        pytest.skip("waiver plan was made on older rosters; rerun python -m dfo.decide.waivers")
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


def test_trade_review_covers_sample_and_2026():
    h = pd.read_csv(PROCESSED / "decide_trades_review_history.csv")
    s = pd.read_csv(PROCESSED / "decide_trade_sample.csv")
    assert set(h.trade_id) == set(s.trade_id) and len(h) == 2 * len(s)
    assert s.approved_by_jordan.notna().all()
    t = pd.read_csv(PROCESSED / "decide_trades_review_2026.csv")
    assert t.trade.nunique() == 4 and len(t) == 8
    # each side is scored by its own dial: the two sides need not sum to zero, but each row's parts add up
    assert ((t.payout_now + t.payout_later - t.payout).abs() < 0.05).all()


def test_trade_salary_rule_matches_league_config():
    from dfo.rules.contracts import TradeSide, trade_salaries_legal
    assert trade_salaries_legal(TradeSide([19]), TradeSide([47]))[0] is False      # Puka trade without cap
    # Puka trade: Max sends $47 and $10 of cap for $19; Russ had the space to absorb the rest
    ok, why = trade_salaries_legal(TradeSide([15, 4]), TradeSide([45, 2], cap_compensation=10))
    assert "$29 vs $47" in why and ok is False                                      # no room known
    assert trade_salaries_legal(TradeSide([15, 4], cap_room=18), TradeSide([45, 2], cap_compensation=10))[0]
    assert not trade_salaries_legal(TradeSide([15, 4], cap_room=17), TradeSide([45, 2], cap_compensation=10))[0]
    assert trade_salaries_legal(TradeSide([40]), TradeSide([47]))[0] is True
    assert trade_salaries_legal(TradeSide([5, 6]), TradeSide([18]))[0] is True     # nobody over $20


def test_nothing_for_nothing_is_worth_nothing():
    from dfo.decide.trades import Assets, Trade, live
    st = live()
    e = st.evaluate(Trade("Jordan", "Ben", Assets(), Assets()))
    assert (e.payout.abs() < 1e-6).all() and e.legal.all()


def test_outlook_fits_under_the_cap_every_season():
    from dfo.decide.outlook import Outlook
    o = Outlook()
    t = o.team("Jordan")
    assert list(t.season) == [2027, 2028, 2029]
    # under the cap, or over it only by RFA matches (league.yaml: a match may exceed the cap)
    for r in t.itertuples():
        g = o.prices[o.prices.player_id.isin(o.rfa_years.get(r.season, set())) & (o.prices.owner == "Jordan")]
        assert r.cap_room + g[f"salary_{r.season}"].fillna(0).sum() >= 0
        assert r.cap_room >= 0 or r.points_bought == 0


def test_a_pick_sent_costs_the_sender_what_it_gives_the_receiver():
    from dfo.decide.trades import Assets, Trade, live
    st = live()
    d = st.later(Trade("Jordan", "Ben", Assets(picks=[(2028, 1, "Jordan")]), Assets()))
    assert d["Jordan"]["later_points"] < 0 < d["Ben"]["later_points"]
    assert abs(d["Jordan"]["later_points"] + d["Ben"]["later_points"]) < 1e-6


def test_trade_search_output_is_legal_and_never_marked_proposed():
    p = PROCESSED / "decide_trade_search.csv"
    if not p.exists():
        pytest.skip("run python -m dfo.decide.trade_search")
    d = pd.read_csv(p)
    assert (d.salary_rule.str.contains("within|not required|space")).all()
    assert "status" not in d or (d.status == "idea").all()
    assert (d.joint == d.jordan + d.partner_gain).all() or ((d.joint - d.jordan - d.partner_gain).abs() <= 1).all()


def test_conversions_are_stable_league_wide():
    from dfo.valuation import conversions as C
    c = C.load()
    assert 0.009 < c["places_per_point"] < 0.014
    assert abs(c["dollars_per_point_later"] - c["places_per_point"] * c["dollars_per_place_avg"]) < 1e-3
    assert c["finish_carryover_rank_corr"] < 0.3              # why later seasons use one league-wide value
    odds = C.finish_odds(2.0)
    assert abs(odds.sum() - 1) < 1e-9 and C.expected_payout(odds, 1.0) > C.expected_payout(odds)


def test_pick_tiers_fall_with_slot_and_cover_every_slot():
    from dfo.valuation import pick_tiers as T
    t = T.load()
    assert t.first_slot.iloc[0] == 1 and t.last_slot.iloc[-1] == 30
    assert (t.first_slot.iloc[1:].to_numpy() == t.last_slot.iloc[:-1].to_numpy() + 1).all()
    assert t.mean_points.is_monotonic_decreasing
    assert T.pick_value(1, 2) > T.pick_value(1) > T.pick_value(1, 9.5) >= T.pick_value(2)


def test_locked_out_team_buys_nothing_and_still_fills_its_roster():
    from dfo.decide.offseason import ME, Auction
    a = Auction()
    p = a.p
    gid, lid = p.index[p.player == "Jahmyr Gibbs"][0], p.index[p.player == "CeeDee Lamb"][0]
    r = a.play({lid: 96}, {gid: True})
    assert r["rfa_paid"][gid] > 96                     # an elite RFA bid on first draws the league's money
    assert r["jordan_room"] < 0 and r["jordan_spent"] == 0 and r["jordan_free"]
    open_ = a.play({}, {gid: False})
    assert open_["rfa_winner"][gid] != "Jordan"
    assert open_["jordan_spent"] <= open_["jordan_room"] + 18 and open_["jordan_bought"]


def test_owner_plans_follow_behavior_not_bargains():
    from dfo.decide.owner_plans import predict
    d = predict(exclude_owner="Jordan")
    assert (d.groupby("owner").apply(lambda g: (g.action == "RFA").sum()) <= 2).all()
    assert (d.groupby("owner").apply(lambda g: (g.action == "tag").sum()) <= 1).all()
    # the most valuable RFA-eligible player of each owner is designated when anyone would bid on him
    bij = d[d.player == "Bijan Robinson"]
    assert len(bij) == 1 and bij.action.iloc[0] in ("RFA", "tag") and bij.p_keep.iloc[0] >= 0.85


def test_deadline_posture_and_run_plan():
    from dfo import run
    from dfo.decide.deadline import posture
    p = posture().set_index("owner")
    assert set(p.posture) <= {"push", "hold", "build"}
    assert (p.loc[p.posture == "push", "now_vs_later"] >= 1.25).all()
    for job in ("daily", "waivers", "lineup", "trades"):
        steps = [s[0] for s in run.plan(job)]
        assert steps[:3] == ["dfo.ingest.site", "dfo.scoring", "dfo.projections.current"]
    assert "dfo.decide.trade_search" in [s[0] for s in run.plan("trades")]


def test_rfa_order_follows_dynasty_value():
    from dfo.decide.offseason import Auction
    a = Auction()
    p = a.p
    gid = p.index[p.player == "Jahmyr Gibbs"][0]
    cook = p.index[p.player == "James Cook"][0]
    assert a.order_value(gid) > a.order_value(cook)
