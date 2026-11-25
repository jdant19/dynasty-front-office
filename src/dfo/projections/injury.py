"""Injury status in the projections: who plays this week, and how many of the remaining games.

    PYTHONPATH=src python -m dfo.projections.injury fit     # writes the two parameter files below
    PYTHONPATH=src python -m dfo.projections.injury check   # how well the fit matches 2021-2025

Fit from 2021-2025 (regular season), joining the NFL's injury reports (nflverse `injuries`: the final
game status and practice participation) and weekly roster status (nflverse `weekly_rosters`: reserve
lists, R01 = injured reserve) to who actually played (`played` in the projection features):

- `params_injury_week.csv`: the chance of playing this week. Out, Doubtful, IR and the other reserve
  lists are 0 (2021-2025, ranked players: Out 0 of 700 played, Doubtful 1 of 172, IR 0 of 222).
  Questionable is a logistic fit on position, projection and practice participation (1,675 player-weeks:
  62% played overall, QBs 40%, players who did not practice 43%, full practice 73%). When a
  Questionable player plays he scores like anyone else (actual/projected 1.18 vs 1.14 for unlisted
  players), so the projection is his chance of playing times his projection.
- `params_injury_return.csv`: the share who played each later game (the next, the one after, ...), by
  status and games already missed in a row (QB/RB/WR/TE ranked earlier in the season; it includes
  getting hurt again after coming back). A player on IR cannot return before the NFL's minimum of 4
  games, enforced on top.

`profile()` turns these into a chance of playing in each remaining team game; rest-of-season totals use
its mean, and the weekly grids (waivers, trades) and the season simulator use it week by week, so a
player due back for the playoffs keeps that value.

Live status: the league site (rostered players, the most current) and the NFL's report and reserve
lists for the coming week (everyone, free agents included). The more serious of the two is used, except
that the site wins on IR for rostered players (it moves the moment a player is activated).
"""
from __future__ import annotations

import datetime as dt
import sys
from functools import lru_cache

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.sources.fetch import as_of, nflverse

WEEK = PROCESSED / "params_injury_week.csv"
RETURN = PROCESSED / "params_injury_return.csv"
FIT_SEASONS = range(2021, 2026)
IR_MIN_GAMES = 4                       # NFL rule since 2022
NEVER = {"Out", "Doubtful", "IR", "Reserve", "Suspended"}
SEVERITY = {"": 0, "Questionable": 1, "Doubtful": 2, "Out": 3, "Suspended": 3, "Reserve": 4, "IR": 5}
# league site / Yahoo designations -> the statuses fit here
SITE = {"Q": "Questionable", "Questionable": "Questionable", "D": "Doubtful", "Doubtful": "Doubtful",
        "O": "Out", "Out": "Out", "DNR": "Out", "IR": "IR", "IR-R": "IR", "IR-LT": "IR", "IR-NFI": "IR",
        "PUP": "IR", "PUP-R": "IR", "PUP-P": "Reserve", "NFI": "IR", "NFI-R": "IR",   # PUP/NFI: same 4-game minimum
        "NA": "Reserve", "SUSP": "Suspended", "Suspended": "Suspended", "COV": "Out"}
POS = ["QB", "RB", "WR", "TE", "K"]
PRACTICE = ["dnp", "limited", "full", "none"]


# ---------------------------------------------------------------- inputs
def _practice(s: pd.Series) -> pd.Series:
    t = s.fillna("").astype(str)
    return np.select([t.str.contains("Did Not"), t.str.contains("Limited"), t.str.contains("Full")],
                     ["dnp", "limited", "full"], "none")


def reports(season: int) -> pd.DataFrame:
    d = pd.read_csv(nflverse("injuries", f"injuries_{season}.csv"), low_memory=False)
    gt = "season_type" if "season_type" in d else "game_type"
    d = d[d[gt].eq("REG")].rename(columns={"gsis_id": "player_id"})
    d = d.assign(report=d.report_status.where(d.report_status.isin(["Questionable", "Doubtful", "Out"]), ""),
                 practice=_practice(d.practice_status))
    return d[["season", "week", "player_id", "report", "practice"]].drop_duplicates(["season", "week", "player_id"], keep="last")


