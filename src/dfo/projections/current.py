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


def ros_rate(wk: pd.DataFrame, fp_prev: pd.Series | None = None) -> pd.Series:
    """Rest-of-season points per game. The FantasyPros term is this week's rank in points; when this
    week's ranks are not out yet (Tue-Thu) it is last week's rank, one week stale. Backtest 2021-2025
    at weeks 6/8/10 (docs/projections.md): last week's rank MAE 2.33/2.22/2.34 vs 2.55/2.37/2.47 with
    no rank term, so a one-week-old rank is kept rather than dropped (fixed 2026-11-18)."""
    fp = wk.fp_pts if fp_prev is None else wk.fp_pts.fillna(fp_prev.reindex(wk.index))
    x = wk.assign(fp_pts=fp)
    rate = sum(w * x[c] for c, w in ROS_WEIGHTS.items())
    fallback = x[["points_std", "xfp_std"]].mean(axis=1)
    out = np.where(x[list(ROS_WEIGHTS)].notna().all(axis=1), rate,
                   np.where(x.fp_pts.notna(), 0.5 * x.fp_pts + 0.5 * fallback.fillna(x.fp_pts), fallback))
    return pd.Series(out, index=wk.index).fillna(wk.points_std).fillna(0).clip(lower=0)   # never below 0


