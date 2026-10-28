"""Weekly feature table for projections. Every feature uses only information available before the week.

Rows: one per player-week for QB/RB/WR/TE/K and one per team defense-week, for regular-season weeks
2016 onward. The target `points` is actual league points (0 when a FantasyPros-ranked player did not
record a stat, e.g. a late scratch).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.projections.ecr import weekly_ecr
from dfo.sources.fetch import CACHE, nflverse

FIRST_SEASON = 2016
POS = ["QB", "RB", "WR", "TE", "K"]


def _xfp(ep: pd.DataFrame) -> pd.Series:
    """Expected league points from ffopportunity's expected stat lines (league scoring)."""
    s = load_config("league")["scoring"]
    g = lambda c: ep[c].fillna(0) if c in ep else 0
    return (g("pass_yards_gained_exp") / s["pass_yds_per_pt"] + g("pass_touchdown_exp") * s["pass_td"]
            + g("pass_interception_exp") * s["pass_int"]
            + g("rush_yards_gained_exp") / s["rush_yds_per_pt"] + g("rush_touchdown_exp") * s["rush_td"]
            + g("receptions_exp") * s["reception"] + g("rec_yards_gained_exp") / s["rec_yds_per_pt"]
            + g("rec_touchdown_exp") * s["rec_td"]
            + (g("pass_two_point_conv_exp") + g("rush_two_point_conv_exp") + g("rec_two_point_conv_exp")) * s["two_pt"])


def implied_totals(seasons: list[int]) -> pd.DataFrame:
    g = pd.read_csv(nflverse("schedules", "games.csv"), low_memory=False)
    g = g[g.season.isin(seasons) & (g.game_type == "REG")]
    home = pd.DataFrame(dict(season=g.season, week=g.week, team=g.home_team, opp=g.away_team, home=1,
                             implied=(g.total_line + g.spread_line) / 2,
                             opp_implied=(g.total_line - g.spread_line) / 2, gameday=g.gameday))
    away = pd.DataFrame(dict(season=g.season, week=g.week, team=g.away_team, opp=g.home_team, home=0,
                             implied=(g.total_line - g.spread_line) / 2,
                             opp_implied=(g.total_line + g.spread_line) / 2, gameday=g.gameday))
    return pd.concat([home, away], ignore_index=True)


def _points(seasons: list[int]) -> pd.DataFrame:
    frames = []
    for s in seasons:
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", low_memory=False)
        frames.append(p[p.season_type == "REG"])
    return pd.concat(frames, ignore_index=True)


def _usage(seasons: list[int]) -> pd.DataFrame:
    frames = []
    for s in seasons:
        st = pd.read_csv(nflverse("stats_player", f"stats_player_week_{s}.csv"), low_memory=False)
        st = st[st.season_type == "REG"][["player_id", "season", "week", "team", "opponent_team", "targets",
                                          "carries", "attempts", "target_share", "receptions"]]
        ep_path = CACHE / "ffverse" / f"ep_weekly_{s}.csv"
        if ep_path.exists():
            ep = pd.read_csv(ep_path, low_memory=False)
            ep = ep[ep.week <= 18].assign(xfp=lambda d: _xfp(d))
            ep["carry_share"] = ep.rush_attempt / ep.rush_attempt_team.replace(0, np.nan)
            st = st.merge(ep[["player_id", "season", "week", "xfp", "carry_share"]],
                          on=["player_id", "season", "week"], how="left")
        frames.append(st)
    return pd.concat(frames, ignore_index=True)


def _trailing(df: pd.DataFrame, col: str, key: list[str]) -> pd.DataFrame:
    """Season-to-date mean and last-3-games mean of `col` over games actually played, as known
    before each row's week. Rows where the player did not play do not count as games."""
    df = df.sort_values(key + ["season", "week"]).copy()
    val = df[col].where(df["played"] == 1)
    played = df["played"] == 1
    through_std = val.groupby([df[k] for k in key] + [df["season"]]).transform(lambda x: x.expanding().mean())
    through_l3 = val.groupby([df[k] for k in key]).transform(
        lambda x: x.dropna().rolling(3, min_periods=1).mean().reindex(x.index))
    through_std = through_std.where(played)
    through_l3 = through_l3.where(played)
    df[f"{col}_std"] = through_std.groupby([df[k] for k in key] + [df["season"]]).transform(
        lambda x: x.ffill().shift(1))
    df[f"{col}_l3"] = through_l3.groupby([df[k] for k in key]).transform(lambda x: x.ffill().shift(1))
    return df


