"""Starter check from NFL depth charts: a backup quarterback is not projected as a starter.

    PYTHONPATH=src python -m dfo.projections.depth fit      # writes params_qb_depth.csv

Found in the Weeks 9-11 calibration review: from Tuesday until the week's FantasyPros ranks arrive, the
projection runs without ranks, and a quarterback who had started earlier in the season was projected on
those starts after losing the job (Malik Willis 14.6, Carson Wentz 12.2: neither played). The
rest-of-season rate had the same problem every day of the week, because its season-average and
expected-points terms come from his starts.

Fit 2021-2025, regular season. Depth charts: nflverse `depth_charts` (2021-2024 the weekly chart, QB
in the offense; 2025 on, ESPN snapshots, the last one before the team's game). The team's starter is
its highest-listed quarterback who is not Out, Doubtful or on a reserve list; everyone else is a backup.

| Role (2021-2025) | Player-weeks | Started (15+ attempts) | Points a game |
| --- | --- | --- | --- |
| Starter | 2,972 | 82% | 14.2 |
| Backup who started earlier this season | 813 | 15.6% (about 20% of later games) | 2.38 |
| Other backup, 2nd available | 2,165 | 4.2% | 0.73 |
| Other backup, 3rd or lower | 980 | 0.8% | 0.29 |

A quarterback FantasyPros ranks in the top 32 this week is never treated as a backup (the chart can lag a
starter's return). When FantasyPros ranks are in, they already know the role (ranked backups: projected 2.54, scored
2.42), so this week's projection is changed only when there is no rank: a backup's projection and
range are capped at his role's average and range. Rest of season: a backup's rate is his rate when he plays (season average and
expected points), times his chance of starting each remaining game (`params_qb_depth.csv`, by game).
"""
from __future__ import annotations

import datetime as dt
import sys
from functools import lru_cache

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.sources.fetch import as_of, nflverse

PARAMS = PROCESSED / "params_qb_depth.csv"
FIT_SEASONS = range(2021, 2026)
TEAM = {"LAR": "LA", "JAC": "JAX", "WSH": "WAS", "OAK": "LV", "SD": "LAC"}
STARTED = 15      # pass attempts
STARTER_RANK = 32
BACKUPS = ["former_starter", "backup2", "backup3"]


def charts(season: int, before: pd.DataFrame | None = None) -> pd.DataFrame:
    """QB depth by team and week: season, week, team, player_id, depth. `before` (season, week, team,
    gameday): for the ESPN snapshot seasons, the last snapshot before each game."""
    d = pd.read_csv(nflverse("depth_charts", f"depth_charts_{season}.csv"), low_memory=False)
    if "depth_position" in d:
        d = d[d.game_type.eq("REG") & d.depth_position.eq("QB") & d.formation.eq("Offense")]
        d = d.rename(columns={"club_code": "team", "gsis_id": "player_id", "depth_team": "depth"})
        out = d[["season", "week", "team", "player_id", "depth"]]
    else:
        d = d[d.pos_abb.eq("QB")].rename(columns={"gsis_id": "player_id", "pos_rank": "depth"})
        d["dt"] = pd.to_datetime(d.dt, utc=True)
        d["team"] = d.team.replace(TEAM)
        g = before[before.season == season].copy()
        g["gd"] = pd.to_datetime(g.gameday).dt.tz_localize("UTC")
        m = d.merge(g[["week", "team", "gd"]], on="team")
        m = m[m.dt < m.gd]
        m = m[m.dt == m.groupby(["week", "team"]).dt.transform("max")]
        out = m.assign(season=season)[["season", "week", "team", "player_id", "depth"]]
    out = out.dropna(subset=["player_id"]).assign(team=lambda x: x.team.replace(TEAM))
    return out.drop_duplicates(["season", "week", "team", "player_id"])


def roles(dc: pd.DataFrame, status: pd.DataFrame) -> pd.DataFrame:
    """Add `out`, `avail_rank` (1 = the starter) and `role` (starter / backup / out) to a depth chart.
    `status`: season, week, player_id, status (projections/injury.py names)."""
    x = dc.merge(status, on=["season", "week", "player_id"], how="left")
    x["out"] = x.status.fillna("").isin(["Out", "Doubtful", "IR", "Reserve", "Suspended"])
    x = x.sort_values(["season", "week", "team", "depth"])
    x["avail_rank"] = x[~x.out].groupby(["season", "week", "team"]).cumcount() + 1
    x["role"] = np.where(x.out, "out", np.where(x.avail_rank == 1, "starter", "backup"))
    return x


