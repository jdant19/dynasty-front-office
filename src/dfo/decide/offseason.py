"""Offseason plan scenarios: which designations to make, played through next August's auction.

    PYTHONPATH=src python -m dfo.decide.offseason      # decide_offseason_2027.md / .csv

Question: which set of designations (RFA match, tag, let go) gives the best team and payout?
Each scenario is played through, player by player:
1. Every other team follows its likely plan (designation_plan.csv). Everyone not kept is the auction
   pool. A let-go RFA signs with the team that has the most room; a skipped tag goes to the auction.
2. Auction prices: each pool player's market price (prices.csv auction_cost, the league's past auctions)
   scaled so the money spent equals the room the league has for the players it will roster.
3. Jordan buys the set of players that adds the most to his best lineup within his room. A team over
   the cap (allowed only through RFA matches) is locked out and takes $0 leftovers after the auction.
4. The other teams then buy in price order: each player goes to the team he helps most that can pay.
5. Every team's 2027 strength is its best lineup (projected points) plus rookies; the future
   simulator turns the ten strengths into expected payout. 2028-29 come from the three-year outlook
   with the same designations (a matched RFA can be tagged the year after; a tagged player cannot be
   designated the year after).
"""
from __future__ import annotations

import itertools

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.decide.outlook import Outlook
from dfo.sim import future as F

Y = 2027
ROSTER = 18
ME = "Jordan"
SLOTS = dict(F.SLOTS)


def _lineup(pts: dict[str, list[float]], repl: dict[str, float]) -> float:
    """Fast best-lineup points from points by position (same rule as sim/future.lineup_points)."""
    total, spare = 0.0, []
    for pos, k in SLOTS.items():
        v = sorted(pts.get(pos, ()), reverse=True)
        top = v[:k] + [repl[pos]] * (k - len(v[:k]))
        total += sum(max(x, repl[pos]) for x in top)
        if pos in F.FLEX_POS and len(v) > k:
            spare.append(v[k])
    return total + max(spare + [max(repl[p] for p in F.FLEX_POS)])


