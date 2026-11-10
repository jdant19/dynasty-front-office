"""Three-year team outlook (2027-2029), and what a trade does to it.

    PYTHONPATH=src python -m dfo.decide.outlook [owner]     # decide_outlook_<owner>.md / .csv

The working model of a team for the next three seasons, built on the future-season simulator's parts
(sim/future.py), one season at a time:
  - who is under contract (prices.csv salaries plus the designation plan: tags, RFA matches, extensions)
  - cap committed: those salaries, dead money, cap traded, rookie salaries for picks owned
  - cap room = $300 - committed - $1 fillers to 18 players. RFA matches may take a team over the cap
    (tags and extensions may not), so a plan fits when everything but its matches fits; the team then
    buys nothing at auction. Otherwise the team gives something up (a planned designation is skipped
    for free; a real contract is cut and leaves 75% dead) until it fits
  - strength = best lineup from players under contract + what the room buys at that August's auction
    (the season's clearing price) + rookies from picks owned
A trade moves players, picks and cap between two teams; the outlook is rebuilt for both. The change
in strength each season, x0.88 a year, is the trade's later value in lineup points (so depth that
would never start, or cap a team has no room to give, is counted for what it really does), priced at
the league-average value of a point (P18). Expected payout by season (the future simulator, all ten
teams) is shown beside it.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.sim import future as F

HAIRCUT = 0.88
SEASON = 2026
CUT_DEAD = 0.75


class Outlook:
    def __init__(self):
        from dfo.valuation.auction import market_params
        from dfo.valuation.expected import ExpectedValue
        shift, lam, _ = market_params()
        self.repl = dict(ExpectedValue("auction", shift=shift, horizons=(0,)).replacement[0])
        self.prices = F.contracts()
        raw = pd.read_csv(PROCESSED / "prices.csv").set_index("player_id")
        self.planned = {y: set(self.prices[(self.prices[f"salary_{y}"].fillna(0) > 0)
                                           & (self.prices.player_id.map(raw[f"salary_{y}"]).fillna(0) == 0)].player_id)
                        for y in F.FUTURE}
        self.cap0 = F._cap_by_year()
        self.picks = pd.read_csv(PROCESSED / "site_picks.csv")
        self.picks = self.picks[~self.picks.used.astype(bool)]
        dist = pd.read_csv(PROCESSED / "val_picks_distribution.csv")
        cols = ["points_y1", "points_y2", "points_y3"]
        self.by_round = {1: dist[dist.slots.str.startswith("1.")][cols].mean(),
                         2: dist[dist.slots.str.startswith("2.")][cols].mean(),
                         3: dist[dist.slots == "3rd"][cols].mean()}
        self.rookie_salary = load_config("league")["contracts"]["rookie"]["salary_by_round"]
        self.base = F.strengths()
        self.price = self.base.drop_duplicates("season").set_index("season").price_per_point
        self.names = self.prices.set_index("player_id").player
        # RFA matches may take a team over the cap (league.yaml designations.rfa.match_may_exceed_cap);
        # tags and extensions may not, so a plan fits if everything but its RFA matches fits
        plan = pd.read_csv(PROCESSED / "designation_plan.csv")
        self.rfa_years = {}
        for r in plan[plan.type == "rfa"].itertuples():
            for y in F.FUTURE:
                if y >= int(r.offseason) and float(getattr(r, f"salary_{y}", 0) or 0) > 0:
                    self.rfa_years.setdefault(y, set()).add(r.player_id)
        self.allow_rfa_over_cap = load_config("league")["designations"]["rfa"].get("match_may_exceed_cap", False)
        self._cache = {}

    # ------------------------------------------------------------ one team, one season
    def _pick_cap(self, rnd: int, draft_year: int, y: int) -> float:
        return (self.rookie_salary.get(int(rnd), 1) + (y - draft_year)) if y >= draft_year else 0.0

    def _rookies(self, picks: list[tuple[int, int]], y: int) -> float:
        return float(sum(self.by_round[r].iloc[min(y - d, 2)] for d, r in picks if d <= y and r in self.by_round))

    def season(self, owner: str, y: int, add=(), remove=(), picks_in=(), picks_out=(), cap_out: float = 0.0) -> dict:
        """`add`/`remove`: player ids joining/leaving; picks as (draft_year, round); `cap_out`: cap this
        team sends for season y (negative: receives)."""
        p = self.prices
        g = p[((p.owner == owner) & ~p.player_id.isin(list(remove))) | p.player_id.isin(list(add))]
        g = g[g[f"salary_{y}"].fillna(0) > 0][["player_id", "pos", f"proj_points_{y}", f"salary_{y}"]]
        g = g.rename(columns={f"proj_points_{y}": "points", f"salary_{y}": "salary"}).dropna(subset=["points"])
        g = g.set_index("player_id")
        committed = self.cap0.get(owner, {}).get(y, 0.0) + cap_out
        committed += sum(float(p.loc[p.player_id == a, f"salary_{y}"].fillna(0).sum()) for a in add)
        committed -= sum(float(p.loc[(p.player_id == a) & (p.owner == owner), f"salary_{y}"].fillna(0).sum()) for a in remove)
        committed += sum(self._pick_cap(r, d, y) for d, r in picks_in) - sum(self._pick_cap(r, d, y) for d, r in picks_out)
        own = [(int(r.draft_year), int(r.round)) for r in self.picks[self.picks.owner == owner].itertuples()]
        for k in picks_out:
            if k in own:
                own.remove(k)
        own += list(picks_in)
        price = float(self.price[y])
        released = []

        def room_of(gg, com):
            return 300 - com - max(0, 18 - len(gg))

        room = room_of(g, committed)
        matched = (self.rfa_years.get(y, set()) & set(g.index)) if self.allow_rfa_over_cap else set()
        over_ok = lambda gg, r: sum(float(gg.salary.get(m, 0)) for m in matched if m in gg.index) + r >= 0
        while room < 0 and len(g) and not over_ok(g, room):
            base = F.lineup_points(g, self.repl)
            opts = []
            for pid, r in g.iterrows():
                freed = r.salary if pid in self.planned[y] else r.salary * (1 - CUT_DEAD)
                if freed <= 0.5:
                    continue
                gg = g.drop(pid)
                new_room = room_of(gg, committed - freed)
                loss = base - F.lineup_points(gg, self.repl)
                opts.append((new_room >= 0, -(loss - max(new_room, 0.0) / price), -loss / freed, pid, freed))
            if not opts:
                break
            fits = [x for x in opts if x[0]]
            # one release that fits: the one leaving the strongest team. Otherwise skip a planned
            # designation first (free, and the big salaries), then cut the cheapest contract per dollar
            plan = [x for x in opts if x[3] in self.planned[y]]
            _, _, _, pid, freed = (max(fits, key=lambda x: x[1]) if fits else
                                   max(plan or opts, key=lambda x: x[2]))
            released.append(str(self.names.get(pid, pid)) + (" (designation skipped)" if pid in self.planned[y] else " (cut)"))
            g = g.drop(pid)
            committed -= freed
            room = room_of(g, committed)
        lineup = F.lineup_points(g, self.repl)
        bought = max(room, 0.0) / price
        rook = self._rookies(own, y)
        note = ("over the cap through RFA matches (allowed); nothing bought at auction" if room < 0 else "")
        return dict(season=y, owner=owner, contracted=len(g), committed=round(committed), cap_room=round(room), note=note,
                    lineup_points=round(lineup, 1), points_bought=round(bought, 1), rookie_points=round(rook, 1),
                    strength=round(lineup + bought + rook, 1), released="; ".join(released))

    def team(self, owner: str, **moves) -> pd.DataFrame:
        cap = moves.pop("cap_out", {}) or {}
        key = (owner, tuple(sorted(moves.get("add", ()))), tuple(sorted(moves.get("remove", ()))),
               tuple(sorted(moves.get("picks_in", ()))), tuple(sorted(moves.get("picks_out", ()))), tuple(sorted(cap.items())))
        if key not in self._cache:
            self._cache[key] = pd.DataFrame([self.season(owner, y, cap_out=float(cap.get(y, 0)), **moves) for y in F.FUTURE])
        return self._cache[key]

    # ------------------------------------------------------------ a trade
    def trade(self, a: str, b: str, a_players=(), b_players=(), a_picks=(), b_picks=(), a_cap=None, b_cap=None) -> dict:
        """What `a` sends and `b` sends: players (ids), picks (draft_year, round), cap {season: $}."""
        a_cap, b_cap = a_cap or {}, b_cap or {}
        net = lambda x, z: {y: x.get(y, 0) - z.get(y, 0) for y in F.FUTURE}
        out = {}
        for o, give_p, get_p, give_k, get_k, cap in ((a, a_players, b_players, a_picks, b_picks, net(a_cap, b_cap)),
                                                    (b, b_players, a_players, b_picks, a_picks, net(b_cap, a_cap))):
            before = self.team(o)
            after = self.team(o, add=tuple(get_p), remove=tuple(give_p), picks_in=tuple(get_k), picks_out=tuple(give_k),
                              cap_out=cap)
            d = after.strength.to_numpy() - before.strength.to_numpy()
            pts = float(sum(v * HAIRCUT ** (y - SEASON) for v, y in zip(d, F.FUTURE)))
            out[o] = dict(before=before, after=after, delta=dict(zip(F.FUTURE, np.round(d, 1))), later_points=round(pts, 1))
        return out

    def payouts(self, changed: dict[str, pd.DataFrame] | None = None, n: int = 8000) -> pd.DataFrame:
        """Expected payout by season for every team, with some teams' outlook replaced."""
        st = self.base.copy()
        for o, t in (changed or {}).items():
            for r in t.itertuples():
                m = (st.owner == o) & (st.season == r.season)
                st.loc[m, "strength"] = r.strength
        return F.odds(st, n=n)


