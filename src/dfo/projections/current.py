"""Current projections for the season in progress.

    PYTHONPATH=src python -m dfo.projections.current

Writes, for every rostered player plus every FantasyPros-ranked player:
  data/processed/proj_week.csv     next week: projection, 80% range, bye flag
  data/processed/proj_ros.csv      rest of season: per-game rate, games left, total
  data/processed/proj_future.csv   next three seasons: points per game, expected games, total
Each file carries the as-of time of its inputs.
"""
from __future__ import annotations

import datetime as dt
import warnings

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.projections.features import build, implied_totals
from dfo.projections.seasons import predict_future
from dfo.projections.weekly import RangeMap, WeeklyModel
from dfo.sources.fetch import as_of, nflverse

warnings.filterwarnings("ignore")
SEASON = 2026
# Rest-of-season rate: weights chosen in the 2021-2025 check at weeks 6, 8 and 10 (docs/projections.md).
ROS_WEIGHTS = {"fp_pts": 0.5, "points_std": 0.25, "xfp_std": 0.25}


def next_week() -> int:
    p = pd.read_csv(PROCESSED / f"points_{SEASON}.csv")
    return int(p[p.season_type == "REG"].week.max()) + 1


def rostered() -> pd.DataFrame:
    ros = pd.read_csv(PROCESSED / "site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id", "pos"]]
    r = ros.merge(ids.rename(columns={"site_player_id": "player_id", "pos": "id_pos"}), on="player_id", how="left")
    return r.rename(columns={"canonical_id": "pid"})


def availability(df: pd.DataFrame, week: int) -> pd.Series:
    """Share of remaining team games that healthy, ranked players actually played, by position,
    2021-2025 from the same week on."""
    h = df[(df.season.between(2021, 2025)) & (df.week >= week) & df.implied.notna()]
    ranked_at = df[(df.season.between(2021, 2025)) & (df.week == week) & df.ecr.notna() & (df.played == 1)]
    h = h.merge(ranked_at[["season", "player_id"]], on=["season", "player_id"])
    return h.groupby("pos").played.mean()


def main() -> None:
    week = next_week()
    ros_tbl = rostered()
    last_team = (pd.read_csv(nflverse("stats_player", f"stats_player_week_{SEASON}.csv"), low_memory=False)
                 .sort_values("week").groupby("player_id").team.last())
    extra = pd.DataFrame(dict(season=SEASON, week=week, player_id=ros_tbl.pid, pos=ros_tbl.id_pos))
    extra = extra[extra.player_id.notna()]
    site_team = ros_tbl.dropna(subset=["pid"]).set_index("pid").nfl_team.replace({"LAR": "LA", "JAC": "JAX", "WSH": "WAS"})
    extra["team"] = extra.player_id.map(site_team).fillna(extra.player_id.map(last_team))
    df = build(extra=extra)
    train = df[(df.season < SEASON) | ((df.season == SEASON) & (df.week < week))]
    model = WeeklyModel(train)
    wk = df[(df.season == SEASON) & (df.week == week)].copy()
    p = model.predict(wk, RangeMap.from_backtest())
    wk = wk.join(p[["proj", "low", "high", "fp_pts"]])
    wk["bye"] = wk.implied.isna()
    wk.loc[wk.bye, ["proj", "low", "high"]] = 0.0
    names = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").name
    owner = ros_tbl.dropna(subset=["pid"]).set_index("pid").owner
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    inputs_as_of = as_of(nflverse("stats_player", f"stats_player_week_{SEASON}.csv"))
    out_week = pd.DataFrame(dict(
        season=SEASON, week=week, player_id=wk.player_id, player=wk.player_id.map(names), pos=wk.pos,
        team=wk.team, opp=wk.opp, owner=wk.player_id.map(owner).fillna(""), fp_rank=wk.ecr.round(1),
        proj=wk.proj.round(1), low=wk.low.round(1), high=wk.high.round(1), bye=wk.bye,
        injury_status=wk.player_id.map(ros_tbl.dropna(subset=["pid"]).set_index("pid").injury_status).fillna(""),
        stats_as_of=inputs_as_of, made_at=stamp))
    out_week.sort_values(["pos", "proj"], ascending=[True, False]).to_csv(PROCESSED / "proj_week.csv", index=False)

    # rest of season
    rate = sum(w * wk[c] for c, w in ROS_WEIGHTS.items())
    fallback = wk[["points_std", "xfp_std"]].mean(axis=1)
    wk["ros_ppg"] = np.where(wk[list(ROS_WEIGHTS)].notna().all(axis=1), rate,
                             np.where(wk.fp_pts.notna(), 0.5 * wk.fp_pts + 0.5 * fallback.fillna(wk.fp_pts), fallback))
    wk["ros_ppg"] = wk.ros_ppg.fillna(wk.points_std).fillna(0)
    imp = implied_totals([SEASON])
    games_left = imp[imp.week >= week].groupby("team").size()
    avail = availability(df, min(week, 17))
    wk["games_left"] = wk.team.map(games_left).fillna(0)
    wk["availability"] = wk.pos.map(avail).fillna(0.85)
    wk["ros_points"] = wk.ros_ppg * wk.games_left * wk.availability
    out_ros = pd.DataFrame(dict(
        season=SEASON, from_week=week, player_id=wk.player_id, player=wk.player_id.map(names), pos=wk.pos,
        team=wk.team, owner=wk.player_id.map(owner).fillna(""), ros_ppg=wk.ros_ppg.round(2),
        games_left=wk.games_left, availability=wk.availability.round(2), ros_points=wk.ros_points.round(1),
        stats_as_of=inputs_as_of, made_at=stamp))
    out_ros.sort_values("ros_points", ascending=False).to_csv(PROCESSED / "proj_ros.csv", index=False)

    fut = predict_future(SEASON)
    fut.insert(1, "player", fut.player_id.map(names))
    fut.insert(3, "owner", fut.player_id.map(owner).fillna(""))
    fut["made_at"] = stamp
    fut.to_csv(PROCESSED / "proj_future.csv", index=False)
    print(f"week {week}: {len(out_week)} players | ROS: {len(out_ros)} | future: {len(fut)}")


if __name__ == "__main__":
    main()