def history() -> pd.DataFrame:
    from dfo.projections import injury
    from dfo.projections.features import implied_totals
    imp = implied_totals(list(FIT_SEASONS))[["season", "week", "team", "gameday"]].drop_duplicates(["season", "week", "team"])
    dc = pd.concat([charts(s, imp) for s in FIT_SEASONS])
    st = []
    for s in FIT_SEASONS:
        x = injury._status(injury.reports(s), injury.reserve(s), dc[dc.season == s][["season", "week", "player_id"]])
        st.append(x[["season", "week", "player_id", "status"]])
    x = roles(dc, pd.concat(st).drop_duplicates(["season", "week", "player_id"]))
    att, pts = [], []
    for s in FIT_SEASONS:
        a = pd.read_csv(nflverse("stats_player", f"stats_player_week_{s}.csv"), low_memory=False)
        a = a[a.season_type.eq("REG")] if "season_type" in a else a
        att.append(a[["season", "week", "player_id", "attempts"]])
        p = pd.read_csv(PROCESSED / f"points_{s}.csv")
        pts.append(p[p.season_type.eq("REG")][["season", "week", "player_id", "points"]])
    x = x.merge(pd.concat(att), on=["season", "week", "player_id"], how="left").merge(pd.concat(pts), on=["season", "week", "player_id"], how="left")
    x[["attempts", "points"]] = x[["attempts", "points"]].fillna(0)
    x["started"] = (x.attempts >= STARTED).astype(int)
    x = x.sort_values(["season", "player_id", "week"])
    x["prev_starts"] = x.groupby(["season", "player_id"]).started.transform(lambda v: v.shift().fillna(0).cumsum())
    return x


def group(role, avail_rank, prev_starts) -> np.ndarray:
    """Backups who started earlier this season get the job back far more often (20% of later games vs
    11-17% for other second-stringers, 4-11% for third)."""
    role, rank, prev = np.asarray(role), np.asarray(avail_rank, float), np.asarray(prev_starts, float)
    return np.where(role == "backup", np.where(prev > 0, "former_starter", np.where(rank == 2, "backup2", "backup3")), role)


def fit() -> pd.DataFrame:
    x = history()
    x["group"] = group(x.role, x.avail_rank, x.prev_starts)
    rows = []
    for grp, g in x[x.group.isin(BACKUPS)].groupby("group"):
        rows.append(dict(group=grp, game=0, started=round(g.started.mean(), 4), points=round(g.points.mean(), 3),
                         p10=round(g.points.quantile(0.1), 2), p90=round(g.points.quantile(0.9), 2), n=len(g)))
    # later games: did this week's backup start game j from now (on any team; cut or traded = not started)
    seq = x.set_index(["season", "player_id", "week"]).started
    weeks = x[["season", "week", "team"]].drop_duplicates()
    for grp, g in x[x.group.isin(BACKUPS)].groupby("group"):
        for j in range(1, 13):
            v = []
            for r in g.itertuples():
                tw = weeks[(weeks.season == r.season) & (weeks.team == r.team) & (weeks.week > r.week)].week.sort_values()
                if len(tw) >= j:
                    v.append(seq.get((r.season, r.player_id, int(tw.iloc[j - 1])), 0))
            if len(v) >= 30:
                rows.append(dict(group=grp, game=j, started=round(float(np.mean(v)), 4), n=len(v)))
    # what a backup scores when he starts: a former starter his own rate; anyone else a first start
    rows.append(dict(group="first_start_points", game=-1, points=round(x[x.started.eq(1) & x.prev_starts.eq(0)].points.mean(), 3),
                     n=int((x.started.eq(1) & x.prev_starts.eq(0)).sum())))
    out = pd.DataFrame(rows)
    out["fit_on"], out["fit_date"] = "2021-2025 REG, QB depth charts (nflverse)", dt.date.today().isoformat()
    out.to_csv(PARAMS, index=False)
    return out


@lru_cache(maxsize=1)
def params() -> pd.DataFrame:
    return pd.read_csv(PARAMS)


def start_curve(group: str, games: int) -> np.ndarray:
    p = params()
    g = p[p.group == group].set_index("game").started
    return g.reindex(range(games)).ffill().fillna(g.iloc[-1]).to_numpy(float).copy()


def live_roles(season: int, week: int, status: pd.DataFrame, now: dt.datetime | None = None) -> pd.DataFrame:
    """The latest depth chart (ESPN snapshot at or before `now`) with roles for the coming week, indexed
    by player id: team, depth, avail_rank, role, group, chart_as_of."""
    now = now or dt.datetime.now(dt.timezone.utc)
    d = pd.read_csv(nflverse("depth_charts", f"depth_charts_{season}.csv"), low_memory=False)
    d = d[d.pos_abb.eq("QB")].rename(columns={"gsis_id": "player_id", "pos_rank": "depth"}).dropna(subset=["player_id"])
    d["dt"] = pd.to_datetime(d.dt, utc=True)
    d = d[d.dt <= pd.Timestamp(now)]
    d["team"] = d.team.replace(TEAM)
    d = d[d.dt == d.groupby("team").dt.transform("max")]
    chart_as_of = str(d.dt.max()) if len(d) else ""
    dc = d.assign(season=season, week=week)[["season", "week", "team", "player_id", "depth"]].drop_duplicates(["team", "player_id"])
    st = status.reset_index().rename(columns={"index": "player_id"})[["player_id", "status"]].assign(season=season, week=week)
    x = roles(dc, st)
    a = pd.read_csv(nflverse("stats_player", f"stats_player_week_{season}.csv"), low_memory=False)
    a = a[(a.season_type.eq("REG") if "season_type" in a else True) & (a.week < week) & (a.attempts >= STARTED)]
    x["prev_starts"] = x.player_id.map(a.groupby("player_id").size()).fillna(0)
    x["group"] = group(x.role, x.avail_rank, x.prev_starts)
    x["chart_as_of"] = f"depth chart {chart_as_of} (file {as_of(nflverse('depth_charts', f'depth_charts_{season}.csv'))})"
    return x.drop_duplicates("player_id").set_index("player_id")


