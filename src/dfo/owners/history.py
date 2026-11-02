"""League history, graded on what actually happened (exact league scoring from nflverse).

    PYTHONPATH=src python -m dfo.owners.history       # writes data/processed/hist_*.csv

Units (P15): points over the market replacement level for what assets produced; cap dollars for
salaries, prices and cap moved.

Trades. Real trades only: 2014-2025 from the trade archive, 2026 from the site (the old system's 2026
"summary" entries are trade ideas it logged, not trades). For each side, over the trade's season and
the next two:
  - players: points over replacement each season someone kept him (in the next season's keeper
    list), and the salary paid those seasons; the trade season itself is shown apart, since trade
    dates before 2026 are not recorded (an in-season deal only gets part of it)
  - picks: the average points that round produced in that draft (this league's rookies, first
    three seasons)
  - cap moved in the trade
Graded once two later seasons are complete (trades through the 2023 season).

Auctions, rookie picks, RFAs and tags are graded per owner from the valuation backtests.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, player_key
from dfo.valuation.auction import market_params
from dfo.valuation.expected import LAST_COMPLETE, ExpectedValue, season_totals

PICK_RE = re.compile(r"(20\d\d)\D*?(1st|2nd|3rd|round\s*([123]))", re.I)


def _pick(text: str) -> tuple[int, int] | None:
    m = re.search(r"(20\d\d)", str(text))
    r = re.search(r"\b(1st|2nd|3rd)\b|round\s*(\d)", str(text), re.I)
    if not m or not r:
        return None
    rnd = int(r.group(2)) if r.group(2) else {"1st": 1, "2nd": 2, "3rd": 3}[r.group(1).lower()]
    return int(m.group(1)), rnd


def _matcher():
    from dfo.ingest.player_ids import _index, match_name
    import csv
    ids = list(csv.DictReader(open(PROCESSED / "player_ids.csv", encoding="utf-8")))
    idx = _index(ids)
    return lambda name, season: match_name(re.sub(r"\(.*?\)", "", str(name)).strip(), season, idx)[0]


CAP_RE = re.compile(r"^\$(\d+)(?:\s+in\s+(20\d\d))?", re.I)
SLOT_RE = re.compile(r"(?:(20\d\d)\D*?#\s*(\d+))|(?:#\s*(\d+)\s+pick\s+in\s+(20\d\d))", re.I)


def _classify(kind: str, asset: str, season: int):
    """Archive rows typed 'player' that are really cap ("$25 in 2022") or a pick by slot
    ("2021 pick #16", "#10 pick in 2020"); conditional swaps stay unresolved."""
    text = str(asset)
    m = CAP_RE.match(text)
    if kind == "player" and m and "cut" not in text.lower() and "retire" not in text.lower():
        return "cap", float(m.group(1)), None
    m = SLOT_RE.search(text)
    if kind in ("player", "pick") and m:
        year = int(m.group(1) or m.group(4))
        slot = int(m.group(2) or m.group(3))
        return "pick", np.nan, (year, (slot - 1) // 10 + 1)
    return kind, np.nan, None


def trades() -> pd.DataFrame:
    """One row per asset moved: trade_id, season, receiver, sender, kind, asset, player_id, pick
    year/round, cap amount."""
    match = _matcher()
    t = pd.read_csv(PROCESSED / "trades.csv")
    t = t[~t.trade_id.str.startswith("T2026") & t.asset_type.isin(["player", "pick", "cap"])].copy()
    t["season"] = t.season.astype(str).str[:4].astype(int)
    rows = [dict(trade_id=r.trade_id, season=r.season, date=r.date, receiver=r.receiver, sender=r.sender,
                 kind=r.asset_type, asset=r.asset, cap=r.cap_amount) for r in t.itertuples()]
    s = pd.read_csv(PROCESSED / "site_transactions.csv")
    s = s[s.type == "trade"].copy()
    s["key"] = s.occurred_at.str[:16]
    for i, (k, g) in enumerate(sorted(s.groupby("key"), key=lambda kv: kv[0]), start=1):
        for r in g.itertuples():
            kind = "cap" if "cap space" in str(r.player) else ("pick" if "pick" in str(r.player).lower() else "player")
            rows.append(dict(trade_id=f"S2026-{i:02d}", season=2026, date=k[:10], receiver=r.to_owner,
                             sender=r.from_owner, kind=kind, asset=r.player,
                             cap=r.amount if kind == "cap" else np.nan))
    out = pd.DataFrame(rows)
    cls = [_classify(k, a, s_) for k, a, s_ in zip(out.kind, out.asset, out.season)]
    out["kind"] = [c[0] for c in cls]
    out["cap"] = [c[1] if c[0] == "cap" and pd.isna(cp) else cp for c, cp in zip(cls, out.cap)]
    out["player_id"] = [(match(a, s_) if k == "player" else None) for a, s_, k in zip(out.asset, out.season, out.kind)]
    out["player_id"] = out.player_id.where(out.player_id.astype(str).str.len() > 0, None)
    pk = [(c[2] if c[2] else (_pick(a) if k == "pick" else None)) for c, a, k in zip(cls, out.asset, out.kind)]
    out["pick_year"] = [p[0] if p else np.nan for p in pk]
    out["pick_round"] = [p[1] if p else np.nan for p in pk]
    out.to_csv(PROCESSED / "hist_trade_assets.csv", index=False)
    return out


def grade_trades(assets: pd.DataFrame) -> pd.DataFrame:
    shift, lam, _ = market_params()
    R = ExpectedValue("auction", shift=shift, horizons=(0,)).replacement[0]
    st = season_totals().set_index(["season", "player_id"]).points
    pos = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos
    a = pd.read_csv(PROCESSED / "auctions.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    a = a.merge(nm[nm.table == "auctions"][["season", "name", "canonical_id"]],
                left_on=["season", "player"], right_on=["season", "name"], how="left")
    kept_salary = a.dropna(subset=["canonical_id"]).groupby(["season", "canonical_id"]).salary.max()
    real = pd.read_csv(PROCESSED / "val_picks_realized.csv")
    real["pts3"] = real[["points_y1", "points_y2", "points_y3"]].sum(axis=1, min_count=1)
    round_pts = real[real.years_observed >= 3].groupby(["season", "round"]).pts3.mean()

    def por(pid, s):
        return max(0.0, float(st.get((s, pid), 0.0)) - R.get(pos.get(pid), 1e9))

    rows = []
    for tid, g in assets.groupby("trade_id"):
        y0 = int(g.season.iloc[0])
        teams = sorted(set(g.receiver.dropna()) | set(g.sender.dropna()))
        if len(teams) != 2:
            continue
        rec = {o: dict(trade_season=0.0, later=0.0, salary_later=0.0, pick_points=0.0, cap=0.0,
                       players=0, picks=0, unresolved=0) for o in teams}
        for r in g.itertuples():
            d = rec[r.receiver]
            if r.kind == "player":
                d["players"] += 1
                if not isinstance(r.player_id, str):
                    d["unresolved"] += 1
                    continue
                if y0 <= LAST_COMPLETE:
                    d["trade_season"] += por(r.player_id, y0)
                for s in (y0 + 1, y0 + 2):
                    sal = kept_salary.get((s, r.player_id))
                    if s <= LAST_COMPLETE and sal is not None:
                        d["later"] += por(r.player_id, s)
                        d["salary_later"] += float(sal)
            elif r.kind == "pick":
                d["picks"] += 1
                v = round_pts.get((r.pick_year, r.pick_round)) if pd.notna(r.pick_year) else None
                if v is None or pd.isna(v):
                    d["unresolved"] += 1
                else:
                    d["pick_points"] += float(v)
            elif r.kind == "cap" and pd.notna(r.cap):
                d["cap"] += float(r.cap)
                rec[r.sender]["cap"] -= float(r.cap) if r.sender in rec else 0.0
        graded = y0 + 2 <= LAST_COMPLETE
        a_, b_ = teams
        for o, other in ((a_, b_), (b_, a_)):
            me, them = rec[o], rec[other]
            rows.append(dict(trade_id=tid, season=y0, owner=o, partner=other, graded=graded,
                             players_in=me["players"], picks_in=me["picks"],
                             points_trade_season=round(me["trade_season"] - them["trade_season"], 1),
                             points_later=round(me["later"] + me["pick_points"] - them["later"] - them["pick_points"], 1),
                             salary_later=round(me["salary_later"] - them["salary_later"], 1),
                             cap_in=round(me["cap"], 1),
                             unresolved=me["unresolved"] + them["unresolved"]))
    out = pd.DataFrame(rows)
    # one cap-equivalent summary, at the auction's price per point (labeled; points and cap stay separate)
    out["net_later_cap_equiv"] = (lam * out.points_later - out.salary_later + out.cap_in).round(1)
    out.to_csv(PROCESSED / "hist_trades.csv", index=False)
    return out


def auctions_by_owner() -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "auctions.csv")
    a = a[a.kind == "auction"]
    bt = pd.read_csv(PROCESSED / "val_auction_backtest.csv")
    league_ppp = (bt.salary - 1).sum() / bt.exp_por0.sum()
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    a = a.merge(nm[nm.table == "auctions"][["season", "name", "canonical_id"]],
                left_on=["season", "player"], right_on=["season", "name"], how="left")
    a["age"] = a.season - pd.to_datetime(a.canonical_id.map(ids.birthdate), errors="coerce").dt.year
    rows = []
    for o, g in a.groupby("owner"):
        b = bt[bt.owner == o]
        spend = g.salary.sum()
        rec = dict(owner=o, auctions=g.season.nunique(), buys=len(g), avg_spend_per_auction=round(spend / g.season.nunique()),
                   avg_age_of_buys=round(g[g.pos.isin(["QB", "RB", "WR", "TE"])].age.mean(), 1),
                   price_vs_league=round(((b.salary - 1).sum() / max(b.exp_por0.sum(), 1e-9)) / league_ppp, 2) if len(b) else np.nan,
                   surplus_per_buy=round((b.realized0 - (b.salary - 1)).mean(), 1) if len(b) else np.nan,
                   graded_buys=len(b))
        for p in ["QB", "RB", "WR", "TE"]:
            rec[f"spend_share_{p}"] = round(g[g.pos == p].salary.sum() / spend, 2) if spend else 0
        rows.append(rec)
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "hist_auctions_by_owner.csv", index=False)
    return out


def rookies_by_owner() -> pd.DataFrame:
    from sklearn.isotonic import IsotonicRegression
    real = pd.read_csv(PROCESSED / "val_picks_realized.csv")
    real["pts4"] = real[["points_y1", "points_y2", "points_y3", "points_y4"]].sum(axis=1)
    full = real[real.years_observed == 4].copy()
    iso = IsotonicRegression(increasing=False, out_of_bounds="clip").fit(full.overall, full.pts4)
    full["vs_slot"] = full.pts4 - iso.predict(full.overall)
    rk = pd.read_csv(PROCESSED / "ranks_at_rfa.csv")[["season", "player_id", "rookie_rank"]]
    allp = real.merge(rk, on=["season", "player_id"], how="left")
    allp["consensus_order"] = allp.groupby("season").rookie_rank.rank(method="first")
    allp["reach"] = allp.consensus_order - allp.overall          # positive: taken earlier than consensus
    rows = []
    for o, g in allp.groupby("owner"):
        f = full[full.owner == o]
        rows.append(dict(owner=o, picks=len(g), first_rounders=int((g["round"] == 1).sum()),
                         points_vs_slot=round(f.vs_slot.mean(), 1) if len(f) else np.nan, graded_picks=len(f),
                         reach=round(g.reach.mean(), 1) if g.reach.notna().any() else np.nan,
                         ranked_picks=int(g.reach.notna().sum()),
                         share_RB=round((g.pos == "RB").mean(), 2), share_WR=round((g.pos == "WR").mean(), 2),
                         share_QB=round((g.pos == "QB").mean(), 2), share_TE=round((g.pos == "TE").mean(), 2)))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "hist_rookies_by_owner.csv", index=False)
    return out


def rfa_by_owner() -> pd.DataFrame:
    b = pd.read_csv(PROCESSED / "rfa_bids.csv")
    bt = pd.read_csv(PROCESSED / "val_rfa_backtest.csv")
    fin = bt.set_index(["season", "player"])
    b = b.join(fin[["exp_por0", "engine0"]], on=["season", "player"])
    rows = []
    owners = sorted(set(b.bidder.dropna()) | set(b.original_owner.dropna()))
    for o in owners:
        bids = b[b.bidder == o]
        own = bt[bt.original_owner == o]
        contested = own[own.outcome.isin(["matched", "changed_teams"])]
        won = b[(b.bidder == o) & (b.final_owner == o) & (b.original_owner != o)].drop_duplicates(["season", "player"])
        rows.append(dict(owner=o, bids=len(bids), players_bid_on=bids[["season", "player"]].drop_duplicates().shape[0],
                         avg_bid_vs_final=round((bids.bid / bids.final_price).mean(), 2) if len(bids) else np.nan,
                         won_others=len(won), own_rfas=len(own), match_rate=round((contested.outcome == "matched").mean(), 2)
                         if len(contested) else np.nan,
                         matched_value_vs_price=round((own[own.outcome == "matched"].realized0
                                                       - (own[own.outcome == "matched"].final - 1)).mean(), 1)
                         if (own.outcome == "matched").any() else np.nan))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "hist_rfa_by_owner.csv", index=False)
    return out


def tags_by_owner() -> pd.DataFrame:
    shift, lam, _ = market_params()
    R = ExpectedValue("auction", shift=shift, horizons=(0,)).replacement[0]
    st = season_totals().set_index(["season", "player_id"]).points
    d = pd.read_csv(PROCESSED / "designations.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    d = d.merge(nm[nm.table == "designations"][["season", "name", "canonical_id"]],
                left_on=["season", "player"], right_on=["season", "name"], how="left")
    pos = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").pos
    tb = pd.read_csv(PROCESSED / "tag_benchmarks.csv").set_index(["season", "position"]).tag_cost
    tags = d[d.kind == "franchise_tag"].copy()
    tags["pos"] = tags.canonical_id.map(pos)
    tags["cost"] = [tb.get((s, p), np.nan) for s, p in zip(tags.season, tags.pos)]
    tags["points"] = [max(0.0, float(st.get((s, pid), 0.0)) - R.get(p, 1e9)) if s <= LAST_COMPLETE else np.nan
                      for s, pid, p in zip(tags.season, tags.canonical_id, tags.pos)]
    tags["surplus"] = lam * tags.points - tags.cost
    out = tags.groupby("owner").agg(tags=("player", "size"), graded=("surplus", "count"),
                                    avg_tag_surplus=("surplus", "mean")).round(1).reset_index()
    out.to_csv(PROCESSED / "hist_tags_by_owner.csv", index=False)
    tags.to_csv(PROCESSED / "hist_tags.csv", index=False)
    return out


def summarize_trades(g: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for o, x in g.groupby("owner"):
        gr = x[x.graded]
        rows.append(dict(owner=o, trades=len(x), partners=x.partner.nunique(),
                         seasons_active=x.season.nunique(),
                         picks_in_minus_out=int((x.picks_in - g.set_index(["trade_id", "owner"]).loc[
                             list(zip(x.trade_id, x.partner)), "picks_in"].to_numpy()).sum()),
                         graded=len(gr),
                         won_later=int((gr.net_later_cap_equiv > 0).sum()),
                         avg_points_later=round(gr.points_later.mean(), 1) if len(gr) else np.nan,
                         avg_points_trade_season=round(gr.points_trade_season.mean(), 1) if len(gr) else np.nan,
                         avg_net_later_cap_equiv=round(gr.net_later_cap_equiv.mean(), 1) if len(gr) else np.nan))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "hist_trades_by_owner.csv", index=False)
    return out


def build() -> dict[str, pd.DataFrame]:
    assets = trades()
    g = grade_trades(assets)
    return dict(trades=summarize_trades(g), auctions=auctions_by_owner(), rookies=rookies_by_owner(),
                rfa=rfa_by_owner(), tags=tags_by_owner())


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    for k, v in build().items():
        print(f"\n== {k}\n{v.to_string(index=False)}")
