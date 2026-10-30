"""Valuation checks (Phase 4): fitted parameters are sane and stamped, the backtest claims in
docs/valuation.md still hold, and current prices obey the league's money rules."""
import numpy as np
import pandas as pd

from dfo.common import PROCESSED


def _csv(name):
    return pd.read_csv(PROCESSED / name)


def test_market_fit_parameters():
    p = _csv("params_market.csv")
    assert set(p.pos) == {"QB", "RB", "WR", "TE"}
    assert p.replacement_shift.between(-40, 200).all()
    assert 0.5 < p.price_per_point.iloc[0] < 2.0 and 0.3 < p.price_per_point_structural.iloc[0] < 1.5
    assert p.fit_mae.iloc[0] < 9 and p.fit_date.notna().all()
    # QB replacement sits furthest above the structural level (mid-tier QBs sell for $1-2)
    assert p.set_index("pos").replacement_shift.idxmax() == "QB"


def test_engine_ranks_auction_buys_about_as_well_as_the_league():
    y = _csv("val_auction_year0.csv").set_index("predictor")
    assert y.loc["engine", "rank_corr"] >= y.loc["league price", "rank_corr"] - 0.03


def test_rfa_costs_more_per_point_than_the_auction():
    o = _csv("val_rfa_overall.csv").iloc[0]
    assert o.rfa_premium > 0


def test_gap_and_aging_parameters():
    g = _csv("params_gap_shrink.csv")
    assert g.slope.between(0.2, 1.1).all() and g.fit_date.notna().all()
    a = _csv("params_aging.csv")
    for _, c in a.iterrows():      # every position declines by age 30
        assert c.c0 + c.c1 * 30 + c.c2 * 900 < 0, c.pos


def test_prices_follow_money_rules():
    p = _csv("prices.csv")
    for col in ["season_dollars", "lifetime_dollars", "market_auction", "value_2027", "value_2028", "value_2029"]:
        v = p[col].dropna()
        assert (v == v.round()).all(), col                  # whole dollars
    assert (p.season_dollars >= 0).all()
    assert (p.market_auction >= 1).all()
    roster = _csv("site_rosters.csv")
    assert p.owner.ne("").sum() >= 0.9 * len(roster)         # nearly every rostered player is priced
    sk = p[p.pos.isin(["QB", "RB", "WR", "TE"]) & (p.value_2027 > 20)]
    assert (sk.value_2029 / sk.value_2027).median() <= 1.0  # values fall with age on the whole


def test_pick_model():
    s = _csv("val_picks_slots.csv")
    assert (np.diff(s.use_value) <= 1e-6).all()              # never rises with a later pick
    assert s.use_value.iloc[0] > s[s["round"] == 3].use_value.max()
    o = _csv("val_picks_odds.csv")
    assert o.bust.between(0, 1).all() and o.star.between(0, 1).all()
    t = _csv("val_picks_trade_summary.csv")
    assert len(t) == 2 and (t.trades >= 15).all()


def test_ours_uses_waivers_and_roster():
    o = _csv("ours_jordan.csv")
    mine = o[o.on_roster]
    assert len(mine) >= 15
    assert o.drop_if_added.dropna().isin(mine.player_id).all()
    assert (o.dial_weight == 1.0).all()                      # placeholder until Phase 5, labeled
    k = mine[mine.pos == "K"]
    if len(k):                                               # a kicker is worth his edge over streaming
        assert (k.lineup_change < 60).all()
