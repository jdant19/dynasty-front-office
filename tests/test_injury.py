"""Injury status in the projections (2026-11-25): IR and Out players were projected about 11 points a
week and counted at full rest-of-season value (calibration review, Weeks 9-11: 2 of 27 played)."""
import numpy as np
import pandas as pd
import pytest

from dfo.common import PROCESSED
from dfo.projections import injury


def test_this_week_chance_of_playing():
    p = injury.play_prob(["Out", "IR", "Doubtful", "Questionable", "Questionable", "", "Questionable"],
                         ["WR", "RB", "TE", "WR", "WR", "QB", "DEF"], [15, 15, 10, 12, 12, 20, 8],
                         ["none", "none", "none", "full", "dnp", "none", "none"])
    assert list(p[:3]) == [0, 0, 0]
    assert 0.5 < p[4] < p[3] < 1            # did not practice < full practice
    assert p[5] == 1 and p[6] == 1          # unlisted plays; defenses are always available


def test_fitted_rates_match_history():
    w = pd.read_csv(PROCESSED / "params_injury_week.csv").set_index("term").coef
    assert w["rate_Out"] < 0.01 and w["rate_Doubtful"] < 0.02 and w["rate_IR"] < 0.03
    assert 0.55 < w["rate_Questionable"] < 0.7 and w["n_Questionable"] > 1000


def test_ir_minimum_and_return():
    # placed on IR this week: no chance of playing the next three games, then the fitted return share
    a = injury.profile("IR", "WR", 7, 0.87, 0.0, missed_before=0, ir_games=1)
    assert (a[:4] == 0).all() and a[4] > 0 and a.mean() < 0.3
    # fourth game on IR: eligible next week
    b = injury.profile("IR", "WR", 7, 0.87, 0.0, missed_before=3, ir_games=4)
    assert b[0] == 0 and b[1] > 0
    # healthy: the flat position rate, unchanged from before
    assert (injury.profile("", "WR", 5, 0.87, 1.0) == 0.87).all()
    # out this week: back next week often, not always
    o = injury.profile("Out", "RB", 6, 0.84, 0.0)
    assert o[0] == 0 and 0.25 < o[1] < 0.6 and o[3] > o[1]


def test_week_projection_is_a_mixture():
    wk = pd.DataFrame(dict(player_id=["a", "b", "c"], pos=["WR", "WR", "RB"], proj=[12.0, 12.0, 10.0],
                           low=[4.0, 4.0, 3.0], high=[22.0, 22.0, 18.0]))
    st = pd.DataFrame(dict(status=["Questionable", "IR"], practice=["dnp", "none"], source="league site",
                           missed_before=0, ir_games=[0, 3]), index=["a", "b"])
    out = injury.apply_week(wk, st).set_index("player_id")
    p = out.loc["a", "play_prob"]
    assert out.loc["a", "proj"] == pytest.approx(p * 12.0, abs=0.01) and out.loc["a", "low"] == 0
    assert out.loc["a", "proj_if_plays"] == 12.0
    assert out.loc["b", ["proj", "low", "high"]].tolist() == [0, 0, 0]
    assert out.loc["c", ["proj", "low", "high", "play_prob"]].tolist() == [10.0, 3.0, 18.0, 1.0]


def test_profiles_only_for_the_week_they_were_made(tmp_path, monkeypatch):
    f = tmp_path / "proj_avail.csv"
    pd.DataFrame(dict(player_id=["x", "x"], week=[12, 13], play=[0.0, 0.2], from_week=12)).to_csv(f, index=False)
    monkeypatch.setattr(injury, "PROCESSED", tmp_path)
    injury._avail_file.cache_clear()
    d = pd.Series(0.9, index=["x", "y"])
    now = injury.weekly_play(["x", "y"], [12, 13], d)
    assert now.loc["x"].tolist() == [0.0, 0.2] and now.loc["y"].tolist() == [0.9, 0.9]
    past = injury.weekly_play(["x"], [9, 10, 11, 12, 13], pd.Series(0.9, index=["x"]))    # a review of Week 9
    assert (past.loc["x"] == 0.9).all()
    injury._avail_file.cache_clear()


def test_live_files_respect_status():
    w = pd.read_csv(PROCESSED / "proj_week.csv")
    if "play_prob" not in w:
        pytest.skip("projections not rebuilt since the injury fit")
    out = w[w.injury_status.isin(["Out", "IR", "Doubtful"])]
    assert (out.proj == 0).all()
    r = pd.read_csv(PROCESSED / "proj_ros.csv").set_index("player_id")
    ir = w[w.injury_status.eq("IR")].player_id
    assert (r.loc[r.index.intersection(ir), "availability"] < 0.5).all()
