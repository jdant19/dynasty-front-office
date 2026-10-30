"""Gap calibration: how far apart players really finish per point of projected gap.

    PYTHONPATH=src python -m dfo.valuation.calibrate    # writes data/processed/params_gap_shrink.csv

For startable players at a position (top N by projection), every pair's projected gap is compared
with its actual gap; the slope (through the origin) of actual on projected is the shrink factor.
1.0 means gaps are sized right; below 1 means projections spread players too far apart. Valuation
pulls each projection toward the group mean by that factor before converting to dollars:
    adjusted = mean + slope * (projection - mean)
Fit on 2021-2025 backtest rows (every projection out of sample). Re-fit each offseason.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.projections.seasons import MODEL_WEIGHT, backtest_future

TOP_WEEK = {"QB": 15, "RB": 36, "WR": 48, "TE": 15, "K": 12, "DEF": 12}
TOP_SEASON = {"QB": 20, "RB": 40, "WR": 50, "TE": 20}
TOP_ROS = {**TOP_SEASON, "K": 12, "DEF": 12}
PARAMS = PROCESSED / "params_gap_shrink.csv"


def _pairs(g: pd.DataFrame, pred: str, act: str) -> tuple[np.ndarray, np.ndarray]:
    p, a = g[pred].to_numpy(), g[act].to_numpy()
    i, j = np.triu_indices(len(g), 1)
    return p[i] - p[j], a[i] - a[j]


def _slope(chunks: list[tuple[np.ndarray, np.ndarray]]) -> tuple[float, int]:
    pg = np.concatenate([c[0] for c in chunks])
    ag = np.concatenate([c[1] for c in chunks])
    return float((pg * ag).sum() / (pg * pg).sum()), len(pg)


def weekly() -> list[dict]:
    ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv").dropna(subset=["blend"])
    out = []
    for pos, gp in ev.groupby("pos"):
        chunks = [_pairs(g.nlargest(TOP_WEEK[pos], "blend"), "blend", "points")
                  for _, g in gp.groupby(["season", "week"]) if len(g) >= 6]
        b, n = _slope(chunks)
        out.append(dict(horizon="week", pos=pos, slope=round(b, 3), pairs=n))
    return out


def rest_of_season() -> list[dict]:
    from dfo.projections.weekly import features
    ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv")
    df = features()
    chunks: dict[str, list] = {}
    for wk in (6, 8, 10):
        snap = ev[(ev.week == wk) & ev.pos.isin(TOP_ROS)].merge(
            df[["season", "week", "player_id", "points_std", "xfp_std"]], on=["season", "week", "player_id"], how="left")
        later = df[(df.week >= wk) & (df.played == 1)].groupby(["season", "player_id"]).points.agg(["mean", "size"])
        later = later[later["size"] >= 4].rename(columns={"mean": "ros_ppg"}).reset_index()
        t = snap.merge(later, on=["season", "player_id"]).dropna(subset=["points_std", "fp_only"])
        # same rate as current.py: kickers and defenses have no expected-points input
        t["ros_mix"] = 0.5 * t.fp_only + 0.25 * t.points_std + 0.25 * t.xfp_std.fillna(t.points_std)
        for (s, pos), g in t.groupby(["season", "pos"]):
            g = g.nlargest(TOP_ROS[pos], "ros_mix")
            if len(g) >= 8:
                chunks.setdefault(pos, []).append(_pairs(g, "ros_mix", "ros_ppg"))
    return [dict(horizon="ros", pos=p, slope=round(_slope(c)[0], 3), pairs=_slope(c)[1]) for p, c in chunks.items()]


def future() -> list[dict]:
    out = []
    for k in (1, 2, 3):
        ev, _ = backtest_future(k)
        ev = ev[ev.games_next >= 4]
        w = MODEL_WEIGHT[k]
        ev["used"] = np.where(ev.rank_ppg.notna(), w * ev.model_ppg + (1 - w) * ev.rank_ppg, ev.model_ppg)
        chunks = []
        for (s, pos), g in ev.groupby(["target_season", "pos"]):
            g = g.dropna(subset=["used"]).nlargest(TOP_SEASON[pos], "used")
            if len(g) >= 8:
                chunks.append(_pairs(g, "used", "ppg_next"))
        b, n = _slope(chunks)
        out.append(dict(horizon=f"+{k}", pos="all", slope=round(b, 3), pairs=n))
    return out


def fit() -> pd.DataFrame:
    p = pd.DataFrame(weekly() + rest_of_season() + future())
    p["fit_on"] = "2021-2025 backtest"
    p["fit_date"] = dt.date.today().isoformat()
    p.to_csv(PARAMS, index=False)
    return p


def load() -> pd.DataFrame:
    return pd.read_csv(PARAMS)


def shrink(values: pd.Series, pos: pd.Series, horizon: str, top: dict[str, int] | None = None) -> pd.Series:
    """Pull each position's projections toward its startable group's mean by the fitted slope."""
    p = load()
    top = top or TOP_ROS
    out = values.copy()
    for ps in pos.dropna().unique():
        row = p[(p.horizon == horizon) & (p.pos.isin([ps, "all"]))]
        if not len(row):
            continue
        b = float(row.slope.iloc[0])
        m = (pos == ps) & values.notna()
        center = values[m].nlargest(top.get(ps, 12)).mean()
        out[m] = center + b * (values[m] - center)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    print(fit().to_string(index=False))
