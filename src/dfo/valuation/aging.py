"""Aging curves: average change in league points per game from one season to the next, by age.

    PYTHONPATH=src python -m dfo.valuation.aging      # writes data/processed/params_aging.csv

Delta method: players with 6+ games in both seasons, each pair weighted by the harmonic mean of
games, a quadratic in age fit per position, 2013-2025 nflverse seasons. Seasons lost to injury or
leaving the league are not in the curve; the games model (projections/seasons.py) carries them.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from dfo.common import PROCESSED

PARAMS = PROCESSED / "params_aging.csv"
SKILL = ["QB", "RB", "WR", "TE"]


def fit() -> pd.DataFrame:
    from dfo.projections.seasons import season_table
    st = season_table()
    nxt = st[["player_id", "season", "ppg", "games"]].rename(columns={"ppg": "ppg_n", "games": "games_n"})
    nxt["season"] -= 1
    t = st.merge(nxt, on=["player_id", "season"])
    t = t[(t.games >= 6) & (t.games_n >= 6) & t.pos.isin(SKILL) & t.age.notna()]
    w = np.sqrt(2 / (1 / t.games + 1 / t.games_n))
    rows = []
    for pos, g in t.groupby("pos"):
        ww = w[g.index].to_numpy()
        A = np.c_[np.ones(len(g)), g.age, g.age ** 2] * ww[:, None]
        c = np.linalg.lstsq(A, (g.ppg_n - g.ppg).to_numpy() * ww, rcond=None)[0]
        rows.append(dict(pos=pos, c0=c[0], c1=c[1], c2=c[2], pairs=len(g),
                         seasons=f"{int(t.season.min())}-{int(t.season.max()) + 1}",
                         fit_date=dt.date.today().isoformat()))
    p = pd.DataFrame(rows)
    p.to_csv(PARAMS, index=False)
    return p


def delta(pos: pd.Series, age: pd.Series) -> pd.Series:
    """Expected change in points per game from the season a player plays at `age` to the next."""
    p = pd.read_csv(PARAMS).set_index("pos")
    a = age.clip(21, 34)
    out = pd.Series(0.0, index=age.index)
    for ps in p.index:
        m = pos == ps
        c = p.loc[ps]
        out[m] = c.c0 + c.c1 * a[m] + c.c2 * a[m] ** 2
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    f = fit()
    ages = pd.Series(np.arange(22, 34, dtype=float))
    for pos in SKILL:
        print(pos, delta(pd.Series([pos] * len(ages)), ages).round(2).tolist())
