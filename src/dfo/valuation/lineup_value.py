"""Lineup value: what a player's projected season is worth in a starting lineup (principle P16).

    PYTHONPATH=src python -m dfo.valuation.lineup_value    # writes params_lineup_value.csv

For cap decisions in later seasons (keep, cut, bid, tag, RFA, take on salary) the value of a player is
the points he adds to a starting lineup over the waiver level, and his cost is what his salary would
normally buy (owners/grades.py PriceCurve). This module maps a preseason projection to the first.

Fit: every player ranked before the 2020-2025 auctions, his projected points then (expected.py, fit
without that season) against the lineup points over waiver he actually started for, summed over
whoever held him that season (rebuilt lineups, owners/diagnose.py). Isotonic by position: the average
team's use of a player projected for that many points, uncertainty and bench weeks included.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED

SEASONS = range(2020, 2026)
GRID = [50, 100, 150, 200, 250, 300, 350, 400]


def training_rows() -> pd.DataFrame:
    from dfo.valuation.expected import ExpectedValue, rank_for, ranks
    h = pd.read_csv(PROCESSED / "owner_season_rosters.csv")
    L = pd.read_csv(PROCESSED / "lineup_attribution.csv")
    started = L.groupby(["season", "player_id"]).over_waiver.sum()
    r = ranks("auction")
    rows = []
    for s in SEASONS:
        g = r[r.season == s].copy()
        if not len(g):
            continue
        ev = ExpectedValue("auction", exclude_season=s)
        g["proj"] = ev.predict(g.pos, rank_for(g, 0), 0).points
        g = g[g.proj.notna()]
        g["started"] = [float(started.get((s, p), 0.0)) for p in g.player_id]
        g["rostered"] = g.player_id.isin(set(h[h.season == s].player_id))
        rows.append(g[["season", "player_id", "pos", "proj", "started", "rostered"]])
    return pd.concat(rows, ignore_index=True)


class LineupValue:
    def __init__(self, rows: pd.DataFrame | None = None):
        rows = training_rows() if rows is None else rows
        rows = rows[rows.rostered]
        self.fits, out = {}, []
        for pos, g in rows.groupby("pos"):
            if len(g) < 30:
                continue
            iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(g.proj, g.started)
            # above the top 15% of projections the isotonic fit runs out of players and goes flat;
            # extend it with the slope those top players show
            top = g[g.proj >= g.proj.quantile(0.85)]
            slope = max(float(np.polyfit(top.proj, top.started, 1)[0]), 0.0) if len(top) >= 10 else 0.0
            self.fits[pos] = (iso, float(top.proj.min()) if len(top) else np.inf, slope)
            for p in GRID:
                out.append(dict(pos=pos, projected_points=p, lineup_points=round(float(self([pos], [p])[0]), 1),
                                players=len(g), top_slope=round(slope, 3)))
        self.table = pd.DataFrame(out).assign(fit_date=str(dt.date.today()), seasons="2020-2025")

    def __call__(self, pos, points) -> np.ndarray:
        pos = np.asarray(pos, dtype=object)
        points = np.asarray(points, dtype=float)
        out = np.zeros(len(pos))
        for p, (iso, x0, slope) in self.fits.items():
            m = (pos == p) & ~np.isnan(points)
            if m.any():
                x = points[m]
                ext = iso.predict(np.full(len(x), min(x0, 1e9))) + slope * (x - x0)
                out[m] = np.where(x > x0, np.maximum(iso.predict(x), ext), iso.predict(x))
        return out


def load() -> tuple[LineupValue, "object"]:
    """The lineup-value map and the price curve (what a salary normally buys), both refit from history."""
    from dfo.owners.grades import PriceCurve, _load
    _, h = _load()
    return LineupValue(), PriceCurve(h)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    lv = LineupValue()
    lv.table.to_csv(PROCESSED / "params_lineup_value.csv", index=False)
    print(lv.table.pivot(index="projected_points", columns="pos", values="lineup_points").to_string())
