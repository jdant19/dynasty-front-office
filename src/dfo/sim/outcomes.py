"""What a player scores in a week, as a draw from history rather than an average.

Residual pools: 2021-2025 out-of-sample weekly projections (proj_weekly_backtest.csv), actual minus
projected, by position, projection band (quantile bins) and period (weeks 1-16 vs 17-18, when NFL
teams rest starters). A player's week = his projection + a residual drawn from his band, floored at
the position's minimum. Weeks after this one use only rows where the player scored (absence is
drawn separately), so a missed game is not counted twice.

Absence: a two-state chain per player, fit from 2021-2025 (fantasy-relevant players): the chance he
is out this week given he played last week, and given he was out (injuries come in stretches).

    PYTHONPATH=src python -m dfo.sim.outcomes      # writes data/processed/params_absence.csv
"""
from __future__ import annotations

import datetime as dt
from functools import lru_cache

import numpy as np
import pandas as pd

from dfo.common import PROCESSED

N_BINS = 12
FLOOR = {"QB": -4.0, "RB": -2.0, "WR": -2.0, "TE": -2.0, "K": -2.0, "DEF": -10.0}
ABSENCE = PROCESSED / "params_absence.csv"


def fit_absence() -> pd.DataFrame:
    from dfo.projections.weekly import features
    df = features()
    d = df[df.season.between(2021, 2025) & df.pos.isin(["QB", "RB", "WR", "TE"]) & df.implied.notna()]
    d = d[["season", "week", "player_id", "pos", "played", "ecr"]].sort_values(["player_id", "season", "week"])
    rel = d[d.ecr <= 30].groupby(["season", "player_id"]).size().index
    d = d.set_index(["season", "player_id"]).loc[lambda x: x.index.isin(rel)].reset_index()
    d["prev"] = d.groupby(["season", "player_id"]).played.shift(1)
    t = d.dropna(subset=["prev"])
    rows = []
    for pos, g in t.groupby("pos"):
        rows.append(dict(pos=pos, out_if_played=round(1 - g[g.prev == 1].played.mean(), 4),
                         out_if_out=round(1 - g[g.prev == 0].played.mean(), 4), player_weeks=len(g)))
    rows += [dict(pos=p, out_if_played=0.0, out_if_out=0.0, player_weeks=0) for p in ("K", "DEF")]  # streamed
    out = pd.DataFrame(rows)
    out["fit_on"] = "2021-2025"
    out["fit_date"] = dt.date.today().isoformat()
    out.to_csv(ABSENCE, index=False)
    return out


@lru_cache(maxsize=1)
def absence() -> dict[str, tuple[float, float]]:
    p = pd.read_csv(ABSENCE)
    return {r.pos: (float(r.out_if_played), float(r.out_if_out)) for r in p.itertuples()}


class Pools:
    """Residual pools keyed by (pos, late_season, played_only)."""

    def __init__(self):
        ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv", usecols=["week", "pos", "points", "blend"])
        ev = ev.dropna(subset=["blend"])
        ev["late"] = ev.week >= 17
        ev["res"] = ev.points - ev.blend
        self.edges, self.res = {}, {}
        for (pos, late), g in ev.groupby(["pos", "late"]):
            edges = np.unique(np.quantile(g.blend, np.linspace(0, 1, N_BINS + 1)))
            self.edges[(pos, late)] = edges
            b = np.clip(np.searchsorted(edges, g.blend, side="right") - 1, 0, len(edges) - 2)
            for played_only in (False, True):
                keep = (g.points != 0) | g.pos.isin(["K", "DEF"]) if played_only else pd.Series(True, index=g.index)
                for i in range(len(edges) - 1):
                    r = g.res[(b == i) & keep].to_numpy()
                    if played_only and len(r):
                        r = r - r.mean()     # later weeks: the calibrated rate is the mean; only the spread is drawn
                    self.res[(pos, late, played_only, i)] = r if len(r) else np.array([0.0])

    def draw(self, pos: str, proj: np.ndarray, late: bool, played_only: bool, rng: np.random.Generator) -> np.ndarray:
        """One draw per entry of `proj` (any shape)."""
        edges = self.edges.get((pos, late)) if (pos, late) in self.edges else self.edges[(pos, False)]
        lk = late if (pos, late) in self.edges else False
        b = np.clip(np.searchsorted(edges, proj, side="right") - 1, 0, len(edges) - 2)
        out = np.empty(proj.shape)
        for i in np.unique(b):
            m = b == i
            pool = self.res[(pos, lk, played_only, int(i))]
            out[m] = proj[m] + pool[rng.integers(0, len(pool), m.sum())]
        return np.maximum(out, FLOOR.get(pos, -2.0))


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    print(fit_absence().to_string(index=False))