def reserve(season: int) -> pd.DataFrame:
    """Weekly reserve status: IR (R01, and R48 designated to return, still inactive), other reserve lists."""
    d = pd.read_csv(nflverse("weekly_rosters", f"roster_weekly_{season}.csv"), low_memory=False)
    d = d[d.game_type.eq("REG")].rename(columns={"gsis_id": "player_id"})
    c = d.status_description_abbr.astype(str)
    d["reserve"] = np.select([c.isin(["R01", "R48"]), c.str.startswith("R")], ["IR", "Reserve"], "")
    return d[["season", "week", "player_id", "reserve"]].drop_duplicates(["season", "week", "player_id"], keep="last")


def _status(rep: pd.DataFrame, res: pd.DataFrame, base: pd.DataFrame) -> pd.DataFrame:
    x = base.merge(rep, on=["season", "week", "player_id"], how="left").merge(res, on=["season", "week", "player_id"], how="left")
    x[["report", "reserve"]] = x[["report", "reserve"]].fillna("")
    x["practice"] = x.practice.fillna("none")
    x["status"] = np.where(x.reserve.ne(""), x.reserve, x.report)
    return x


def _streaks(df: pd.DataFrame) -> pd.DataFrame:
    """For each team game: games missed in a row before it, and games on IR in a row (this one included)."""
    df = df.sort_values(["player_id", "season", "week"]).copy()
    g = df.groupby(["player_id", "season"])
    miss = (df.played == 0).astype(int)
    run = miss.groupby((miss != miss.groupby([df.player_id, df.season]).shift()).cumsum()).cumsum() * miss
    df["missed_before"] = run.groupby([df.player_id, df.season]).shift().fillna(0).astype(int)
    ir = df.status.eq("IR").astype(int)
    df["ir_games"] = (ir.groupby((ir != ir.groupby([df.player_id, df.season]).shift()).cumsum()).cumsum() * ir).astype(int)
    df["game_no"] = g.cumcount()
    return df


def team_games(seasons) -> pd.DataFrame:
    """Every team game of every rostered player (on a reserve list or not): season, week, player_id, pos,
    played (a box-score row in points_<season>.csv, as in the projection features). The projection
    feature rows alone skip weeks a player was neither ranked nor in the box score (most of an IR stint)."""
    from dfo.projections.features import implied_totals
    seasons = list(seasons)
    games = implied_totals(seasons)[["season", "week", "team"]].drop_duplicates()
    rost, pts = [], []
    for s in seasons:
        d = pd.read_csv(nflverse("weekly_rosters", f"roster_weekly_{s}.csv"), low_memory=False)
        d = d[d.game_type.eq("REG")].rename(columns={"gsis_id": "player_id", "position": "pos"})
        rost.append(d[["season", "week", "player_id", "team", "pos"]].dropna(subset=["player_id"]))
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", usecols=["season", "season_type", "week", "player_id", "pos", "points"])
        pts.append(p[p.season_type.eq("REG")].drop(columns="season_type"))
    rost = pd.concat(rost).drop_duplicates(["season", "week", "player_id"]).merge(games, on=["season", "week", "team"])
    pts = pd.concat(pts).drop_duplicates(["season", "week", "player_id"]).rename(columns={"pos": "ppos"})
    x = rost.merge(pts, on=["season", "week", "player_id"], how="left")
    x["played"] = x.points.notna().astype(int)
    x["pos"] = x.ppos.fillna(x.pos)
    return x[x.pos.isin(POS)].drop(columns=["ppos"]).assign(points=lambda d: d.points.fillna(0.0))


def history() -> pd.DataFrame:
    """Every 2021-2025 team game of QB/RB/WR/TE/K players, with status, practice, streaks, FantasyPros
    rank and the out-of-sample weekly projection where there is one."""
    from dfo.projections.weekly import features
    f = features()
    f = f[f.season.isin(FIT_SEASONS)][["season", "week", "player_id", "ecr"]].drop_duplicates(["season", "week", "player_id"])
    base = team_games(FIT_SEASONS).drop(columns="team").merge(f, on=["season", "week", "player_id"], how="left")
    rep = pd.concat([reports(s) for s in FIT_SEASONS])
    res = pd.concat([reserve(s) for s in FIT_SEASONS])
    x = _streaks(_status(rep, res, base))
    bt = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv", usecols=["season", "week", "player_id", "blend"])
    return x.merge(bt, on=["season", "week", "player_id"], how="left")