def apply_week(wk: pd.DataFrame, r: pd.DataFrame) -> pd.DataFrame:
    """Unranked backup QBs: this week's projection and range from their role (ranked ones are left to
    FantasyPros, which already knows the role)."""
    wk = wk.copy()
    p = params().query("game == 0").set_index("group")
    grp = wk.player_id.map(r.group).where(wk.pos.eq("QB"))
    team_ok = wk.player_id.map(r.team).eq(wk.team)              # on the chart of the team he is projected for
    # a backup on the chart whom FantasyPros ranks as a starter this week (top 32) is not treated as one:
    # the rank has the news the chart lacks (a starter returning from injury, a benching reversed)
    ranked_starter = wk.ecr.le(STARTER_RANK)
    bk = grp.isin(BACKUPS) & team_ok
    wk["qb_role"] = np.where(wk.pos.eq("QB"), np.where(bk & ranked_starter, "starter (FantasyPros rank)",
                                                       np.where(bk | ~grp.isin(BACKUPS), grp.fillna(""), "")), "")
    m = bk & ~ranked_starter & wk.ecr.isna() & ~wk.bye.astype(bool)
    for c, col in (("proj", "points"), ("low", "p10"), ("high", "p90")):     # a cap: never raised
        wk.loc[m, c] = np.minimum(wk.loc[m, c].to_numpy(float), grp[m].map(p[col]).to_numpy(float))
    wk.loc[m, "low"] = np.minimum(wk.loc[m, "low"], wk.loc[m, "proj"])
    wk.loc[m, "high"] = np.maximum(wk.loc[m, "high"], wk.loc[m, "proj"])
    return wk


def ros_backups(wk: pd.DataFrame, game_weeks: dict, done: set, prof: pd.DataFrame, healthy: pd.Series):
    """Backup QBs, rest of season: his rate if he starts x his chance of starting each remaining game (and
    of being healthy, when he has an injury status). The rate: a former starter (started this season) his
    own season average and expected points (no FantasyPros term: his rank prices him as a backup); anyone
    else what backups scored in a first start, 2021-2025 (14.7). Back-test, every backup at Weeks 6/8/10
    2021-2025 (617): points per remaining team game 1.91 / 1.10 / 2.58 predicted vs 1.78 / 1.02 / 3.11
    actual (second-string / third / former starter); the old rate x position availability gave a former
    starter 9.2. Returns the updated rate, availability and weekly profile."""
    wk = wk.copy()
    rows = []
    keep = prof[~prof.player_id.isin(wk.player_id[wk.qb_role.isin(BACKUPS)])] if len(prof) else prof
    for i, r in wk[wk.qb_role.isin(BACKUPS)].iterrows():
        gw = list(game_weeks.get(r.team, []))
        if not gw:
            continue
        d = int(r.team in done)
        c = start_curve(r.qb_role, len(gw) + d)[d:]
        mine = prof[prof.player_id.eq(r.player_id)] if len(prof) else prof
        if len(mine):                                     # injured too: times his chance of being back
            h = float(healthy.get("QB", 0.8))
            c = c * (mine.set_index("week").play.reindex(gw).fillna(h).to_numpy() / h)
        own = np.nanmean([r.points_std, r.xfp_std]) if pd.notna(r.points_std) or pd.notna(r.xfp_std) else np.nan
        first = float(params().query("group == 'first_start_points'").points.iloc[0])
        per_start = own if r.qb_role == "former_starter" and pd.notna(own) else first
        wk.loc[i, "ros_ppg"] = float(per_start)
        wk.loc[i, "availability"] = float(np.clip(c, 0, 1).mean())
        rows += [dict(player_id=r.player_id, week=w, play=round(float(v), 3), injury_status=r.injury_status or "backup QB")
                 for w, v in zip(gw, np.clip(c, 0, 1))]
    return wk, pd.concat([keep, pd.DataFrame(rows)], ignore_index=True) if rows else prof


if __name__ == "__main__":
    if sys.argv[1:2] == ["fit"]:
        print(fit().to_string())