def build(seasons: list[int] | None = None, extra: pd.DataFrame | None = None) -> pd.DataFrame:
    """`extra`: optional (season, week, player_id, pos, team) rows to project that have no stat line or
    FantasyPros rank yet (e.g. rostered bench players in the upcoming week)."""
    seasons = seasons or list(range(FIRST_SEASON, 2027))
    pts = _points(seasons)
    use = _usage(seasons)
    imp = implied_totals(seasons)

    # ---------------- offense + kickers
    off = pts[pts.pos.isin(POS)].merge(use, on=["player_id", "season", "week"], how="left",
                                       suffixes=("", "_u"))
    off["played"] = 1
    ecr = weekly_ecr()
    if len(ecr):
        e = ecr[ecr.pos.isin(POS)][["season", "week", "player_id", "pos", "ecr", "ecr_sd", "scrape_date"]
                                   + (["fp_proj"] if "fp_proj" in ecr else [])]
        off = off.merge(e, on=["season", "week", "player_id", "pos"], how="outer")
        off["played"] = off["played"].fillna(0)
        off["points"] = off["points"].fillna(0.0)
    if extra is not None and len(extra):
        x = extra[extra.pos.isin(POS)]
        x = x.merge(off[["season", "week", "player_id"]], on=["season", "week", "player_id"], how="left", indicator=True)
        x = x[x._merge == "left_only"].drop(columns="_merge").assign(played=0, points=0.0)
        off = pd.concat([off, x], ignore_index=True)
    off = off.sort_values(["player_id", "season", "week"])
    # carry team forward for ranked rows with no stat line (a team given on an `extra` row wins)
    off["team"] = off.groupby("player_id")["team"].transform(lambda x: x.ffill().bfill())
    off["points"] = off["points"].fillna(0.0)
    for c in ["points", "xfp", "targets", "carries", "target_share", "carry_share", "attempts"]:
        if c in off:
            off = _trailing(off, c, ["player_id"])
    off["games_std"] = off.groupby(["player_id", "season"])["played"].transform(lambda x: x.shift(1).fillna(0).cumsum())
    season_ppg = (off[off.played == 1].groupby(["player_id", "season"]).points.mean()
                  .rename("prev_ppg").reset_index().assign(season=lambda d: d.season + 1))
    off = off.merge(season_ppg, on=["player_id", "season"], how="left")
    off = off.merge(imp[["season", "week", "team", "opp", "home", "implied", "opp_implied", "gameday"]],
                    on=["season", "week", "team"], how="left")
    # opponent's points allowed to the position so far this season
    # (through each week, then looked up as of the previous week, so upcoming weeks get a value too)
    allowed = (off[off.played == 1].groupby(["season", "week", "opp", "pos"]).points.sum().reset_index()
               .sort_values(["opp", "pos", "season", "week"]))
    allowed["through"] = allowed.groupby(["opp", "pos", "season"]).points.transform(lambda x: x.expanding().mean())
    off = off.sort_values("week")
    off = pd.merge_asof(off, allowed[["season", "week", "opp", "pos", "through"]].sort_values("week")
                        .rename(columns={"through": "opp_allowed_std"}),
                        on="week", by=["season", "opp", "pos"], allow_exact_matches=False)
    team_imp = imp.sort_values(["team", "season", "week"])
    team_imp["implied_std"] = team_imp.groupby(["team", "season"]).implied.transform(
        lambda x: x.shift(1).expanding().mean())
    off = off.merge(team_imp[["season", "week", "team", "implied_std"]], on=["season", "week", "team"], how="left")
    off["implied_rel"] = off.implied / off.implied_std

    # ---------------- team defenses
    d = pts[pts.pos == "DEF"].copy()
    d["played"] = 1
    if len(ecr):
        e = ecr[ecr.pos == "DEF"][["season", "week", "player_id", "ecr", "ecr_sd", "scrape_date"]
                                  + (["fp_proj"] if "fp_proj" in ecr else [])]
        d = d.merge(e, on=["season", "week", "player_id"], how="outer")
    if extra is not None and len(extra):
        x = extra[extra.pos == "DEF"][["season", "week", "player_id"]]
        d = pd.concat([d, x.merge(d[["season", "week", "player_id"]], how="left", indicator=True)
                       .query("_merge == 'left_only'").drop(columns="_merge")], ignore_index=True)
    d["played"] = d["played"].fillna(0)
    d["points"] = d["points"].fillna(0.0)
    d["pos"] = "DEF"
    d["team"] = d.player_id.str.replace("DEF-", "", regex=False)
    d = _trailing(d, "points", ["player_id"])
    d = d.merge(imp, on=["season", "week", "team"], how="left")
    prev = (d.groupby(["player_id", "season"]).points.mean().rename("prev_ppg").reset_index()
            .assign(season=lambda x: x.season + 1))
    d = d.merge(prev, on=["player_id", "season"], how="left")

    df = pd.concat([off, d], ignore_index=True, sort=False)
    df = df[df.week.between(1, 18)]
    df["ecr_log"] = np.log(df["ecr"])
    df["pos_code"] = df.pos.map({p: i for i, p in enumerate(POS + ["DEF"])})
    return df.reset_index(drop=True)


FEATURES = ["pos_code", "points_std", "points_l3", "games_std", "prev_ppg", "xfp_std", "xfp_l3",
            "targets_std", "targets_l3", "carries_std", "carries_l3", "target_share_std", "carry_share_std",
            "attempts_std", "implied", "opp_implied", "implied_rel", "home", "opp_allowed_std"]
ECR_FEATURES = ["ecr_log", "ecr_sd"]
