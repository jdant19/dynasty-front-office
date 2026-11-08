"""Kicker and defense matchups more than a week ahead.

    PYTHONPATH=src python -m dfo.decide.streaming      # back-test + fit: params_stream_matchup.csv

Question: when choosing which K or DEF to hold, does the schedule two or three weeks out tell us
anything beyond the unit's own scoring rate? Betting lines are posted about a week ahead, so the
coming week uses the weekly projection; for the weeks after it the signals known now are
  - opp_vs:  points the position has scored against that opponent so far this season
  - opp_off: the opponent's average implied team total so far (how good the betting market thinks
             its offense is; for a kicker, his own team's average implied total is the rate's context)
Back-test 2021-2025, leave one season out: at each week 4-14, from the units ranked 11-20 by rate (the
free-agent pool in a 10-team league), pick the best by rate alone vs by rate + matchup, k weeks ahead.
The fitted model is used as an adjustment: prediction with this opponent minus prediction against an
average opponent, added to the unit's rest-of-season rate for that week.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.linear_model import LinearRegression

from dfo.common import PROCESSED
from dfo.projections.features import implied_totals

COLS = ["rate", "opp_vs", "opp_off"]
AHEAD = (2, 3)            # weeks after the decision week the adjustment is used for (tested range)


def team_weeks(seasons: list[int]) -> pd.DataFrame:
    """One row per season, week, position (K/DEF) and team: points scored, opponent, implied totals."""
    rows = []
    for s in seasons:
        p = pd.read_csv(PROCESSED / f"points_{s}.csv")
        p = p[(p.season_type == "REG") & p.pos.isin(["K", "DEF"])]
        rows.append(p.groupby(["season", "week", "pos", "team"], as_index=False).points.sum())
    pts = pd.concat(rows)
    imp = implied_totals(seasons)[["season", "week", "team", "opp", "implied"]]
    return imp.merge(pts, on=["season", "week", "team"], how="left")    # future weeks: points NaN


def signals(tw: pd.DataFrame, season: int, through: int, pos: str) -> tuple[pd.Series, pd.Series, pd.Series]:
    """(own rate by team, points the position scored vs each opponent, opponent's mean implied total),
    all from games through week `through`."""
    x = tw[(tw.season == season) & (tw.pos == pos) & (tw.week <= through) & tw.points.notna()]
    i = tw[(tw.season == season) & (tw.week <= through)].drop_duplicates(["week", "team"])
    return x.groupby("team").points.mean(), x.groupby("opp").points.mean(), i.groupby("team").implied.mean()


def _rows(tw: pd.DataFrame, seasons, ks=(1, 2, 3)) -> pd.DataFrame:
    out = []
    for s in seasons:
        for pos in ("DEF", "K"):
            for t in range(4, 15):
                rate, vs, off = signals(tw, s, t, pos)
                for k in ks:
                    g = tw[(tw.season == s) & (tw.week == t + k) & (tw.pos == pos) & tw.points.notna()]
                    if not len(g):
                        continue
                    out.append(pd.DataFrame(dict(season=s, week=t, k=k, pos=pos, team=g.team.values, points=g.points.values,
                                                 rate=g.team.map(rate).values, opp_vs=g.opp.map(vs).values,
                                                 opp_off=(g.opp if pos == "DEF" else g.team).map(off).values)))
    return pd.concat(out).dropna(subset=COLS)


def backtest(seasons=range(2016, 2026), test=range(2021, 2026)) -> tuple[pd.DataFrame, pd.DataFrame]:
    tw = team_weeks(list(seasons))
    a = _rows(tw, seasons)
    res = []
    for (pos, k), z in a.groupby(["pos", "k"]):
        for y in test:
            tr, te = z[z.season != y], z[z.season == y].copy()
            te["by_rate"] = LinearRegression().fit(tr[["rate"]], tr.points).predict(te[["rate"]])
            te["by_matchup"] = LinearRegression().fit(tr[COLS], tr.points).predict(te[COLS])
            for (_, t), g in te.groupby(["season", "week"]):
                pool = g.sort_values("rate", ascending=False).iloc[10:20]
                if len(pool):
                    res.append(dict(pos=pos, k=k, season=y, week=t, pool_mean=pool.points.mean(),
                                    by_rate=pool.loc[pool.by_rate.idxmax(), "points"],
                                    by_matchup=pool.loc[pool.by_matchup.idxmax(), "points"]))
    r = pd.DataFrame(res)
    r["gain"] = r.by_matchup - r.by_rate
    summ = r.groupby(["pos", "k"]).agg(pool_mean=("pool_mean", "mean"), by_rate=("by_rate", "mean"),
                                       by_matchup=("by_matchup", "mean"), gain=("gain", "mean"),
                                       gain_se=("gain", lambda v: v.std() / np.sqrt(len(v))),
                                       seasons_positive=("gain", lambda v: 0), n=("gain", "size"))
    by_season = r.groupby(["pos", "k", "season"]).gain.mean().unstack()
    summ["seasons_positive"] = (by_season > 0).sum(axis=1)
    return summ.round(2).reset_index(), a


def fit(save: bool = True) -> pd.DataFrame:
    """Back-test, then the model on all seasons for each (pos, k) where matchup beat rate alone by
    more than two standard errors; elsewhere no adjustment."""
    summ, a = backtest()
    rows = []
    for (pos, k), z in a.groupby(["pos", "k"]):
        s = summ[(summ.pos == pos) & (summ.k == k)].iloc[0]
        use = bool(k in AHEAD and s.gain > 2 * s.gain_se)
        m = LinearRegression().fit(z[COLS], z.points)
        rows.append(dict(pos=pos, k=k, use=use, intercept=m.intercept_, **dict(zip(COLS, m.coef_)),
                         avg_opp_vs=z.opp_vs.mean(), avg_opp_off=z.opp_off.mean(),
                         backtest_gain=s.gain, gain_se=s.gain_se, seasons_positive=int(s.seasons_positive),
                         fit_seasons="2016-2025", fit_date=pd.Timestamp.now(tz="UTC").date().isoformat()))
    out = pd.DataFrame(rows)
    if save:
        out.round(4).to_csv(PROCESSED / "params_stream_matchup.csv", index=False)
        summ.to_csv(PROCESSED / "val_stream_backtest.csv", index=False)
    return out


def adjustments(season: int, week0: int) -> pd.DataFrame:
    """Points to add to each K/DEF unit's rate in the weeks after the coming one (week0 + k - 1 for
    the tested k), against an average opponent. Columns: pos, team, week, adj."""
    path = PROCESSED / "params_stream_matchup.csv"
    if not path.exists():
        return pd.DataFrame(columns=["pos", "team", "week", "adj"])
    prm = pd.read_csv(path)
    prm = prm[prm.use]
    tw = team_weeks([season])
    t = week0 - 1                                  # data through the last completed week
    out = []
    for r in prm.itertuples():
        rate, vs, off = signals(tw, season, t, r.pos)
        g = tw[(tw.season == season) & (tw.week == t + r.k)].drop_duplicates("team")
        ov = g.opp.map(vs).fillna(r.avg_opp_vs)
        oo = (g.opp if r.pos == "DEF" else g.team).map(off).fillna(r.avg_opp_off)
        adj = r.opp_vs * (ov - r.avg_opp_vs) + r.opp_off * (oo - r.avg_opp_off)
        out.append(pd.DataFrame(dict(pos=r.pos, team=g.team.values, week=t + r.k, adj=adj.values)))
    return pd.concat(out, ignore_index=True) if out else pd.DataFrame(columns=["pos", "team", "week", "adj"])


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 200)
    p = fit()
    print(pd.read_csv(PROCESSED / "val_stream_backtest.csv").to_string(index=False))
    print(p[["pos", "k", "use", "rate", "opp_vs", "opp_off", "backtest_gain", "gain_se"]].to_string(index=False))
