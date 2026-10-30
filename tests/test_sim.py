"""Simulator and dial checks (Phase 5)."""
import pandas as pd

from dfo.common import PROCESSED, load_config


def _csv(name):
    return pd.read_csv(PROCESSED / name)


def test_2026_odds_are_a_proper_split_of_the_prize_pool():
    s = _csv("sim_2026.csv")
    pay = load_config("league")["payouts"]
    pool = sum(pay["regular_season"].values()) + sum(pay["playoffs"].values())
    assert abs(s.exp_payout.sum() - pool) < 0.02 * pool
    assert abs(s.p_first.sum() - 1) < 0.01 and abs(s.p_top4.sum() - 4) < 0.02
    assert abs(s.p_playoff_champ.sum() - 1) < 0.01


def test_weekly_scores_match_the_real_season():
    c = _csv("sim_calibration.csv").set_index("measure")
    for m in c.index:
        assert abs(c.loc[m, "simulated"] / c.loc[m, "actual"] - 1) < 0.10, m


def test_preseason_strength_has_skill_on_past_seasons():
    b = _csv("sim_future_backtest_summary.csv").set_index("event")
    assert b.loc["top 4", "brier"] < b.loc["top 4", "brier_no_skill"]
    assert b.loc["regular-season 1st", "brier"] <= b.loc["regular-season 1st", "brier_no_skill"]
    assert b.strength_vs_finish_rank_corr.iloc[0] > 0.15


def test_future_auctions_clear():
    st = _csv("sim_future_strength.csv")
    assert st.price_per_point.between(0.5, 3).all()
    assert len(st.owner.unique()) == 10 and set(st.season) == {2027, 2028, 2029}


def test_dial():
    d = _csv("dial.csv")
    assert len(d) == 10
    for c in ["per_point_2026", "per_point_2027", "per_point_2028", "per_point_2029"]:
        assert (d[c] >= -0.05).all(), c
    assert d.posture.str.match(r"^(push|build|no lever)").all()
    assert (d.per_point_2029_haircut <= d.per_point_2029 + 1e-9).all()


def test_parameters_are_stamped():
    for f in ["params_absence.csv", "params_future_sim.csv"]:
        assert _csv(f).fit_date.notna().all(), f


def test_nfl_config_team_codes_are_strings():
    divs = load_config("nfl")["divisions"]
    teams = [t for ts in divs.values() for t in ts]
    assert len(teams) == 32 and all(isinstance(t, str) for t in teams) and "NO" in teams