class Auction:
    def __init__(self, outlook: Outlook | None = None):
        self.o = outlook or Outlook()
        self.p = self.o.prices.set_index("player_id")
        self.repl = self.o.repl
        self.owners = sorted(x for x in self.p.owner.dropna().unique() if x)
        self.name = self.p.player

    def play(self, keep: dict[str, float], let_go_rfa: tuple[str, ...] = (), star_premium: float = 1.0,
             not_available: tuple[str, ...] = ()) -> dict:
        """`keep`: Jordan's designated players -> 2027 salary (overrides the plan). Planned designations
        of his not in `keep` are dropped: to RFA (signs elsewhere) if in `let_go_rfa`, else the auction."""
        p = self.p.copy()
        sal = p[f"salary_{Y}"].fillna(0).copy()
        planned = self.o.planned[Y]
        mine_planned = [i for i in planned if p.owner.get(i) == ME]
        committed = {o: self.o.cap0.get(o, {}).get(Y, 0.0) for o in self.owners}
        for i in mine_planned:
            committed[ME] -= sal[i]
            sal[i] = 0.0
        for i, s in keep.items():
            sal[i] = s
            committed[ME] += s
        pts = p[f"proj_points_{Y}"]
        roster = {o: [i for i in p.index[(p.owner == o) & (sal > 0)] if pd.notna(pts[i])] for o in self.owners}
        room = {o: 300 - committed[o] - max(0, ROSTER - len(roster[o])) for o in self.owners}
        for i in let_go_rfa:                                   # signs with the team with the most room
            w = max((o for o in self.owners if o != ME), key=lambda o: room[o])
            price = float(p.auction_cost_2027.get(i, 1))
            roster[w].append(i)
            room[w] -= price - 1
        held = set(itertools.chain.from_iterable(roster.values()))
        pool = p[(~p.index.isin(held)) & pts.notna() & p.pos.isin(list(SLOTS))].copy()
        pool = pool[~pool.index.isin(list(not_available))]       # sensitivity: kept by their teams after all
        pool["base"] = pool.auction_cost_2027.fillna(1).clip(lower=1).astype(float)
        pool.loc[pool.base >= 50, "base"] *= star_premium          # sensitivity: stars go for more
        pool = pool.sort_values(["base", f"proj_points_{Y}"], ascending=False)
        spots = sum(max(0, ROSTER - len(r)) for r in roster.values())
        money = sum(max(0.0, room[o]) + max(0, ROSTER - len(roster[o])) for o in self.owners if room[o] >= 0)
        sold = pool.head(spots)
        f = float(np.clip((money - len(sold)) / max(1.0, (sold.base - 1).sum()), 0.4, 3.0))
        pool["price"] = (1 + f * (pool.base - 1)).round()
        by_pos = lambda ids: {pos: [float(pts[i]) for i in ids if p.pos[i] == pos] for pos in SLOTS}
        budget = {o: max(0.0, room[o]) + max(0, ROSTER - len(roster[o])) if room[o] >= 0 else 0.0 for o in self.owners}
        bought = {o: [] for o in self.owners}
        avail = list(pool.index[: spots + 40])
        price = pool.price.to_dict()

        def gain(o, i, extra=()):
            ids = roster[o] + bought[o] + list(extra)
            return _lineup(by_pos(ids + [i]), self.repl) - _lineup(by_pos(ids), self.repl)

        # Jordan first: the set that adds most to his lineup within budget (greedy by points per dollar,
        # then by points, keeping the better; then single upgrades)
        if budget[ME] > 0:
            def fill(rule):
                got, left, need = [], budget[ME], max(0, ROSTER - len(roster[ME]))
                while need > 0:
                    cands = [(gain(ME, i, got), i) for i in avail if i not in got and price[i] <= left - (need - 1)]
                    cands = [(g, i) for g, i in cands if g > 0.5]
                    if not cands:
                        break
                    g, i = max(cands, key=(lambda c: c[0] / price[c[1]]) if rule == "ratio" else (lambda c: c[0]))
                    got.append(i)
                    left -= price[i]
                    need -= 1
                return got
            value = lambda got: _lineup(by_pos(roster[ME] + got), self.repl)
            best = max((fill("ratio"), fill("points")), key=value)
            improved = True
            while improved:
                improved = False
                spent = sum(price[i] for i in best)
                need_after = max(0, ROSTER - len(roster[ME]) - len(best))
                for out_i in list(best):
                    for in_i in avail:
                        if in_i in best or price[in_i] - price[out_i] > budget[ME] - spent - need_after:
                            continue
                        trial = [x for x in best if x != out_i] + [in_i]
                        if value(trial) > value(best) + 0.5:
                            best, improved = trial, True
                            break
                    if improved:
                        break
            bought[ME] = best
            budget[ME] -= sum(price[i] for i in best)
            avail = [i for i in avail if i not in best]
        # everyone else, in price order
        left_over = []
        for i in avail:
            need = {o: ROSTER - len(roster[o]) - len(bought[o]) for o in self.owners}
            can = [o for o in self.owners if o != ME and need[o] > 0 and budget[o] - (need[o] - 1) >= price[i]]
            if not can:
                left_over.append(i)
                continue
            w = max(can, key=lambda o: (gain(o, i), budget[o]))
            bought[w].append(i)
            budget[w] -= price[i]
        left_over += [i for i in pool.index[spots + 40:]]
        # after the auction: $0 leftovers, best first, teams in order of need (locked-out teams included)
        free = {o: [] for o in self.owners}
        for o in sorted(self.owners, key=lambda o: -(ROSTER - len(roster[o]) - len(bought[o]))):
            while len(roster[o]) + len(bought[o]) + len(free[o]) < ROSTER and left_over:
                g = [(gain(o, i, free[o]), float(pts[i]), i) for i in left_over[:60]]
                _, _, i = max(g)
                free[o].append(i)
                left_over.remove(i)
        rows = []
        for o in self.owners:
            ids = roster[o] + bought[o] + free[o]
            own = [(int(r.draft_year), int(r.round)) for r in self.o.picks[self.o.picks.owner == o].itertuples()]
            rows.append(dict(owner=o, room=round(room[o]), lineup=round(_lineup(by_pos(ids), self.repl), 1),
                             rookies=round(self.o._rookies(own, Y), 1)))
        t = pd.DataFrame(rows)
        t["strength"] = t.lineup + t.rookies
        nm = lambda ids: ", ".join(f"{self.name[i]} (${int(price.get(i, 0))})" if i in price and i in bought[ME]
                                   else str(self.name[i]) for i in ids)
        return dict(teams=t, price_factor=round(f, 2), jordan_room=round(room[ME]),
                    jordan_bought=nm(bought[ME]), jordan_free=nm(free[ME]),
                    jordan_spent=int(sum(price[i] for i in bought[ME])),
                    prices=pool[["player", "pos", f"proj_points_{Y}", "base", "price"]].head(60))