# ---------------------------------------------------------------- fit
def _design(pos, proj, practice) -> np.ndarray:
    pos, practice = np.asarray(pos), np.asarray(practice)
    lp = np.log1p(np.clip(np.asarray(proj, float), 0, 30))
    cols = [np.ones(len(lp)), lp]
    cols += [(pos == p).astype(float) for p in POS[1:]]          # QB is the base
    cols += [(practice == p).astype(float) for p in PRACTICE[:2]]  # full/none the base
    cols += [(practice == "none").astype(float)]
    return np.column_stack(cols)


TERMS = ["intercept", "log1p_proj"] + [f"pos_{p}" for p in POS[1:]] + ["practice_dnp", "practice_limited", "practice_none"]


def fit() -> tuple[pd.DataFrame, pd.DataFrame]:
    from sklearn.linear_model import LogisticRegression
    x = history()
    today = dt.date.today().isoformat()
    # this week: Questionable, ranked players with an out-of-sample projection
    q = x[x.status.eq("Questionable") & x.blend.notna()]
    lr = LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000).fit(_design(q.pos, q.blend, q.practice), q.played)
    week = pd.DataFrame(dict(term=TERMS, coef=np.round(lr.coef_[0], 4)))
    # before the final report (practice participation unknown): position and projection only
    lr0 = LogisticRegression(C=1.0, fit_intercept=False, max_iter=2000).fit(_design(q.pos, q.blend, q.practice)[:, :len(TERMS) - 3], q.played)
    week = pd.concat([week, pd.DataFrame(dict(term=[f"nopractice_{t}" for t in TERMS[:-3]], coef=np.round(lr0.coef_[0], 4)))],
                     ignore_index=True)
    rates = (x[x.blend.notna()].assign(status=lambda d: d.status.replace("", "none"))
             .groupby("status").agg(n=("played", "size"), played=("played", "mean")).round(4))
    for s, r in rates.iterrows():
        week.loc[len(week)] = dict(term=f"rate_{s}", coef=r.played)
        week.loc[len(week)] = dict(term=f"n_{s}", coef=r.n)
    week["fit_on"], week["fit_date"] = "2021-2025 REG, ranked players (proj_weekly_backtest)", today
    week.to_csv(WEEK, index=False)

    # remaining games: QB/RB/WR/TE who were ranked (top 24 QB/TE, 48 RB, 60 WR) earlier that season
    cap = {"QB": 24, "RB": 48, "WR": 60, "TE": 24}
    s = x[x.pos.isin(cap)].sort_values(["player_id", "season", "week"])
    good = s.ecr <= s.pos.map(cap)
    s = s[good.groupby([s.player_id, s.season]).transform(lambda v: v.shift().fillna(False).cummax()).astype(bool)]
    rows = []
    for (sid, pid), g in s.groupby(["season", "player_id"]):
        p = g.played.to_numpy()
        for i, r in enumerate(g.itertuples()):
            if r.status == "" and r.missed_before == 0:
                continue
            grp = r.status or "none"
            prior = min(r.ir_games - 1, 6) if grp == "IR" else min(r.missed_before, 3)
            for j, v in enumerate(p[i:i + 18]):
                rows.append((grp, prior, j, v))
    ev = pd.DataFrame(rows, columns=["group", "prior", "game", "played"])
    ret = ev.groupby(["group", "prior", "game"]).played.agg(play="mean", n="size").reset_index()
    ret = ret[ret.groupby(["group", "prior"]).n.transform("max") >= 15]
    ret["play"] = ret.play.round(4)
    ret["fit_on"], ret["fit_date"] = "2021-2025 REG, QB/RB/WR/TE ranked earlier that season", today
    ret.to_csv(RETURN, index=False)
    return week, ret


