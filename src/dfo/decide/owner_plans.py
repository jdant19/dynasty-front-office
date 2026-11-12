"""What each owner is likely to do with his expiring players next offseason (the model's opinion,
for Jordan to review and correct).

    PYTHONPATH=src python -m dfo.decide.owner_plans      # decide_owner_plans_2027.md / .csv

Built from how owners have behaved, not from what would be cheapest (2019-2026 on record):
- RFA: designating costs nothing (the owner can decline to match), and owners use 1.2-1.9 of their two
  slots a year, so each owner designates his two most valuable RFA-eligible players (market value from
  the auction model, prices.csv auction_cost_2027) when someone would bid above the opening price.
- Matching: owners matched 90% of RFAs the model valued at $50+, 76% at $25-50, 69% below $25
  (val_rfa_backtest.csv, 101 RFAs 2020-2026). A match may go over the cap.
- RFA price: final RFA prices ran 1.08x the model's auction value at $50+, 1.50x at $25-50 and 1.79x
  below $25 (bidding opens at 110% of last salary). Bids are limited to the bidders' cap room.
- Tag: owners tagged in 44-86% of offseasons, almost always a star and usually at a loss (2024-25:
  A.J. Brown, Tyreek Hill, Ekeler, Metcalf, Higgins and Barkley returned less than the tag cost). So an
  owner tags his best tag-eligible player not already designated when that player's market value is
  at least half the tag cost (a starter the owner would hate to lose).
- Everyone else goes to the auction.
Each row carries the reason and a chance; Jordan's corrections go in config/owner_plans.yaml and win.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, ROOT, load_config
from dfo.rules.contracts import franchise_tag_cost, rfa_starting_bid

Y = 2027
MATCH = [(50, 0.90), (25, 0.76), (0, 0.69)]          # market value >= x: match rate
RFA_PRICE = [(50, 1.08), (25, 1.50), (0, 1.79)]      # market value >= x: final price / market value
TAG_MIN_SHARE = 0.5


def _tier(v: float, table) -> float:
    return next(r for x, r in table if v >= x)


def tag_costs(p: pd.DataFrame) -> dict[str, int]:
    out = {}
    for pos, g in p.groupby("pos"):
        top = sorted(g.salary_2026.fillna(0), reverse=True)[:5]
        if len(top) == 5:
            out[pos] = franchise_tag_cost(top)
    return out


def overrides() -> dict:
    f = ROOT / "config" / "owner_plans.yaml"
    if not f.exists():
        return {}
    import yaml
    return yaml.safe_load(f.read_text()) or {}


def predict(exclude_owner: str | None = None) -> pd.DataFrame:
    p = pd.read_csv(PROCESSED / "prices.csv")
    tags = tag_costs(p)
    e = p[(p.rights.fillna("none") != "none") & (p.salary_2027.fillna(0) == 0) & p.owner.notna()].copy()
    if exclude_owner:
        e = e[e.owner != exclude_owner]
    e["market"] = e.auction_cost_2027.fillna(1).clip(lower=1)
    rows = []
    for owner, g in e.groupby("owner"):
        g = g.sort_values("market", ascending=False)
        rfa = g[g.rights.str.contains("Restricted")]
        rfa = rfa[[m > rfa_starting_bid(int(s or 0)) for m, s in zip(rfa.market, rfa.salary_2026.fillna(0))]].head(2)
        used = set(rfa.player_id)
        for r in rfa.itertuples():
            start = rfa_starting_bid(int(r.salary_2026 or 0))
            price = max(start, round(r.market * _tier(r.market, RFA_PRICE)))
            pm = _tier(r.market, MATCH)
            rows.append(dict(owner=owner, player_id=r.player_id, player=r.player, pos=r.pos, salary_2026=int(r.salary_2026 or 0),
                             market_value=int(r.market), action="RFA", expected_price=int(price), p_keep=pm,
                             reason=f"one of his two most valuable RFA-eligible players; owners match {pm:.0%} at this value"))
        tg = g[g.rights.str.contains("Franchise") & ~g.player_id.isin(used)]
        tagged = None
        if len(tg):
            r = tg.iloc[0]
            cost = tags.get(r.pos, 2)
            if r.market >= TAG_MIN_SHARE * cost:
                tagged = r.player_id
                rows.append(dict(owner=owner, player_id=r.player_id, player=r.player, pos=r.pos,
                                 salary_2026=int(r.salary_2026 or 0), market_value=int(r.market), action="tag",
                                 expected_price=int(cost), p_keep=0.9,
                                 reason=f"his best tag-eligible player; owners tag stars even above value (tag ${cost})"))
        for r in g[~g.player_id.isin(used | {tagged})].itertuples():
            rows.append(dict(owner=owner, player_id=r.player_id, player=r.player, pos=r.pos, salary_2026=int(r.salary_2026 or 0),
                             market_value=int(r.market), action="auction", expected_price=int(r.market), p_keep=0.0,
                             reason="no designation left for him" if r.market >= 15 else "low value"))
    out = pd.DataFrame(rows)
    # Jordan's corrections win
    for owner, plays in (overrides() or {}).items():
        for name, how in (plays or {}).items():
            m = (out.owner == owner) & (out.player == name)
            if m.any():
                out.loc[m, "action"] = how["action"]
                out.loc[m, "p_keep"] = how.get("p_keep", 0.95 if how["action"] in ("RFA", "tag") else 0.0)
                if "price" in how:
                    out.loc[m, "expected_price"] = how["price"]
                out.loc[m, "reason"] = f"Jordan: {how.get('note', 'override')}"
    out["made_at"] = pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")
    return out.sort_values(["owner", "market_value"], ascending=[True, False])


def to_markdown(d: pd.DataFrame) -> str:
    L = ["# What other owners will likely do in the 2027 offseason (model's read, for review)", "",
         "Correct anything in config/owner_plans.yaml (owner -> player -> action: RFA / tag / auction).", "",
         "| Owner | Player | Pos | Market value | Likely action | Chance he stays | Expected price | Why |", "|---|---|---|---|---|---|---|---|"]
    for r in d[(d.action != "auction") | (d.market_value >= 20)].itertuples():
        L.append(f"| {r.owner} | {r.player} | {r.pos} | ${r.market_value} | {r.action} | {r.p_keep:.0%} | ${r.expected_price} | {r.reason} |")
    return "\n".join(L)


if __name__ == "__main__":
    d = predict(exclude_owner="Jordan")
    d.to_csv(PROCESSED / "decide_owner_plans_2027.csv", index=False)
    md = to_markdown(d)
    (PROCESSED / "decide_owner_plans_2027.md").write_text(md)
    print(md)
