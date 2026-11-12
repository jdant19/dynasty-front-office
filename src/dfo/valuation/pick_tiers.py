"""Rookie pick value by tier of draft slot (where the cliffs are), not by round.

    PYTHONPATH=src python -m dfo.valuation.pick_tiers      # writes params_pick_tiers.csv

Value of a pick = its player's lineup points over what his rookie salary buys, over the four-year
rookie deal at 0.88 a year (val_picks_realized.csv use_lineup), drafts 2014-2022 (all four seasons
played). Slots are grouped into contiguous tiers by the step fit used for auction price tiers
(valuation/price_tiers.py: least squares, number of tiers by BIC, at least 20 picks a tier), each with
its mean, median, chance of giving no lineup value, and a bootstrap range for the mean.
A future pick's value is the average over the slots it could land on: for next year's draft, the
slots around the original team's simulated finish; for later drafts every slot in the round is equally
likely (finishes do not carry over year to year, P18).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.valuation.price_tiers import _segments

MIN_N = 20
LAST_FULL_DRAFT = 2022


def realized() -> pd.DataFrame:
    r = pd.read_csv(PROCESSED / "val_picks_realized.csv")
    return r[r.season <= LAST_FULL_DRAFT].sort_values("overall")


def fit(save: bool = True) -> pd.DataFrame:
    r = realized()
    x, y = r.overall.to_numpy(), r.use_lineup.to_numpy(float)
    n = len(y)
    best = None
    for k in range(1, 8):
        sse, cuts = _segments(x, y, k, MIN_N)
        if not np.isfinite(sse):
            break
        bic = n * np.log(sse / n) + 2 * k * np.log(n)
        if best is None or bic < best[0]:
            best = (bic, k, cuts)
    _, k, cuts = best
    rng = np.random.default_rng(0)
    rows, i = [], 0
    for t, j in enumerate(cuts, 1):
        g = r.iloc[i:j]
        boots = [g.use_lineup.sample(len(g), replace=True, random_state=int(rng.integers(1e9))).mean() for _ in range(500)]
        rows.append(dict(tier=t, first_slot=int(g.overall.min()), last_slot=int(g.overall.max()), picks=len(g),
                         mean_points=round(g.use_lineup.mean(), 1), median_points=round(g.use_lineup.median(), 1),
                         chance_no_value=round(g.no_lineup_value.mean(), 2),
                         chance_100_plus=round((g.use_lineup >= 100).mean(), 2),
                         mean_low=round(np.percentile(boots, 10), 1), mean_high=round(np.percentile(boots, 90), 1),
                         drafts=f"2014-{LAST_FULL_DRAFT}", fit_date=pd.Timestamp.now(tz="UTC").date().isoformat()))
        i = j
    out = pd.DataFrame(rows)
    if save:
        out.to_csv(PROCESSED / "params_pick_tiers.csv", index=False)
    return out


def load() -> pd.DataFrame:
    p = PROCESSED / "params_pick_tiers.csv"
    return pd.read_csv(p) if p.exists() else fit()


def slot_value(overall, tiers: pd.DataFrame | None = None) -> np.ndarray:
    t = load() if tiers is None else tiers
    ov = np.atleast_1d(np.asarray(overall, float))
    idx = np.searchsorted(t.last_slot.to_numpy(), np.clip(np.round(ov), 1, t.last_slot.max()))
    return t.mean_points.to_numpy()[idx]


def pick_value(rnd: int, slot_mean: float | None = None, slot_sd: float = 2.0) -> float:
    """Expected value of a pick in round `rnd`: slot known roughly (mean, sd within the round) or not
    at all (every slot equally likely)."""
    slots = np.arange(1, 11)
    if slot_mean is None:
        w = np.ones(10)
    else:
        w = np.exp(-0.5 * ((slots - slot_mean) / slot_sd) ** 2)
    w = w / w.sum()
    return float(np.sum(w * slot_value((rnd - 1) * 10 + slots)))


if __name__ == "__main__":
    t = fit()
    print(t.to_string(index=False))
    print({r: round(pick_value(r)) for r in (1, 2, 3)}, "late 1st:", round(pick_value(1, 9.5)), "early 1st:", round(pick_value(1, 2)))