# ---------------------------------------------------------------- apply
@lru_cache(maxsize=1)
def _week_params() -> pd.Series:
    return pd.read_csv(WEEK).set_index("term").coef


@lru_cache(maxsize=1)
def _return_params() -> pd.DataFrame:
    return pd.read_csv(RETURN)


def play_prob(status, pos, proj, practice=None) -> np.ndarray:
    """Chance of playing this week."""
    status = pd.Series(status).fillna("").astype(str).to_numpy()
    n = len(status)
    practice = np.full(n, "none") if practice is None else pd.Series(practice).fillna("none").astype(str).to_numpy()
    c = _week_params()
    X = _design(pos, proj, practice)
    z = X @ c.reindex(TERMS).to_numpy()
    z0 = X[:, :len(TERMS) - 3] @ c.reindex([f"nopractice_{t}" for t in TERMS[:-3]]).to_numpy()
    q = 1 / (1 + np.exp(-np.where(practice == "none", z0, z)))     # practice unknown: the model without it
    pos = np.asarray(pos)
    out = np.where(np.isin(status, list(NEVER)), 0.0, np.where(status == "Questionable", q, 1.0))
    return np.where(np.isin(pos, ["DEF"]), 1.0, out)


def _curve(status: str, prior: int, games: int) -> np.ndarray:
    """Share of players with this status who played the j-th game from now (2021-2025). Where fewer than
    15 such players had a j-th game left, the last well-measured share is carried on."""
    r = _return_params()
    grp = "IR" if status == "IR" else ("Out" if status in ("Reserve", "Suspended") else (status or "none"))
    g = r[r.group == grp]
    if g.empty:
        return np.zeros(games)
    have = sorted(g.prior.unique())
    g = g[g.prior == max([v for v in have if v <= prior] or [have[0]])].sort_values("game")
    good = g[g.n >= 15]
    c = good.set_index("game").play.reindex(range(games)).ffill().fillna(good.play.iloc[-1] if len(good) else 0.0)
    return c.to_numpy(float).copy()


def profile(status: str, pos: str, games: int, healthy: float, p_now: float, missed_before: int = 0,
            ir_games: int = 0) -> np.ndarray:
    """Chance of playing in each of the next `games` team games (this week first). Healthy players who
    played last week: the position's availability every game (unchanged from before). Everyone else: the
    fitted share by status and games already missed, with this week's chance from `play_prob` and the IR
    minimum on top. K and DEF are streamed and keep the flat rate."""
    if games <= 0:
        return np.zeros(0)
    if pos in ("K", "DEF") or (status == "" and missed_before == 0):
        return np.full(games, healthy)
    prior = min(ir_games - 1, 6) if status == "IR" else min(missed_before, 3)
    a = _curve(status, max(prior, 0), games)
    if status == "IR":
        a[:max(0, IR_MIN_GAMES + 1 - ir_games)] = 0.0   # placed k games ago: eligible from game 5-k
    if status:
        a[0] = p_now
    return np.clip(a, 0, 1)


