"""History grading and owner models (Phase 6)."""
import pandas as pd

from dfo.common import PROCESSED


def _csv(name):
    return pd.read_csv(PROCESSED / name)


def test_only_real_trades_are_graded():
    a = _csv("hist_trade_assets.csv")
    assert not a.trade_id.str.startswith("T2026-L").any()       # old system's trade ideas are not trades
    assert a.trade_id.nunique() >= 55
    unresolved = a[(a.kind == "player") & a.player_id.isna()]
    assert len(unresolved) <= 8                                   # only conditional terms stay unresolved


def test_trade_grades_are_zero_sum():
    g = _csv("hist_trades.csv")
    s = g.groupby("trade_id")[["points_later", "points_trade_season", "salary_later"]].sum()
    assert (s.abs() < 0.5).all().all()


def test_owner_model_covers_current_owners():
    m = _csv("owner_model.csv")
    owners = set(_csv("site_rosters.csv").owner.dropna())
    assert set(m.owner) == owners and len(m) == 10
    assert m.rfa_match_rate.dropna().between(0, 1).all()
    reads = (PROCESSED / "owner_reads.md").read_text()
    assert all(f"## {o}" in reads for o in owners)
    assert "league average average" not in reads


def test_rankings_cover_every_aspect_with_sample_sizes():
    r = _csv("owner_rankings.csv")
    assert len(r) == 10
    for a in ("auction", "rookie_draft", "rfa_bidding", "rfa_matching", "tags", "trades", "waiver_fa", "results"):
        assert r[f"{a}_rank"].dropna().between(1, 10).all(), a
        assert (r[f"{a}_n"] >= 0).all(), a
    assert r.contract_years.dropna().between(1, 4).all()


def test_careers_add_up():
    c = _csv("owner_careers.csv")
    f = _csv("owner_finishes.csv")
    assert (c.payouts == c.payouts.round()).all()
    assert c.payouts.sum() == f.payout.sum()
    assert (f.groupby("season").payout.sum() <= 1000).all()     # today's table pays $1,000 a season
    assert (c.playoff_titles <= c.playoff_trips).all()


def test_jordans_notes_are_logged_with_a_verdict():
    o = _csv("observations.csv")
    assert set(o.owner) >= {"Ben", "Johnny", "Mike", "Russ", "Ryan", "Zach"}
    assert o.verdict.notna().all() and o.date.notna().all() and o.expires.notna().all()
    assert (PROCESSED.parent / "raw" / "owner_notes" / "2026-11-02_owner_reads_doc.md").exists()


def test_decision_grades_use_the_right_baselines():
    d = _csv("grades_decisions.csv")
    assert {"auction", "rookie_pick", "rfa_bid", "rfa_match", "rfa_let_go", "tag", "trade", "waiver_fa"} <= set(d.type)
    assert abs(d[d.type == "auction"].grade.mean()) < 10          # the price curve is the going rate
    assert abs(d[d.type == "rookie_pick"].grade.mean()) < 10      # graded against the slot average
    t = d[d.type == "trade"].groupby("trade_id").grade_unweighted.sum()
    assert (t.abs() < 1).all()                                     # unweighted, a trade is zero-sum
    won = d[d.type == "rfa_bid"].grade.sum()
    assert abs(won + d[d.type == "rfa_let_go"].grade.sum()) < 1    # letting go mirrors the winner's deal
    c = _csv("params_price_curve.csv")
    for _, g in c.groupby("pos"):
        assert g.expected_points.is_monotonic_increasing


def test_rebuilt_lineups_explain_finishes():
    from scipy.stats import spearmanr
    o = _csv("owner_season_lineups.csv")
    assert spearmanr(o.lineup_ppg, -o.regular_season_finish).statistic >= 0.6
    assert (o.injury_cost_ppg > -5).all()
    p = _csv("owner_season_posture.csv")
    assert abs(p.weight_mid.mean() - 1) < 0.01 and p.weight.between(0, 3).all()
    b = _csv("owner_bridge.csv")
    assert len(b) == 10
