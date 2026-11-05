"""Price tiers: where an extra auction dollar buys more lineup points, and where it buys nothing.

    PYTHONPATH=src python -m dfo.valuation.price_tiers     # writes params_price_tiers.csv, val_cheap_supply.csv

Data: every auction buy 2014-2025 and the lineup points over waiver he started for in the season bought
(owners/grades.py). For each position a step function of price is fit by dynamic programming: tiers of
at least MIN_N buys, the number of tiers chosen by BIC. Within a tier, paying more buys nothing on
average; between tiers the value steps up. Each tier carries a bootstrap 80% range of its mean and the
two halves of the history (2014-2019, 2020-2025) so a tier that only one era shows can be seen.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from dfo.common import PROCESSED

MIN_N = 15
POSITIONS = ["QB", "RB", "WR", "TE"]


def buys() -> pd.DataFrame:
    from dfo.owners.grades import _load
    _, h = _load()
    b = h[(h.channel == "auction") & (h.w0 == 1) & h.salary.notna()].copy()
    return b[b.event.str.slice(1, 5).astype(int) == b.season][["season", "owner", "player_id", "pos", "salary",
                                                                 "started_pts"]]


def _segments(x: np.ndarray, y: np.ndarray, k: int, min_n: int):
    """Best split of price-sorted y into k contiguous segments (no split inside a price), min_n each."""
    n = len(y)
    cs, cs2 = np.r_[0, np.cumsum(y)], np.r_[0, np.cumsum(y ** 2)]
    sse = lambda i, j: cs2[j] - cs2[i] - (cs[j] - cs[i]) ** 2 / (j - i)
    ok = np.r_[True, x[1:] != x[:-1], True]           # allowed cut points: between different prices
    INF = float("inf")
    D = np.full((k + 1, n + 1), INF)
    P = np.zeros((k + 1, n + 1), dtype=int)
    D[0, 0] = 0
    for s in range(1, k + 1):
        for j in range(min_n * s, n + 1):
            if not ok[j] and j != n:
                continue
            best, arg = INF, -1
            for i in range(min_n * (s - 1), j - min_n + 1):
                if D[s - 1, i] < INF and (ok[i] or i == 0):
                    v = D[s - 1, i] + sse(i, j)
                    if v < best:
                        best, arg = v, i
            D[s, j], P[s, j] = best, arg
    cuts, j = [], n
    for s in range(k, 0, -1):
        cuts.append(j)
        j = P[s, j]
    return D[k, n], sorted(cuts)


def fit(b: pd.DataFrame, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for pos in POSITIONS:
        g = b[b.pos == pos].sort_values("salary")
        x, y = g.salary.to_numpy(float), g.started_pts.to_numpy(float)
        best = None
        for k in range(1, 6):
            if len(y) < k * MIN_N:
                break
            sse, cuts = _segments(x, y, k, MIN_N)
            bic = len(y) * np.log(sse / len(y)) + 2 * k * np.log(len(y))
            if best is None or bic < best[0]:
                best = (bic, cuts, k)
        start = 0
        for t, end in enumerate(best[1]):
            seg = g.iloc[start:end]
            v = seg.started_pts.to_numpy(float)
            boot = [rng.choice(v, len(v)).mean() for _ in range(1000)]
            early, late = seg[seg.season <= 2019], seg[seg.season >= 2020]
            rows.append(dict(pos=pos, tier=t + 1, price_from=int(seg.salary.min()), price_to=int(seg.salary.max()),
                             buys=len(seg), mean_points=round(v.mean(), 1), median_points=round(float(np.median(v)), 1),
                             lo80=round(float(np.percentile(boot, 10)), 1), hi80=round(float(np.percentile(boot, 90)), 1),
                             share_50_plus=round(float((v >= 50).mean()), 2),
                             mean_2014_19=round(early.started_pts.mean(), 1) if len(early) >= 5 else np.nan,
                             mean_2020_25=round(late.started_pts.mean(), 1) if len(late) >= 5 else np.nan))
            start = end
    out = pd.DataFrame(rows)
    out["fit_date"] = str(dt.date.today())
    out.to_csv(PROCESSED / "params_price_tiers.csv", index=False)
    return out


def cheap_supply(b: pd.DataFrame, max_price: int = 3, threshold: float = 50) -> pd.DataFrame:
    """Each auction: how many buys at max_price or less started for threshold+ points over waiver."""
    c = b[b.salary <= max_price]
    out = c.groupby(["season", "pos"]).agg(cheap_buys=("salary", "size"),
                                            hits=("started_pts", lambda s: int((s >= threshold).sum()))).reset_index()
    out.to_csv(PROCESSED / "val_cheap_supply.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    b = buys()
    print(fit(b).to_string(index=False))
    s = cheap_supply(b)
    print(s.groupby("pos")[["cheap_buys", "hits"]].mean().round(1))
