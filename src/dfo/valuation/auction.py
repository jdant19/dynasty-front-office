"""The league's auction as a price signal, and the engine's backtest against it.

    PYTHONPATH=src python -m dfo.valuation.auction     # writes data/processed/val_auction_*.csv

The market fit (fit_market): price - $1 = lambda x E[max(0, points - replacement)], coming season,
with the replacement level per position = the structural level (first non-starter) plus a shift,
and lambda and the shifts chosen to fit the prices (least absolute error). The structural level
alone left a strong star premium (mid-tier players cost $0.31 per point, stars $0.70): owners treat
mid-tier players, and every QB outside the top few, as freely available, which with 18-man rosters
and $1 QB11-15s at past auctions they are.

Prices from every auction 2020-2026 set the market fit; outcome backtests use 2020-2025 (complete
seasons), each with models fit with that season left out:
  engine0   lambda x expected value over the market replacement level, coming season
  future    value of contract years 2-4 at the best contract length (salary schedule from
            config/league.yaml, haircut 0.88 a year)
  realized  the same quantities from what players actually scored, against the ex-ante
            replacement level (the replacement you could plan on), never below 0 a season
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd
from scipy.stats import norm, spearmanr

from dfo.common import PROCESSED, load_config
from dfo.rules.contracts import salary_schedule
from dfo.valuation.expected import (HORIZONS, LAST_COMPLETE, STREAMED, ExpectedValue, outcomes, rank_for, ranks,
                                    season_totals)

SKILL = ["QB", "RB", "WR", "TE"]


def haircut() -> float:
    return float(load_config("settings")["objective"]["confidence_haircut_per_year"])


def auction_buys(first: int = 2020) -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "auctions.csv")
    a = a[(a.kind == "auction") & (a.season >= first)]
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    nm = nm[nm.table == "auctions"][["season", "name", "canonical_id"]]
    a = a.merge(nm, left_on=["season", "player"], right_on=["season", "name"], how="left")
    a = a.rename(columns={"canonical_id": "player_id"}).drop(columns="name")
    r = ranks("auction")[["season", "player_id", "redraft_rank", "dynasty_rank"]]
    return a.merge(r, on=["season", "player_id"], how="left")


def contract_value(price: pd.Series, values: dict[int, np.ndarray], h: float) -> tuple[np.ndarray, np.ndarray]:
    """Best contract length (1-4) and the haircut surplus of years 2-4 at that length.
    values[k] = dollar value of season k (k = 0 is the coming season)."""
    sched = np.array([salary_schedule(int(p), 4) for p in price])
    cum = np.zeros(len(price))
    best = np.zeros(len(price))
    length = np.ones(len(price), dtype=int)
    for k in range(1, 4):
        if k not in values:
            break
        cum = cum + h ** k * (values[k] - sched[:, k])
        better = cum > best
        length = np.where(better, k + 1, length)
        best = np.maximum(best, cum)
    return length, best


MARKET_PARAMS = PROCESSED / "params_market.csv"


def _fit_rows(buys: pd.DataFrame, models: dict[int, ExpectedValue]) -> pd.DataFrame:
    """Each skill-position buy with its coming-season expected points, spread and structural
    replacement level, from the model that left its season out."""
    rows = []
    for s, ev in models.items():
        x = buys[(buys.season == s) & buys.pos.isin(SKILL)].copy()
        p = ev.predict(x.pos, rank_for(x, 0), 0)
        x["pts"] = p.points
        x = x[x.pts.notna()]
        x["R"] = x.pos.map(ev.replacement[0])
        rows.append(x)
    return pd.concat(rows, ignore_index=True)


class _Pool:
    """Historical (expected points, actual points) pairs per position, coming season, to price a
    replacement level empirically: E[max(0, actual - R)] given expected points."""

    def __init__(self):
        from sklearn.isotonic import IsotonicRegression
        self._iso = IsotonicRegression
        ev = ExpectedValue("auction")
        o = outcomes("auction", 0)
        self.R = ev.replacement[0]
        self.pool = {}
        for pos, g in o[o.pos.isin(SKILL)].groupby("pos"):
            self.pool[pos] = (ev.points[(pos, 0)].predict(np.log(g["rank"])), g.points.to_numpy())
        self.cache: dict = {}

    def por(self, pos: str, x: np.ndarray, shift: float) -> np.ndarray:
        key = (pos, shift)
        if key not in self.cache:
            xp, yp = self.pool[pos]
            self.cache[key] = (self._iso(increasing=True, out_of_bounds="clip")
                               .fit(xp, np.clip(yp - (self.R[pos] + shift), 0, None)), float(xp.max()))
        iso, top = self.cache[key]
        return iso.predict(np.minimum(x, top)) + np.maximum(x - top, 0)


def _por(fr: pd.DataFrame, shift: dict[str, float], pool: _Pool) -> pd.Series:
    out = pd.Series(0.0, index=fr.index)
    for pos, g in fr.groupby("pos"):
        out[g.index] = pool.por(pos, g.pts.to_numpy(), shift.get(pos, 0.0))
    return out


def fit_market(fr: pd.DataFrame, pool: _Pool | None = None) -> tuple[dict[str, float], float, float]:
    """Replacement shifts per position and the price per point that best fit auction prices."""
    pool = pool or _Pool()
    y = fr.salary - 1

    def score(shift):
        v = _por(fr, shift, pool)
        lam = float((v * y).sum() / (v * v).sum())
        return float((y - lam * v).abs().mean()), lam

    shift = {p: 0.0 for p in SKILL}
    for _ in range(3):                     # coordinate descent, 5-point steps
        for p in SKILL:
            shift[p] = float(min(range(-40, 201, 5), key=lambda d: score({**shift, p: float(d)})[0]))
    mae, lam = score(shift)
    return shift, lam, mae


def backtest() -> pd.DataFrame:
    buys = auction_buys()
    h = haircut()
    st = season_totals()
    priced = sorted(buys.loc[buys.redraft_rank.notna(), "season"].unique())      # prices: every auction
    seasons = [s for s in priced if s <= LAST_COMPLETE]                          # outcomes: complete seasons
    structural = {s: ExpectedValue("auction", exclude_season=s) for s in priced}
    fr = _fit_rows(buys, structural)
    pool = _Pool()
    shift_all, lam_all, mae_all = fit_market(fr, pool)
    v0 = _por(fr, {p: 0.0 for p in SKILL}, pool)
    lam_struct = float(((fr.salary - 1) * v0).sum() / (v0 * v0).sum())     # same prices, structural level
    pd.DataFrame([dict(pos=p, replacement_shift=shift_all[p], structural_replacement=round(
        ExpectedValue("auction").replacement[0][p], 1)) for p in SKILL]).assign(
        price_per_point=round(lam_all, 4), price_per_point_structural=round(lam_struct, 4),
        fit_mae=round(mae_all, 2), auctions=f"{min(priced)}-{max(priced)}",
        buys=len(fr), fit_date=dt.date.today().isoformat()).to_csv(MARKET_PARAMS, index=False)
    rows = []
    for s in seasons:
        shift, lam, _ = fit_market(fr[fr.season != s], pool)
        ev = ExpectedValue("auction", exclude_season=s, shift=shift)
        x = buys[buys.season == s].copy()
        x["lambda_used"] = lam
        pred, real = {}, {}
        for k in HORIZONS:
            if k not in ev.replacement:
                continue
            p = ev.predict(x.pos, rank_for(x, k), k)
            x[f"exp_por{k}"] = p.por
            pred[k] = lam * p.por.to_numpy()
            if s + k <= LAST_COMPLETE:
                act = st[st.season == s + k].set_index("player_id").points
                over = x.player_id.map(act).fillna(0) - x.pos.map(ev.replacement[k])
                # a streamed position held all year is measured against streaming, with no floor
                x[f"act_por{k}"] = over.where(x.pos.isin(STREAMED), over.clip(lower=0))
                real[k] = lam * x[f"act_por{k}"].to_numpy()
        x["engine0"] = pred[0]
        x["realized0"] = real[0]
        x["length"], x["engine_future"] = contract_value(x.salary, pred, h)
        sched = np.array([salary_schedule(int(p), 4) for p in x.salary])
        x["years_observed"] = len(real) - 1
        x["realized_future"] = sum(np.where(x.length.to_numpy() > k, h ** k * (real[k] - sched[:, k]), 0.0)
                                   for k in real if k > 0) if len(real) > 1 else np.nan
        rows.append(x)
    out = pd.concat(rows, ignore_index=True)
    out.to_csv(PROCESSED / "val_auction_backtest.csv", index=False)
    return out


def market_params() -> tuple[dict[str, float], float, float]:
    """(replacement shifts, price per point over the market level, price per point over the
    structural level) from the last fit."""
    p = pd.read_csv(MARKET_PARAMS)
    return (dict(zip(p.pos, p.replacement_shift)), float(p.price_per_point.iloc[0]),
            float(p.price_per_point_structural.iloc[0]))


def _boot_ci(v: np.ndarray, n: int = 2000, seed: int = 7) -> tuple[float, float]:
    rng = np.random.default_rng(seed)
    means = [rng.choice(v, len(v)).mean() for _ in range(n)]
    return float(np.percentile(means, 5)), float(np.percentile(means, 95))


def summarize(bt: pd.DataFrame) -> dict[str, pd.DataFrame]:
    sk = bt[bt.pos.isin(SKILL)].copy()
    sk["market"] = sk.salary - 1
    year0 = []
    for name, col in [("league price", "market"), ("engine", "engine0")]:
        year0.append(dict(predictor=name, buys=len(sk),
                          mae=round(float((sk[col] - sk.realized0).abs().mean()), 1),
                          rank_corr=round(float(spearmanr(sk[col], sk.realized0).statistic), 3)))
    blend = 0.5 * sk.market + 0.5 * sk.engine0
    year0.append(dict(predictor="50/50", buys=len(sk), mae=round(float((blend - sk.realized0).abs().mean()), 1),
                      rank_corr=round(float(spearmanr(blend, sk.realized0).statistic), 3)))
    pos = []
    for p, g in bt.groupby("pos"):
        surplus = (g.realized0 - (g.salary - 1)).to_numpy()
        lo, hi = _boot_ci(surplus)
        pos.append(dict(pos=p, buys=len(g), avg_price=round(g.salary.mean(), 1), avg_engine=round(g.engine0.mean(), 1),
                        avg_realized=round(g.realized0.mean(), 1), surplus=round(surplus.mean(), 1),
                        surplus_90ci=f"{lo:.1f} to {hi:.1f}"))
    fut = sk[sk.years_observed >= 3]
    X = np.c_[sk.engine0, sk.engine_future]
    coef = np.linalg.lstsq(X, sk.market, rcond=None)[0]
    fq = fut.assign(q=pd.qcut(fut.engine_future.rank(method="first"), 4, labels=["lowest", "2nd", "3rd", "highest"]))
    future = fq.groupby("q", observed=True).agg(buys=("salary", "size"), avg_price=("salary", "mean"),
                                                 engine_future=("engine_future", "mean"),
                                                 realized_future=("realized_future", "mean")).round(1).reset_index()
    meta = pd.DataFrame([dict(
        buys_with_3_later_seasons=len(fut),
        future_rank_corr=round(float(spearmanr(fut.engine_future, fut.realized_future).statistic), 3),
        future_mean_engine=round(fut.engine_future.mean(), 1), future_mean_realized=round(fut.realized_future.mean(), 1),
        price_per_engine0_dollar=round(coef[0], 2), price_per_future_dollar=round(coef[1], 2))])
    lam = bt.groupby("season").agg(lambda_used=("lambda_used", "first"), spend=("salary", "sum"),
                                   buys=("salary", "size")).round(3).reset_index()
    res = {"year0": pd.DataFrame(year0), "position": pd.DataFrame(pos), "future": future, "future_meta": meta,
           "lambda": lam}
    for k, v in res.items():
        v.to_csv(PROCESSED / f"val_auction_{k}.csv", index=False)
    return res


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    for k, v in summarize(backtest()).items():
        print(f"\n== {k}\n{v.to_string(index=False)}")