def scenarios(gibbs_prices=(98, 110, 125)) -> pd.DataFrame:
    o = Outlook()
    a = Auction(o)
    p = a.p
    gid = p.index[p.player == "Jahmyr Gibbs"][0]
    lid = p.index[p.player == "CeeDee Lamb"][0]
    tag = float(p.loc[lid, f"salary_{Y}"])
    base = F.strengths()
    cases = [("Let both go", {}, (gid,), None, False)]
    cases += [("Tag Lamb, let Gibbs go", {lid: tag}, (gid,), None, True)]
    for gp in gibbs_prices:
        cases += [(f"Match Gibbs at ${gp}, Lamb to the auction", {gid: gp}, (), gp, False),
                  (f"Tag Lamb + match Gibbs at ${gp}", {lid: tag, gid: gp}, (), gp, True)]
    rows = []
    for label, keep, rfa_out, gp, lamb in cases:
        r = a.play(keep, rfa_out)
        st = base.copy()
        for x in r["teams"].itertuples():
            st.loc[(st.owner == x.owner) & (st.season == Y), "strength"] = x.strength
        # later seasons: the outlook with the same designations (matched Gibbs is tagged in 2028 per the
        # plan; a let-go player is gone)
        gone = tuple(i for i, kept in ((gid, gp is not None), (lid, lamb)) if not kept)
        later = o.team(ME, remove=gone)
        for x in later.itertuples():
            if x.season > Y:
                st.loc[(st.owner == ME) & (st.season == x.season), "strength"] = x.strength
        od = F.odds(st, n=12000).query("owner == @ME").set_index("season")
        me = r["teams"].set_index("owner").loc[ME]
        rows.append(dict(scenario=label, room_2027=r["jordan_room"], auction_price_factor=r["price_factor"],
                         bought=r["jordan_bought"], spent=r["jordan_spent"], free_after_auction=r["jordan_free"],
                         lineup_2027=me.lineup, strength_2027=me.strength,
                         rank_2027=int((r["teams"].strength > me.strength).sum() + 1),
                         payout_2027=round(od.exp_payout[2027]), p_first_2027=round(od.p_first[2027], 3),
                         p_top4_2027=round(od.p_top4[2027], 3),
                         payout_2028=round(od.exp_payout[2028]), payout_2029=round(od.exp_payout[2029]),
                         payout_3yr=round(od.exp_payout[2027] + od.exp_payout[2028] + od.exp_payout[2029])))
    out = pd.DataFrame(rows).sort_values("payout_3yr", ascending=False)
    out.insert(0, "made_at", pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"))
    out.to_csv(PROCESSED / "decide_offseason_2027.csv", index=False)
    a.play({lid: tag, gid: 98})["prices"].to_csv(PROCESSED / "decide_offseason_2027_auction_prices.csv", index=False)
    return out


def to_markdown(d: pd.DataFrame) -> str:
    L = ["# 2027 offseason plan scenarios (Jordan)", "", f"Built {d.made_at.iloc[0]}. Expected payout in $; 2028-29 from the three-year outlook.", "",
         "| Scenario | Room | 2027 lineup | League rank | Payout 2027 | 1st | Top 4 | Payout 2028 | 2029 | Three years |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for r in d.itertuples():
        L.append(f"| {r.scenario} | ${r.room_2027} | {r.lineup_2027:.0f} | {r.rank_2027} | ${r.payout_2027} | {r.p_first_2027:.0%} | "
                 f"{r.p_top4_2027:.0%} | ${r.payout_2028} | ${r.payout_2029} | ${r.payout_3yr} |")
    L += [""]
    for r in d.itertuples():
        L.append(f"- **{r.scenario}:** buys {r.bought or 'nothing'} (${r.spent}); after the auction adds {r.free_after_auction or 'nobody'}.")
    return "\n".join(L)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 150)
    d = scenarios()
    md = to_markdown(d)
    (PROCESSED / "decide_offseason_2027.md").write_text(md)
    print(md)
