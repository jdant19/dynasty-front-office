"""The three stable conversions every decision is scored with (principle P18, Jordan 2026-11-08).

    PYTHONPATH=src python -m dfo.valuation.conversions      # writes params_conversions.csv

1. Salary <-> points: what $1 of salary normally buys in lineup points over waiver (PriceCurve slope,
   auction buys 2014-2025). The only "price per point".
2. Points <-> finish: places in the final regular-season standings per lineup point over a season
   (rebuilt lineups vs finishes, 2014-2025, every team the same).
3. Finish <-> payout: the payout table (config/league.yaml), a top-four finish also carrying the
   average playoff payout.
Where a team stands enters only through its finish odds this season (the season simulator, from the
actual standings). Later seasons use the league-average value of a place: finishes do not carry over
year to year in this league (rank correlation 0.09, 2013-2025), so every team gets the same value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config

N_TEAMS = 10


def payout_by_finish() -> np.ndarray:
    """Expected payout at regular-season finish 1..10 (index 0 = 1st): regular-season payout plus,
    in the top four, the average playoff payout."""
    pay = load_config("league")["payouts"]
    po = sum(pay["playoffs"].values()) / load_config("league")["playoffs"]["teams"]
    return np.array([pay["regular_season"].get(f, 0) + (po if f <= 4 else 0) for f in range(1, N_TEAMS + 1)], float)


def fit(save: bool = True) -> dict:
    from dfo.owners.grades import PriceCurve, _load
    l = pd.read_csv(PROCESSED / "owner_season_lineups.csv")
    weeks = np.where(l.season >= 2021, 18, 17)
    rel = (l.lineup_ppg - l.groupby("season").lineup_ppg.transform("mean")) * weeks
    b, a = np.polyfit(rel, l.regular_season_finish, 1)
    resid_sd = float(np.std(l.regular_season_finish - (a + b * rel), ddof=2))
    boots = []
    rng = np.random.default_rng(1)
    for _ in range(500):
        i = rng.integers(0, len(l), len(l))
        boots.append(np.polyfit(rel.iloc[i], l.regular_season_finish.iloc[i], 1)[0])
    pay = payout_by_finish()
    per_place = float(np.sum(pay[:-1] - pay[1:]) / N_TEAMS)     # a team at a random finish moving up a place
    r = pd.read_csv(PROCESSED / "season_results.csv")[["season", "owner", "regular_season_finish"]]
    nxt = r.merge(r.assign(season=r.season - 1), on=["season", "owner"], suffixes=("", "_next"))
    out = dict(points_per_cap_dollar=float(PriceCurve(_load()[1]).slope),
               places_per_point=float(-b), places_per_point_se=float(np.std(boots)), finish_resid_sd=resid_sd,
               dollars_per_place_avg=per_place, dollars_per_point_later=float(-b) * per_place,
               finish_carryover_rank_corr=float(nxt[["regular_season_finish", "regular_season_finish_next"]]
                                                .corr(method="spearman").iloc[0, 1]),
               fit_seasons="2014-2025", fit_date=pd.Timestamp.now(tz="UTC").date().isoformat())
    if save:
        pd.DataFrame([out]).round(5).to_csv(PROCESSED / "params_conversions.csv", index=False)
    return out


def load() -> dict:
    p = PROCESSED / "params_conversions.csv"
    return pd.read_csv(p).iloc[0].to_dict() if p.exists() else fit()


def finish_odds(mean_finish: float, sd: float | None = None) -> np.ndarray:
    """Chance of each finish 1..10 around an expected finish (normal, the historical spread)."""
    sd = sd if sd is not None else load()["finish_resid_sd"]
    edges = np.arange(0.5, N_TEAMS + 1.5)
    from math import erf, sqrt
    cdf = np.array([0.5 * (1 + erf((e - mean_finish) / (sd * sqrt(2)))) for e in edges])
    cdf[0], cdf[-1] = 0.0, 1.0
    return np.diff(cdf)


def expected_payout(odds: np.ndarray, shift_places: float = 0.0) -> float:
    """Expected payout from finish odds (index 0 = 1st), every outcome moved up `shift_places`
    (negative = down), payouts interpolated between places and capped at 1st and last."""
    pay = payout_by_finish()
    f = np.arange(1, N_TEAMS + 1) - shift_places
    return float(np.sum(odds * np.interp(np.clip(f, 1, N_TEAMS), np.arange(1, N_TEAMS + 1), pay)))


if __name__ == "__main__":
    for k, v in fit().items():
        print(f"{k}: {v}")
