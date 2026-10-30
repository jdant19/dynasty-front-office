"""Rookie picks: what each slot is worth to use, what the market pays, and the use-or-sell test.

    PYTHONPATH=src python -m dfo.valuation.picks     # writes data/processed/val_picks_*.csv

Use value of a slot: what this league's picks at that slot actually produced over the four-year
rookie deal (2014-2022 drafts, every season observed): each season, auction-price value over the
market replacement level minus the rookie salary ($5/$3/$1 by round, +$1 a year), kept only while
that is positive (a bust is cut for a few dollars of dead cap), 0.88 a year. Smoothed across slots
(isotonic), with odds of a bust (no season above replacement), a starter (a season inside the
league's starters at his position) and a star (a top-5 season at his position).

Market value: FantasyCalc's early/mid/late pick values (10-team, 1QB, half PPR), put in dollars
with the same FantasyCalc-to-dollars map used for players (prices.py).

League history: every past trade that moved picks, scored on what each side's assets returned over
the next three seasons (picks at their round's average for that draft; players at auction-price
value over replacement, counted only in seasons someone kept him, minus that salary).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED, load_config
from dfo.valuation.auction import haircut, market_params
from dfo.valuation.expected import LAST_COMPLETE, ExpectedValue, season_totals, starters

ROUND_SALARY = load_config("league")["contracts"]["rookie"]["salary_by_round"]


def rookie_picks() -> pd.DataFrame:
    r = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    nm = nm[nm.table == "rookie_drafts"][["season", "name", "canonical_id"]]
    r = r.merge(nm, left_on=["season", "player"], right_on=["season", "name"], how="left")
    r = r.rename(columns={"canonical_id": "player_id"}).drop(columns="name")
    pos = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos
    r["pos"] = r.player_id.map(pos)
    return r[~r.forfeited.astype(str).str.lower().eq("true")]


def _season_ranks() -> pd.DataFrame:
    st = season_totals().copy()
    st["pos_rank"] = st.groupby(["season", "pos"]).points.rank(ascending=False, method="first")
    return st


def realized_slot_values() -> pd.DataFrame:
    shift, lam, _ = market_params()
    h = haircut()
    R = ExpectedValue("auction", shift=shift).replacement[0]
    n_start, _, _ = starters()
    st = _season_ranks().set_index(["season", "player_id"])
    picks = rookie_picks()
    rows = []
    for _, p in picks.iterrows():
        sal0 = ROUND_SALARY.get(int(p["round"]), 1)
        vals, por_any, starter, star = [], False, False, False
        for j in range(4):
            y = int(p.season) + j
            if y > LAST_COMPLETE:
                vals.append(np.nan)
                continue
            key = (y, p.player_id)
            pts = float(st.points.get(key, 0.0)) if isinstance(p.player_id, str) else 0.0
            rank = float(st.pos_rank.get(key, 999)) if isinstance(p.player_id, str) else 999
            por = max(0.0, pts - R.get(p.pos, 1e9)) if p.pos in R else 0.0
            por_any |= por > 0
            starter |= rank <= n_start.get(p.pos, 10) + (10 if p.pos in ("RB", "WR") else 0) / 2
            star |= rank <= 5
            vals.append(max(0.0, lam * por - (sal0 + j)))
        observed = sum(1 for v in vals if not np.isnan(v))
        rows.append(dict(season=p.season, round=p["round"], pick=p.pick, overall=p.overall, owner=p.owner,
                         player=p.player, player_id=p.player_id, pos=p.pos, years_observed=observed,
                         value_y1=vals[0], value_y2=vals[1], value_y3=vals[2], value_y4=vals[3],
                         use_value=sum(h ** j * v for j, v in enumerate(vals) if not np.isnan(v)),
                         use_value_3yr=sum(h ** j * v for j, v in enumerate(vals[:3]) if not np.isnan(v)),
                         bust=not por_any, starter=starter, star=star))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "val_picks_realized.csv", index=False)
    return out


def slot_curve(real: pd.DataFrame) -> pd.DataFrame:
    full = real[real.years_observed == 4]
    iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(full.overall, full.use_value)
    iso3 = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(full.overall, full.use_value_3yr)
    rng = np.random.default_rng(7)
    boot = []
    for _ in range(500):
        b = full.sample(len(full), replace=True, random_state=int(rng.integers(1e9)))
        boot.append(IsotonicRegression(increasing=False, out_of_bounds="clip").fit(b.overall, b.use_value)
                    .predict(np.arange(1, 31)))
    boot = np.array(boot)
    slots = pd.DataFrame(dict(overall=np.arange(1, 31)))
    slots["round"] = (slots.overall - 1) // 10 + 1
    slots["use_value"] = iso.predict(slots.overall)
    slots["use_value_3yr"] = iso3.predict(slots.overall)
    slots["low_90"] = np.percentile(boot, 5, axis=0)
    slots["high_90"] = np.percentile(boot, 95, axis=0)
    band = pd.cut(full.overall, [0, 3, 7, 10, 13, 17, 20, 30], labels=["1.01-1.03", "1.04-1.07", "1.08-1.10",
                                                                        "2.01-2.03", "2.04-2.07", "2.08-2.10", "3rd"])
    odds = full.groupby(band, observed=True).agg(picks=("bust", "size"), bust=("bust", "mean"),
                                                 starter=("starter", "mean"), star=("star", "mean"),
                                                 avg_use_value=("use_value", "mean")).round(3).reset_index()
    odds = odds.rename(columns={"overall": "slots"})
    slots.round(1).to_csv(PROCESSED / "val_picks_slots.csv", index=False)
    odds.to_csv(PROCESSED / "val_picks_odds.csv", index=False)
    return slots, odds


def market_vs_use(slots: pd.DataFrame) -> pd.DataFrame:
    """Next year's picks: FantasyCalc value in dollars vs use value (3 seasons inside the horizon,
    one year out)."""
    import json
    from dfo.valuation.prices import SNAP
    h = haircut()
    prices = pd.read_csv(PROCESSED / "prices.csv")
    fut_cols = ["value_2027", "value_2028", "value_2029"]
    free = prices.season_dollars.fillna(0) + sum(h ** (k + 1) * prices[c].fillna(0) for k, c in enumerate(fut_cols))
    m = prices.fc_value.notna()
    fmap = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(prices.fc_value[m], free[m])
    d = json.load(open(SNAP / "market" / "fantasycalc" / "latest.json"))
    rows = []
    bands = {"Early": (1, 3), "Mid": (4, 7), "Late": (8, 10)}
    for x in d["data"]:
        name = x["player"]["name"]
        mm = re.match(r"(\d{4}) (\d)(?:st|nd|rd) \((Early|Mid|Late)\)", name)
        if x["player"]["position"] != "PICK" or not mm:
            continue
        year, rnd, band = int(mm.group(1)), int(mm.group(2)), mm.group(3)
        lo, hi = bands[band]
        sl = slots[slots.overall.between((rnd - 1) * 10 + lo, (rnd - 1) * 10 + hi)]
        wait = year - 2026                         # seasons until the rookie plays
        use = h ** wait * sl.use_value_3yr.mean()
        rows.append(dict(pick=name, fc_value=x["value"], market_dollars=round(float(fmap.predict([x["value"]])[0]), 1),
                         use_dollars=round(float(use), 1), use_low_90=round(float(h ** wait * sl.low_90.mean()), 1),
                         use_high_90=round(float(h ** wait * sl.high_90.mean()), 1)))
    out = pd.DataFrame(rows)
    out["market_minus_use"] = (out.market_dollars - out.use_dollars).round(1)
    out["lean"] = np.where(out.market_dollars > out.use_high_90, "sell",
                           np.where(out.market_dollars < out.use_low_90, "use/buy", "close call"))
    out.to_csv(PROCESSED / "val_picks_market.csv", index=False)
    return out


# ------------------------------------------------------------------ league pick trades
PICK_RE = re.compile(r"(20\d\d)\D*?(1st|2nd|3rd)", re.I)


def pick_trades(real: pd.DataFrame) -> pd.DataFrame:
    """Each past trade that moved picks: realized value received by each side over the next
    three seasons."""
    shift, lam, lam_now = market_params()
    h = haircut()
    R = ExpectedValue("auction", shift=shift).replacement[0]
    R_now = ExpectedValue("auction").replacement[0]          # in-season level, as in prices.py
    st = season_totals().set_index(["season", "player_id"]).points
    kept = pd.read_csv(PROCESSED / "auctions.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    nm = nm[nm.table == "auctions"][["season", "name", "canonical_id"]]
    kept = kept.merge(nm, left_on=["season", "player"], right_on=["season", "name"], how="left")
    salary = kept.dropna(subset=["canonical_id"]).groupby(["season", "canonical_id"]).salary.max()
    pos = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos
    tr = pd.read_csv(PROCESSED / "trades.csv")
    tr = tr[tr.trade_id.isin(tr[tr.asset_type == "pick"].trade_id)]
    tnm = pd.read_csv(PROCESSED / "name_matches.csv")
    round_avg = real[real.years_observed >= 3].groupby(["season", "round"]).use_value_3yr.mean()
    rows = []
    for tid, g in tr.groupby("trade_id"):
        y0 = int(str(g.season.iloc[0])[:4])
        if y0 + 3 > LAST_COMPLETE + 1:
            continue
        side_val: dict[str, float] = {}
        side_picks: dict[str, int] = {}
        now_val: dict[str, float] = {}
        unparsed = 0
        for _, a in g.iterrows():
            v = 0.0
            if a.asset_type == "pick":
                m = PICK_RE.search(str(a.asset))
                if not m:
                    unparsed += 1
                    continue
                yr, rnd = int(m.group(1)), int(m.group(2)[0])
                v = h ** max(0, yr - (y0 + 1)) * float(round_avg.get((yr, rnd), np.nan))
                side_picks[a.receiver] = side_picks.get(a.receiver, 0) + 1
            elif a.asset_type == "player":
                pid = _player_id(a.asset, y0, tnm)
                if pid is None:
                    unparsed += 1
                    continue
                # the season of the trade: counted in full for the upper bound only (trade dates are
                # not recorded, so an in-season deal's remaining weeks are unknown)
                por_now = max(0.0, float(st.get((y0, pid), 0.0)) - R_now.get(pos.get(pid), 1e9))
                now_val[a.receiver] = now_val.get(a.receiver, 0.0) + lam_now * por_now
                for j in range(3):
                    yy = y0 + 1 + j
                    s = salary.get((yy, pid))
                    if s is None or yy > LAST_COMPLETE:
                        continue                     # not kept that season: no value under contract
                    por = max(0.0, float(st.get((yy, pid), 0.0)) - R.get(pos.get(pid), 1e9))
                    v += h ** j * (lam * por - s)
            else:
                continue
            if np.isnan(v):
                unparsed += 1
                continue
            side_val[a.receiver] = side_val.get(a.receiver, 0.0) + v
        teams = sorted(set(g.receiver) | set(g.sender))
        if len(teams) != 2 or unparsed > 0:
            continue
        buyer = max(teams, key=lambda t: side_picks.get(t, 0) - sum(side_picks.get(o, 0) for o in teams if o != t))
        seller = [t for t in teams if t != buyer][0]
        rows.append(dict(trade_id=tid, season=g.season.iloc[0], pick_buyer=buyer, pick_seller=seller,
                         picks_to_buyer=side_picks.get(buyer, 0), picks_to_seller=side_picks.get(seller, 0),
                         buyer_received=round(side_val.get(buyer, 0.0), 1),
                         seller_received=round(side_val.get(seller, 0.0), 1),
                         buyer_trade_season=round(now_val.get(buyer, 0.0), 1),
                         seller_trade_season=round(now_val.get(seller, 0.0), 1)))
    out = pd.DataFrame(rows)
    out["buyer_margin"] = out.buyer_received - out.seller_received
    out["buyer_margin_with_trade_season"] = (out.buyer_margin + out.buyer_trade_season - out.seller_trade_season)
    out.to_csv(PROCESSED / "val_picks_trades.csv", index=False)
    return out


def _player_id(name: str, season: int, nm: pd.DataFrame) -> str | None:
    from dfo.common import player_key
    key = player_key(re.sub(r"\(.*?\)", "", str(name)).strip())
    cand = nm[nm.name.map(lambda n: player_key(str(n))) == key]
    if not len(cand):
        return None
    cand = cand.assign(gap=(cand.season - season).abs()).sort_values("gap")
    return cand.canonical_id.iloc[0]


def trade_summary(t: pd.DataFrame) -> pd.DataFrame:
    rng = np.random.default_rng(7)
    rows = []
    for label, col in [("next 3 seasons only", "buyer_margin"),
                       ("plus the full trade season for players", "buyer_margin_with_trade_season")]:
        m = t[col].to_numpy()
        boot = [rng.choice(m, len(m)).mean() for _ in range(2000)]
        rows.append(dict(counting=label, trades=len(t), pick_buyer_won=int((m > 0).sum()),
                         pick_seller_won=int((m < 0).sum()), avg_buyer_margin=round(float(m.mean()), 1),
                         median_buyer_margin=round(float(np.median(m)), 1),
                         margin_90ci=f"{np.percentile(boot, 5):.1f} to {np.percentile(boot, 95):.1f}"))
    s = pd.DataFrame(rows)
    s.to_csv(PROCESSED / "val_picks_trade_summary.csv", index=False)
    return s


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    real = realized_slot_values()
    slots, odds = slot_curve(real)
    print(odds.to_string(index=False))
    print(slots[["overall", "use_value", "low_90", "high_90", "use_value_3yr"]].iloc[[0, 2, 4, 6, 9, 12, 16, 19, 24, 29]].to_string(index=False))
    print(market_vs_use(slots).to_string(index=False))
    t = pick_trades(real)
    print(t.to_string(index=False))
    print(trade_summary(t).to_string(index=False))