def to_markdown(o: Outlook, owner: str) -> str:
    t = o.team(owner)
    pay = o.payouts({owner: t}).query("owner == @owner").set_index("season")
    p = o.prices
    L = [f"# {owner}: three-year outlook (2027-2029)", "",
         f"Built {pd.Timestamp.now(tz='UTC').date()} from prices.csv, the designation plan, site picks, dead money and traded cap.", "",
         "| Season | Under contract | Cap committed | Cap room | Lineup points | Bought with room | Rookies | Strength | Expected payout | 1st | Top 4 |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in t.itertuples():
        q = pay.loc[r.season]
        L.append(f"| {r.season} | {r.contracted} | ${r.committed} | ${r.cap_room} | {r.lineup_points:.0f} | {r.points_bought:.0f} | "
                 f"{r.rookie_points:.0f} | {r.strength:.0f} | ${q.exp_payout:.0f} | {q.p_first:.0%} | {q.p_top4:.0%} |")
    rel = t[t.released != ""]
    for r in rel.itertuples():
        L += ["", f"{r.season}: the plan does not fit under the cap; given up to fit: {r.released}."]
    L += ["", "## Contracts and planned designations", "", "| Player | Pos | 2027 | 2028 | 2029 | Points 2027 |", "|---|---|---|---|---|---|"]
    g = p[(p.owner == owner) & (p[[f"salary_{y}" for y in F.FUTURE]].fillna(0).sum(axis=1) > 0)].sort_values("salary_2027", ascending=False)
    for r in g.itertuples():
        mark = lambda y: ("*" if r.player_id in o.planned[y] else "")
        L.append(f"| {r.player} | {r.pos} | " + " | ".join(
            (f"${getattr(r, f'salary_{y}'):.0f}{mark(y)}" if getattr(r, f"salary_{y}") > 0 else "") for y in F.FUTURE)
            + f" | {r.proj_points_2027:.0f} |" if pd.notna(r.proj_points_2027) else f"| {r.player} | {r.pos} | | | | |")
    L += ["", "\\* planned designation (tag, RFA match or extension), not a signed contract.", ""]
    pk = o.picks[o.picks.owner == owner].sort_values(["draft_year", "round"])
    L += ["Picks owned: " + ", ".join(f"{r.draft_year} round {r.round}" + (f" {r.label[r.label.find('('):]}" if "(" in str(r.label) else "")
                                      for r in pk.itertuples()) + "."]
    return "\n".join(L)


if __name__ == "__main__":
    import sys
    import warnings
    warnings.filterwarnings("ignore")
    who = sys.argv[1] if len(sys.argv) > 1 else "Jordan"
    o = Outlook()
    md = to_markdown(o, who)
    (PROCESSED / f"decide_outlook_{who.lower()}.md").write_text(md)
    o.team(who).to_csv(PROCESSED / f"decide_outlook_{who.lower()}.csv", index=False)
    print(md)