# ---------------------------------------------------------------- live status
def live_status(season: int, week: int, site: pd.Series, played: pd.DataFrame) -> pd.DataFrame:
    """Status for the coming week, per player id: status, practice, source, games missed in a row before
    this week, games on IR so far (this week included), and the as-of time of the NFL inputs.
    `site`: league-site injury status by player id (rostered players). `played`: this season's team
    games so far (season, week, player_id, played)."""
    rep, res = reports(season), reserve(season)
    stamp = f"injuries {as_of(nflverse('injuries', f'injuries_{season}.csv'))}; rosters {as_of(nflverse('weekly_rosters', f'roster_weekly_{season}.csv'))}"
    r_now = rep[rep.week == week].set_index("player_id")
    last_res = res[res.week <= week]
    res_wk = int(last_res.week.max()) if len(last_res) else week
    rs = last_res[last_res.week == res_wk].set_index("player_id").reserve
    ids = pd.Index(sorted(set(site.index) | set(r_now.index) | set(rs[rs.ne("")].index)))
    site_s = site.reindex(ids).fillna("").astype(str).map(lambda v: SITE.get(v, ""))
    nfl_s = pd.Series(np.where(rs.reindex(ids).fillna("").ne(""), rs.reindex(ids).fillna(""),
                               r_now.report.reindex(ids).fillna("")), index=ids)
    on_site = ids.isin(site.index)
    sev = lambda s: s.map(SEVERITY).fillna(0)
    pick = np.where(sev(nfl_s) > sev(site_s), nfl_s, site_s)
    # the site moves the moment a player comes off IR; the NFL roster file is weekly
    pick = np.where(on_site & (nfl_s.eq("IR") | nfl_s.eq("Reserve")) & ~site_s.isin(["IR", "Reserve"]), site_s, pick)
    src = np.where(pick == site_s.to_numpy(), "league site", "NFL report / roster")
    out = pd.DataFrame(dict(status=pick, practice=r_now.practice.reindex(ids).fillna("none"), source=src), index=ids)
    out.loc[out.status.eq(""), "source"] = ""
    # streaks
    p = played[(played.season == season) & (played.week < week)].sort_values(["player_id", "week"])
    miss = p.assign(m=1 - p.played).groupby("player_id").m.apply(
        lambda v: int(np.argmax(v.to_numpy()[::-1] == 0)) if (v.to_numpy() == 0).any() else len(v))
    out["missed_before"] = miss.reindex(ids).fillna(0).astype(int).to_numpy()
    irw = res.sort_values("week")
    irw = irw[irw.week <= res_wk]
    def ir_run(v):
        a = v.to_numpy()[::-1]
        return int(np.argmax(a != "IR")) if (a != "IR").any() else len(a)
    run = irw.groupby("player_id").reserve.apply(ir_run).reindex(ids).fillna(0).astype(int)
    k = np.where(out.status.eq("IR"), np.maximum(run + (week - res_wk), out.missed_before + 1), 0)
    out["ir_games"] = k.astype(int)
    out["status_as_of"] = stamp
    return out


def _quantile(q, mid, low, high):
    """The 80% range as a quantile function: 10th percentile `low`, median `mid`, 90th `high`."""
    from scipy.stats import norm
    z = norm.ppf(np.clip(q, 1e-6, 1 - 1e-6)) / norm.ppf(0.9)
    return np.where(z >= 0, mid + z * (high - mid), mid - z * (low - mid))


def apply_week(wk: pd.DataFrame, st: pd.DataFrame) -> pd.DataFrame:
    """This week's projection = chance of playing x the projection if he plays; the 80% range is that
    mixture's (a 34% chance of not playing puts the 10th percentile at 0)."""
    wk = wk.copy()
    s = st.reindex(wk.player_id.to_numpy())
    status = s.status.fillna("").to_numpy()
    p = play_prob(status, wk.pos.to_numpy(), wk.proj.to_numpy(), s.practice.fillna("none").to_numpy())
    mid, low, high = (wk[c].to_numpy(float) for c in ("proj", "low", "high"))
    miss = 1 - p
    def mix(a):
        inside = (a - miss) / np.maximum(p, 1e-9)
        return np.where(a <= miss, 0.0, _quantile(inside, mid, low, high))
    wk["proj_if_plays"] = mid
    wk["play_prob"] = np.round(p, 3)
    wk["proj"] = p * mid
    wk["low"] = np.where(p < 1, np.clip(mix(0.1), np.minimum(low, 0), mid), low)
    wk["high"] = np.where(p < 1, mix(0.9), high)
    wk["injury_status"] = status
    wk["status_source"] = s.source.fillna("").to_numpy()
    wk["practice"] = np.where(status == "Questionable", s.practice.fillna("none").to_numpy(), "")
    wk["games_missed"] = s.missed_before.fillna(0).astype(int).to_numpy()
    wk["ir_games"] = s.ir_games.fillna(0).astype(int).to_numpy()
    return wk


