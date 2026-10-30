"""Restricted free agency: what RFAs cost in this league, and the engine's backtest against it.

    PYTHONPATH=src python -m dfo.valuation.rfa       # writes data/processed/val_rfa_*.csv

RFA bidding runs in June-July (Jordan), so values use the rankings as of July 1. For each RFA,
the final price (the winning bid, matched or not) is compared with the engine's value of his coming
season at auction prices (market replacement level and price per point from auction.fit_market,
model fit without that season) and with what that season actually returned.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from dfo.common import PROCESSED
from dfo.valuation.expected import LAST_COMPLETE, ExpectedValue, rank_for, ranks, season_totals

SKILL = ["QB", "RB", "WR", "TE"]


def rfa_players() -> pd.DataFrame:
    r = pd.read_csv(PROCESSED / "rfa_bids.csv")
    fin = r.groupby(["season", "player"]).agg(final=("final_price", "first"), start=("starting_bid", "first"),
                                              outcome=("outcome", "first"), bids=("bid", "count"),
                                              original_owner=("original_owner", "first")).reset_index()
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    nm = nm[nm.table == "rfa_bids"][["season", "name", "canonical_id"]]
    fin = fin.merge(nm, left_on=["season", "player"], right_on=["season", "name"], how="left")
    fin = fin.rename(columns={"canonical_id": "player_id"}).drop(columns="name")
    pos = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos
    fin["pos"] = fin.player_id.map(pos).replace({"DST": "DEF"})
    fin.loc[fin.player_id.astype(str).str.startswith("DEF-"), "pos"] = "DEF"
    rk = ranks("rfa")[["season", "player_id", "redraft_rank", "dynasty_rank"]]
    return fin.merge(rk, on=["season", "player_id"], how="left")


def backtest() -> pd.DataFrame:
    from dfo.valuation.auction import market_params
    shift, lam, _ = market_params()
    st = season_totals()
    d = rfa_players()
    rows = []
    for s, g in d[d.redraft_rank.notna()].groupby("season"):
        ev = ExpectedValue("rfa", exclude_season=s if s <= LAST_COMPLETE else None, shift=shift)
        g = g.copy()
        g["exp_por0"] = ev.predict(g.pos, rank_for(g, 0), 0).por
        g["engine0"] = lam * g.exp_por0
        if s <= LAST_COMPLETE:
            act = st[st.season == s].set_index("player_id").points
            g["realized0"] = lam * (g.player_id.map(act).fillna(0) - g.pos.map(ev.replacement[0])).clip(lower=0)
        rows.append(g)
    out = pd.concat(rows, ignore_index=True)
    out.to_csv(PROCESSED / "val_rfa_backtest.csv", index=False)
    return out


def summarize(bt: pd.DataFrame) -> dict[str, pd.DataFrame]:
    from dfo.valuation.auction import market_params
    lam_auction = market_params()[1]
    lam_rfa = (bt.final - 1).sum() / bt.exp_por0.sum()
    by_pos = bt.groupby("pos").agg(players=("final", "size"), avg_price=("final", "mean"),
                                   avg_engine=("engine0", "mean"), avg_realized=("realized0", "mean")).round(1)
    by_pos["price_per_point"] = bt.groupby("pos").apply(lambda g: (g.final - 1).sum() / max(g.exp_por0.sum(), 1e-9)).round(3)
    by_season = bt.groupby("season").apply(lambda g: pd.Series(dict(
        players=len(g), price_per_point=round((g.final - 1).sum() / g.exp_por0.sum(), 3)))).reset_index()
    sk = bt[bt.pos.isin(SKILL) & bt.realized0.notna()]
    acc = pd.DataFrame([
        dict(predictor="RFA price", players=len(sk), mae=round(float((sk.final - 1 - sk.realized0).abs().mean()), 1),
             rank_corr=round(float(spearmanr(sk.final, sk.realized0, nan_policy="omit").statistic), 3)),
        dict(predictor="engine", players=len(sk), mae=round(float((sk.engine0 - sk.realized0).abs().mean()), 1),
             rank_corr=round(float(spearmanr(sk.engine0, sk.realized0).statistic), 3))])
    by_outcome = bt.groupby("outcome").agg(players=("final", "size"), avg_price=("final", "mean"),
                                           avg_engine=("engine0", "mean"), avg_realized=("realized0", "mean")).round(1)
    res = {"accuracy": acc, "position": by_pos.reset_index(), "season": by_season,
           "outcome": by_outcome.reset_index(),
           "overall": pd.DataFrame([dict(rfa_price_per_point=round(lam_rfa, 3),
                                         auction_price_per_point=round(lam_auction, 3),
                                         rfa_premium=round(lam_rfa / lam_auction - 1, 3))])}
    for k, v in res.items():
        v.to_csv(PROCESSED / f"val_rfa_{k}.csv", index=False)
    return res


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    for k, v in summarize(backtest()).items():
        print(f"\n== {k}\n{v.to_string(index=False)}")
