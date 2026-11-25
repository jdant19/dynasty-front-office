"""Starter check (2026-11-25): with no FantasyPros rank, backup QBs were projected on their earlier starts
(Willis 14.6, Wentz 12.2 in Weeks 9-11; neither played)."""
import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.projections import depth


def test_roles_skip_injured_starter():
    dc = pd.DataFrame(dict(season=2026, week=12, team="NYG", player_id=["dart", "mccarthy", "winston"], depth=[1, 2, 3]))
    st = pd.DataFrame(dict(season=2026, week=12, player_id=["dart"], status=["IR"]))
    r = depth.roles(dc, st).set_index("player_id")
    assert r.role.to_dict() == {"dart": "out", "mccarthy": "starter", "winston": "backup"}


def test_unranked_backup_capped_ranked_left_alone():
    wk = pd.DataFrame(dict(player_id=["a", "b", "c", "d"], pos="QB", team="MIN", proj=[14.6, 14.6, 20.0, 0.2],
                           low=[5.0, 5.0, 9.0, 0.0], high=[24.0, 24.0, 30.0, 1.0], ecr=[np.nan, 14.0, np.nan, np.nan], bye=False))
    r = pd.DataFrame(dict(team="MIN", group=["former_starter", "former_starter", "starter", "backup3"]), index=["a", "b", "c", "d"])
    out = depth.apply_week(wk, r).set_index("player_id")
    p = depth.params().query("game == 0").set_index("group")
    assert out.loc["a", "proj"] == p.loc["former_starter", "points"] and out.loc["a", "high"] < 24
    assert out.loc["b", "proj"] == 14.6 and out.loc["b", "qb_role"] == "starter (FantasyPros rank)"
    assert out.loc["c", "proj"] == 20.0
    assert out.loc["d", "proj"] == 0.2                       # never raised


def test_fit_backups_rarely_start():
    p = depth.params()
    now = p[p.game == 0].set_index("group").started
    assert now["backup3"] < now["backup2"] < now["former_starter"] < 0.25
    assert 12 < p.query("group == 'first_start_points'").points.iloc[0] < 18


def test_live_backups_not_projected_as_starters():
    w = pd.read_csv(PROCESSED / "proj_week.csv")
    if "qb_role" not in w:
        return
    b = w[w.qb_role.isin(depth.BACKUPS) & w.fp_rank.isna()]
    cap = depth.params().query("game == 0").set_index("group").points
    assert (b.proj <= b.qb_role.map(cap) + 0.05).all()