def ros_availability(wk: pd.DataFrame, game_weeks: dict, done: set, healthy: pd.Series) -> tuple[pd.Series, pd.DataFrame]:
    """Share of each player's remaining team games he is expected to play, and the week-by-week chance
    for everyone whose chance is not the flat position rate (status, or games missed in a row).
    `game_weeks`: team -> remaining weeks with a game still to be played (this week first, if unplayed);
    `done`: teams whose game this week is already played (this week's chance no longer applies to them)."""
    avail, rows = [], []
    for r in wk.itertuples():
        gw = list(game_weeks.get(r.team, []))
        h = float(healthy.get(r.pos, 0.85))
        st, miss, k = getattr(r, "injury_status", ""), int(getattr(r, "games_missed", 0)), int(getattr(r, "ir_games", 0))
        if not gw:
            avail.append(h)
            continue
        if not st and miss == 0:
            avail.append(h)
            continue
        d = int(r.team in done)
        a = profile(st, r.pos, len(gw) + d, h, float(getattr(r, "play_prob", 1.0)), miss, k)[d:]
        avail.append(float(a.mean()))
        rows += [dict(player_id=r.player_id, week=w, play=round(float(v), 3), injury_status=st) for w, v in zip(gw, a)]
    return pd.Series(avail, index=wk.index), pd.DataFrame(rows, columns=["player_id", "week", "play", "injury_status"])


@lru_cache(maxsize=1)
def _avail_file() -> pd.DataFrame:
    f = PROCESSED / "proj_avail.csv"
    return pd.read_csv(f) if f.exists() else pd.DataFrame(columns=["player_id", "week", "play"])


def weekly_play(player_ids, weeks, default: pd.Series) -> pd.DataFrame:
    """Chance of playing by player and week: proj_avail.csv where the player has a profile, else `default`
    (his flat rest-of-season availability). Rows player_ids, columns weeks."""
    ids = pd.Index(player_ids)
    out = pd.DataFrame({w: default.reindex(ids).to_numpy(float) for w in weeks}, index=ids)
    pa = _avail_file()
    # only the profile made for this same coming week (a review of past weeks must not pick up today's)
    pa = pa[pa.from_week == weeks[0]] if len(weeks) and "from_week" in pa else pa.iloc[0:0]
    if len(pa):
        m = pa[pa.player_id.isin(ids) & pa.week.isin(weeks)].pivot_table(index="player_id", columns="week", values="play")
        for w in m.columns:
            v = ids.map(m[w])
            out[w] = np.where(pd.notna(v), v, out[w].to_numpy())
    return out


def check() -> pd.DataFrame:
    """In-sample: predicted vs actual share of remaining games played, by status group, weeks 3-14."""
    x = history()
    cap = {"QB": 24, "RB": 48, "WR": 60, "TE": 24}
    s = x[x.pos.isin(cap)].sort_values(["player_id", "season", "week"])
    good = s.ecr <= s.pos.map(cap)
    s = s[good.groupby([s.player_id, s.season]).transform(lambda v: v.shift().fillna(False).cummax()).astype(bool)]
    healthy = 0.85
    rows = []
    for (sid, pid), g in s.groupby(["season", "player_id"]):
        p = g.played.to_numpy()
        for i, r in enumerate(g.itertuples()):
            if not 3 <= r.week <= 14 or (r.status == "" and r.missed_before == 0):
                continue
            pn = float(play_prob([r.status], [r.pos], [r.blend if r.blend == r.blend else 5.0], [r.practice])[0])
            a = profile(r.status, r.pos, len(p) - i, healthy, pn, r.missed_before, r.ir_games)
            rows.append(dict(status=r.status or f"none, missed {min(r.missed_before, 3)}", pred=a.mean(), actual=p[i:].mean(),
                             pred_now=a[0], actual_now=p[i]))
    c = pd.DataFrame(rows).groupby("status").agg(n=("pred", "size"), pred=("pred", "mean"), actual=("actual", "mean"),
                                                 pred_now=("pred_now", "mean"), actual_now=("actual_now", "mean")).round(3)
    print(c.to_string())
    return c


if __name__ == "__main__":
    if sys.argv[1:2] == ["fit"]:
        w, r = fit()
        print(w.to_string())
        print(r[r.game.isin([0, 1, 2, 3, 4, 6, 8])].pivot_table(index=["group", "prior"], columns="game", values="play").round(2).to_string())
    elif sys.argv[1:2] == ["check"]:
        check()