def next_week(today: dt.date | None = None) -> int:
    """The first regular-season week with games still to be played (a week whose Thursday game is
    already scored is still the current week until its last game)."""
    today = today or dt.datetime.now(dt.timezone.utc).date()
    imp = implied_totals([SEASON])
    last = pd.to_datetime(imp.groupby("week").gameday.max()).dt.date
    upcoming = last[last >= today]
    if len(upcoming):
        return int(upcoming.index.min())
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
    # FantasyPros weekly ranks for the coming week arrive Thursday or Friday. Until then the free-agent
    # pool would shrink to rostered players only (no waiver candidates), so the players ranked in the
    # latest earlier week are kept in the pool and projected without ranks (their rank is stale: not used).
    ranks_note, prev = "", None
    from dfo.projections.ecr import weekly_ecr
    ecr = weekly_ecr()
    ecr = ecr[ecr.season == SEASON]
    if not (ecr.week == week).any() and (ecr.week < week).any():
        prev = int(ecr[ecr.week < week].week.max())
        # the last two ranked weeks: one week's file can be partial (Week 11 2026: 320 players against ~490)
        carry = ecr[(ecr.week >= prev - 1) & (ecr.week <= prev) & ~ecr.player_id.isin(extra.player_id)][["player_id", "pos"]].drop_duplicates("player_id")
        carry = carry.assign(season=SEASON, week=week, team=carry.player_id.map(last_team))
        carry.loc[carry.pos == "DEF", "team"] = carry.player_id.str.replace("DEF-", "", regex=False)
        # every team defense is always in the pool (a unit on bye last week has no rank to carry)
        defs = pd.DataFrame(dict(player_id="DEF-" + pd.Series(sorted(implied_totals([SEASON]).team.unique())), pos="DEF"))
        defs = defs[~defs.player_id.isin(extra.player_id) & ~defs.player_id.isin(carry.player_id)]
        carry = pd.concat([carry, defs.assign(season=SEASON, week=week, team=defs.player_id.str[4:])], ignore_index=True)
        extra = pd.concat([extra, carry[carry.team.notna()][extra.columns]], ignore_index=True)
        ranks_note = (f"Week {week} FantasyPros ranks not out yet (usually Thursday or Friday); "
                      f"pool from Week {prev} ranks, projected without ranks; rest-of-season rates use "
                      f"Week {prev} ranks (one week stale)")
        print(ranks_note)
    df = build(extra=extra)
    train = df[(df.season < SEASON) | ((df.season == SEASON) & (df.week < week))]
    model = WeeklyModel(train)
    wk = df[(df.season == SEASON) & (df.week == week)].copy()
    p = model.predict(wk, RangeMap.from_backtest())
    wk = wk.join(p[["proj", "low", "high", "fp_pts"]])
    wk["bye"] = wk.implied.isna()
    wk.loc[wk.bye, ["proj", "low", "high"]] = 0.0
    # injury status: chance of playing this week, and of each remaining game (projections/injury.py)
    from dfo.projections import injury
    site = ros_tbl.dropna(subset=["pid"]).drop_duplicates("pid").set_index("pid").injury_status
    status = injury.live_status(SEASON, week, site, injury.team_games([SEASON]))
    # starter check: unranked backup QBs get their role's projection (projections/depth.py)
    from dfo.projections import depth
    qb = depth.live_roles(SEASON, week, status)
    wk = depth.apply_week(wk, qb)
    wk = injury.apply_week(wk, status)
    status_as_of = str(status.status_as_of.iloc[0]) if len(status) else ""
    names = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").name
    owner = ros_tbl.dropna(subset=["pid"]).set_index("pid").owner
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    inputs_as_of = as_of(nflverse("stats_player", f"stats_player_week_{SEASON}.csv"))
    out_week = pd.DataFrame(dict(
        season=SEASON, week=week, player_id=wk.player_id, player=wk.player_id.map(names), pos=wk.pos,
        team=wk.team, opp=wk.opp, owner=wk.player_id.map(owner).fillna(""), fp_rank=wk.ecr.round(1),
        fp_pts=wk.fp_pts.round(1), proj=wk.proj.round(1), low=wk.low.round(1), high=wk.high.round(1), bye=wk.bye,
        injury_status=wk.injury_status, play_prob=wk.play_prob, proj_if_plays=wk.proj_if_plays.round(1),
        practice=wk.practice, status_source=wk.status_source, games_missed=wk.games_missed, ir_games=wk.ir_games,
        qb_role=wk.qb_role, ranks_note=ranks_note, stats_as_of=inputs_as_of,
        status_as_of=status_as_of + "; " + str(qb.chart_as_of.iloc[0] if len(qb) else ""), made_at=stamp))
    out_week.sort_values(["pos", "proj"], ascending=[True, False]).to_csv(PROCESSED / "proj_week.csv", index=False)
    # keep the last projection made before each player's game, for the weekly scorecard
    from dfo.projections import scorecard
    scorecard.freeze(out_week)

    # rest of season (this week's ranks missing: last week's rank, labeled stale in rank_week)
    fp_prev, rank_week = None, pd.Series(np.where(wk.fp_pts.notna(), week, np.nan), index=wk.index)
    if prev is not None:
        last = ecr[ecr.week == prev].drop_duplicates("player_id").set_index("player_id").ecr
        stale = wk.assign(ecr=wk.player_id.map(last))
        fp_prev = model.fp_points(stale)
        rank_week = rank_week.fillna(pd.Series(np.where(fp_prev.notna(), prev, np.nan), index=wk.index))
    wk["ros_ppg"] = ros_rate(wk, fp_prev)
    imp = implied_totals([SEASON])
    today = dt.datetime.now(dt.timezone.utc).date()
    left = imp[(imp.week >= week) & (pd.to_datetime(imp.gameday).dt.date >= today)].sort_values("week")
    games_left = left.groupby("team").size()
    avail = availability(df, min(week, 17))
    wk["games_left"] = wk.team.map(games_left).fillna(0)
    # availability: the position's rate for healthy players; status players (and players who missed games
    # in a row) get the fitted chance of playing each remaining game, IR minimum included
    done = set(imp[(imp.week == week) & (pd.to_datetime(imp.gameday).dt.date < today)].team)
    wk["availability"], prof = injury.ros_availability(wk, left.groupby("team").week.apply(list).to_dict(), done,
                                                       avail)
    wk, prof = depth.ros_backups(wk, left.groupby("team").week.apply(list).to_dict(), done, prof, avail)
    prof.assign(from_week=week, made_at=stamp, status_as_of=status_as_of).to_csv(PROCESSED / "proj_avail.csv", index=False)
    wk["ros_points"] = wk.ros_ppg * wk.games_left * wk.availability
    out_ros = pd.DataFrame(dict(
        season=SEASON, from_week=week, player_id=wk.player_id, player=wk.player_id.map(names), pos=wk.pos,
        team=wk.team, owner=wk.player_id.map(owner).fillna(""), ros_ppg=wk.ros_ppg.round(2),
        games_left=wk.games_left, availability=wk.availability.round(2), ros_points=wk.ros_points.round(1),
        fp_rank_week=rank_week.astype("Int64"), stats_as_of=inputs_as_of, made_at=stamp))
    out_ros.sort_values("ros_points", ascending=False).to_csv(PROCESSED / "proj_ros.csv", index=False)

    fut = predict_future(SEASON)
    fut.insert(1, "player", fut.player_id.map(names))
    fut.insert(3, "owner", fut.player_id.map(owner).fillna(""))
    fut["made_at"] = stamp
    fut.to_csv(PROCESSED / "proj_future.csv", index=False)
    print(f"week {week}: {len(out_week)} players | ROS: {len(out_ros)} | future: {len(fut)}")


if __name__ == "__main__":
    main()
