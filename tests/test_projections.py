"""Projection checks: the outputs exist, are labeled with their as-of time, and the backtest
results the design doc requires still hold (docs/projections.md)."""
import pandas as pd

from dfo.common import PROCESSED


def _csv(name):
    return pd.read_csv(PROCESSED / name)


def test_weekly_beats_fantasypros_on_error_and_is_calibrated():
    s = _csv("proj_weekly_backtest_summary.csv").set_index("group")
    assert s.loc["All", "blend_mae"] < s.loc["All", "fp_only_mae"]
    # 80% range: about 10% above the top; at most ~10% below the bottom (fewer where the bottom is
    # exactly 0 points, since a 0 counts as inside)
    ev = _csv("proj_weekly_backtest.csv")
    for pos, g in ev.groupby("pos"):
        above = (g.points > g.blend_q90).mean()
        below = (g.points < g.blend_q10).mean()
        assert 0.07 <= above <= 0.13, (pos, above)
        assert below <= 0.13, (pos, below)
    assert 0.78 <= s.loc["All", "blend_80pct_coverage"] <= 0.90
    # ranking: no worse than FantasyPros by more than a rounding hair
    assert s.loc["All", "blend_rank_corr"] >= s.loc["All", "fp_only_rank_corr"] - 0.005


def test_weekly_wins_every_test_season():
    b = _csv("proj_weekly_backtest_by_season.csv")
    assert set(b.season) == {2021, 2022, 2023, 2024, 2025}
    assert (b.blend_mae < b.fp_only_mae).all()


def test_rest_of_season_mix_beats_fantasypros_on_error():
    r = _csv("proj_ros_backtest_summary.csv")
    assert (r.ros_mix_mae < r.fp_rank_mae).all()


def test_future_rank_backbone_and_games_model():
    f = _csv("proj_future_backtest_summary.csv")
    a = f[f.group == "All"].set_index("horizon")
    # the dynasty-rank backbone beats last season's average at every horizon
    assert (a.rank_mae < a.naive_mae).all()
    g = _csv("proj_games_backtest_summary.csv")
    assert (g.new_mae <= g.old_mae).all()
    hurt = g[g.group.str.startswith("Hurt")]
    assert (hurt.new_bias.abs() < 1.0).all()


def test_rookie_blend_beats_both_inputs():
    r = _csv("proj_rookie_backtest_summary.csv").iloc[0]
    assert r.model_mae < r.draft_only_mae and r.model_mae < r.rank_only_mae


def test_current_outputs_are_stamped_and_sane():
    w = _csv("proj_week.csv")
    assert w.stats_as_of.notna().all() and w.made_at.notna().all()
    assert (w.low <= w.proj).all() and (w.proj <= w.high).all()
    assert (w.loc[w.bye, "proj"] == 0).all()
    # guard against a degenerate range model (all lows at 0 once happened via early stopping)
    top = w[(~w.bye) & w.pos.isin(["RB", "WR"]) & (w.fp_rank <= 6)]
    assert len(top) and (top.low > 0).mean() > 0.8
    r = _csv("proj_ros.csv")
    assert (r.ros_points >= 0).all() and r.games_left.max() <= 17
    f = _csv("proj_future.csv")
    games = f.filter(like="games_")
    assert ((games >= 0) & (games <= 17)).all().all()
    assert f.dyn_rank_as_of.notna().all()
