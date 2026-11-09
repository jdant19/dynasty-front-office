"""Trade search: Jordan's best deals with each partner, with opening, target and walk-away versions.

    PYTHONPATH=src python -m dfo.decide.trade_search [--top 10] [--sims 3000]

Scored like every trade (decide/trades.py, P18). The search runs in two passes:
1. Quick pass, every partner: each of the partner's players who would raise Jordan's lineup, for each
   package of up to two of Jordan's spare players and picks the partner values. This season's change
   in each team's lineup points is priced at what a few points do to that team's finish odds now (the
   simulator's slope from today's standings); later seasons at the league-average $0.53 a point.
   Illegal packages (salary rule, roster, deadline) are dropped. A package worth more to the two teams
   together than it costs is a candidate.
2. Full pass, the best candidates: the season re-simulated with both rosters changed, which also counts
   the partner getting stronger (it matters most with Russ and Johnny, the teams chasing Jordan).
Versions: the gap between the two sides is closed with next season's cap ($1 of 2027 cap = 1.6 points
= $0.85 to whoever gets it). Opening: the partner gains $5. Target: the joint gain split evenly.
Walk-away: Jordan gains $0, the most he should give. Cap considerations sit outside salary matching.
Acceptance odds are not modeled yet: no real declines are on record (the site's were tests), so the
output shows each partner's gain and how often he has traded instead. A draft message comes with each
deal; nothing is ever sent.
"""
from __future__ import annotations

import itertools
import re

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.decide.trades import SEASON, Assets, Trade, live

ME = "Jordan"
OPENING_GAIN = 5.0
MIN_TARGET_GAIN = 8.0          # lineup points over the rest of the season a target must add for Jordan
GIVE_POOL = 12                 # Jordan's cheapest-to-lose players considered for a package (up to 3)
MAX_TARGETS = 4


def _team_names() -> dict[str, str]:
    out = {}
    for o, v in load_config("owners")["owners"].items():
        for k in ("current_team",):
            if v.get(k):
                out[v[k].lower()] = o
    return out


def picks_held(owner: str) -> list[tuple]:
    p = pd.read_csv(PROCESSED / "site_picks.csv")
    p = p[(p.owner == owner) & ~p.used.astype(bool) & (p.draft_year > SEASON)]
    names = _team_names()
    out = []
    for r in p.itertuples():
        m = re.search(r"\(from ([^)]+)\)", str(r.label))
        orig = names.get(m.group(1).lower(), m.group(1)) if m else owner
        out.append((int(r.draft_year), int(r.round), orig))
    return sorted(out)


class Search:
    def __init__(self):
        from dfo.valuation import conversions
        self.st = live()
        self.conv = conversions.load()
        self.later = float(self.conv["dollars_per_point_later"])
        self.cap_dollar = float(self.conv["points_per_cap_dollar"]) * self.later      # $ of payout per $1 of 2027 cap
        self.now = pd.read_csv(PROCESSED / "dial.csv").set_index("owner").per_point_2026
        self.base = {o: self.st.lineup(r) for o, r in self.st.rosters.items()}
        # each team's cheapest drops (this season's lineup loss + later value), for the quick pass
        order = {}
        for o, r in self.st.rosters.items():
            w = float(self.now.get(o, 0.0))
            cost = {p: w * (self.base[o] - self.st.lineup(r - {p})) + self.later * max(0.0, float(self.st.drop_later.get(p, 0.0)))
                    for p in r}
            order[o] = sorted(cost, key=cost.get)
        self.st.drop_order = order
        tr = pd.read_csv(PROCESSED / "hist_trade_assets.csv").drop_duplicates(["trade_id", "receiver"])
        seasons = pd.read_csv(PROCESSED / "season_results.csv").groupby("owner").season.nunique()
        self.trades_per_season = (tr.groupby("receiver").trade_id.nunique() / seasons).fillna(0).round(2)

    # quick value of one side, $ of payout
    def quick(self, owner: str, gets: Assets, gives: Assets) -> dict:
        s = self.st.side(owner, gets, gives)
        now = float(self.now.get(owner, 0.0)) * s["lineup_points_now"]
        return dict(now=now, later=float(s["payout_later"]), total=now + float(s["payout_later"]), side=s)

    def spare(self) -> list:
        """Jordan's assets in order of what they cost him: players by this season's lineup loss plus
        later surplus, and picks."""
        r = self.st.rosters[ME]
        out = []
        for p in r:
            loss = self.base[ME] - self.st.lineup(r - {p})
            cost = float(self.now.get(ME)) * loss + self.later * float(self.st.surplus.get(p, 0.0))
            out.append(("player", p, cost))
        for k in picks_held(ME):
            out.append(("pick", k, self.later * self.st.pick_points(*k)))
        return out

    def candidates(self, partner: str) -> list[dict]:
        st = self.st
        mine = st.rosters[ME]
        theirs = st.rosters[partner]
        # targets: what each of his players adds to Jordan's lineup
        gains = []
        for p in theirs:
            g = st.lineup(mine | {p}) - self.base[ME]
            if g >= MIN_TARGET_GAIN:
                gains.append((g, p))
        targets = [p for _, p in sorted(gains, reverse=True)[:MAX_TARGETS]]
        if not targets:
            return []
        # Jordan's side: players only. Later value is worth the same $0.53 a point to every team, so picks
        # and cap move value from one side to the other without creating any; they are used to settle the
        # gap (the versions below), not to find the deal. The joint gain comes from this season (a point
        # matters more to a contender) and from roster fit.
        sal = lambda ids: [int(round(float(st.salary.get(x, 0)))) for x in ids]
        from dfo.rules.contracts import TradeSide, trade_salaries_legal
        room = getattr(st, "cap_room", {})
        out = []
        for t in targets:
            t_gain = st.lineup(mine | {t}) - self.base[ME]
            pool = []
            for kind, a, cost in self.spare():
                if kind != "player":
                    continue
                loss = self.base[ME] - st.lineup(mine - {a})
                if loss < 0.8 * t_gain:
                    pool.append((cost, -float(st.salary.get(a, 0)), a))
            # the cheapest to lose, plus the biggest salaries among the rest (fillers for the salary rule)
            cheap = [a for _, _, a in sorted(pool)[:GIVE_POOL - 4]]
            fill = [a for _, _, a in sorted(pool, key=lambda x: x[1]) if a not in cheap][:4]
            pool = cheap + fill
            t_sal = float(st.salary.get(t, 0))
            for k in (1, 2, 3):
                for combo in itertools.combinations(pool, k):
                    # three-player packages only when salaries have to match
                    if k == 3 and max([t_sal] + [float(st.salary.get(x, 0)) for x in combo]) <= 20:
                        continue
                    ok, why = trade_salaries_legal(TradeSide(sal(combo), 0, room.get(ME)),
                                                   TradeSide(sal([t]), 0, room.get(partner)))
                    if not ok:
                        continue
                    trade = Trade(ME, partner, Assets(players=list(combo)), Assets(players=[t]), label=partner)
                    j = self.quick(ME, trade.b_gives, trade.a_gives)
                    q = self.quick(partner, trade.a_gives, trade.b_gives)
                    joint = j["total"] + q["total"]
                    if joint <= 0:
                        continue
                    out.append(dict(partner=partner, trade=trade, jordan=j["total"], partner_gain=q["total"],
                                    joint=joint, salary_rule=why))
        # best package per target
        best = {}
        for c in out:
            key = c["trade"].b_gives.players[0]
            if key not in best or c["joint"] > best[key]["joint"]:
                best[key] = c
        return sorted(best.values(), key=lambda c: -c["joint"])

    def versions(self, j: float, p: float) -> dict:
        """2027 cap Jordan sends (negative: he receives) for the three versions."""
        joint = j + p

        def cap_for(partner_target):
            return int(round((partner_target - p) / self.cap_dollar))
        v = {}
        for name, pt in (("opening", min(OPENING_GAIN, joint)), ("target", joint / 2), ("walk_away", joint)):
            c = cap_for(pt)
            v[name] = dict(cap_2027_to_partner=c, jordan=round(j - c * self.cap_dollar),
                           partner=round(p + c * self.cap_dollar))
        return v


def describe_versions(st, t: Trade, v: dict) -> list[str]:
    out = []
    for name in ("opening", "target", "walk_away"):
        x = v[name]
        c = x["cap_2027_to_partner"]
        cap = (f" + ${c} of 2027 cap from you" if c > 0 else f" + ${-c} of 2027 cap to you" if c < 0 else "")
        out.append(f"{name.replace('_', '-')}: {st.describe(t.a_gives)}{cap} -> you {x['jordan']:+d}, him {x['partner']:+d}")
    return out


def message(st, t: Trade, v: dict) -> str:
    c = v["opening"]["cap_2027_to_partner"]
    extra = f" and ${c} of 2027 cap" if c > 0 else ""
    ask = f" if you add ${-c} of 2027 cap" if c < 0 else ""
    return (f"{t.b}, would you do {st.describe(t.b_gives)} for {st.describe(t.a_gives)}{extra}{ask}? "
            f"Open to tweaking it.")


def run(top: int = 10, sims: int = 3000) -> pd.DataFrame:
    s = Search()
    st = s.st
    partners = [o for o in st.rosters if o != ME]
    cands = []
    for p in partners:
        cands += s.candidates(p)[:3]
    cands = sorted(cands, key=lambda c: -c["joint"])[:top]
    st.drop_order = {}                                # full pass: exact forced drops
    base = st.season_odds(None, sims)
    rows = []
    for c in cands:
        t = c["trade"]
        e = st.evaluate(t, n=sims, base=base).set_index("owner")
        j, p = float(e.payout[ME]), float(e.payout[t.b])
        v = s.versions(j, p)
        rows.append(dict(
            partner=t.b, you_get=st.describe(t.b_gives), you_give=st.describe(t.a_gives),
            jordan_now=e.payout_now[ME], jordan_later=e.payout_later[ME], jordan=round(j),
            partner_now=e.payout_now[t.b], partner_later=e.payout_later[t.b], partner_gain=round(p),
            joint=round(j + p), your_1st=f"{e.p_first_before[ME]:.0%} -> {e.p_first_after[ME]:.0%}",
            your_title=f"{e.p_playoff_champ_before[ME]:.0%} -> {e.p_playoff_champ_after[ME]:.0%}",
            partner_trades_per_season=float(s.trades_per_season.get(t.b, 0.0)),
            forced_drops="; ".join(x for x in e.forced_drops if x),
            salary_rule=c["salary_rule"], quick_joint=round(c["joint"]),
            opening=describe_versions(st, t, v)[0], target=describe_versions(st, t, v)[1],
            walk_away=describe_versions(st, t, v)[2], draft_message=message(st, t, v),
            status="idea", inputs=st.made_at))
    out = pd.DataFrame(rows).sort_values("joint", ascending=False)
    out.to_csv(PROCESSED / "decide_trade_search.csv", index=False)
    (PROCESSED / "decide_trade_search.md").write_text(to_markdown(out, st))
    return out


def to_markdown(d: pd.DataFrame, st) -> str:
    L = [f"# Trade ideas for Jordan (from Week {st.week0} projections)", "",
         f"Inputs: projections {st.made_at}; rosters and cap from the site. Ideas only: nothing is proposed until "
         "Jordan says so. Values in expected payout $ (P18); 'later' at $0.53 a point.", ""]
    good = d[d.joint > 0]
    for r in good.itertuples():
        L += [f"## {r.partner}: you get {r.you_get}", "",
              f"- You give {r.you_give}. You {r.jordan:+d} (this season {r.jordan_now:+.0f}, later {r.jordan_later:+.0f}); "
              f"{r.partner} {r.partner_gain:+d} (this season {r.partner_now:+.0f}, later {r.partner_later:+.0f}).",
              f"- Your 1st-place odds {r.your_1st}, title {r.your_title}. {r.partner} trades {r.partner_trades_per_season:.1f} times a season.",
              f"- {r.opening}", f"- {r.target}", f"- {r.walk_away}",
              f"- Salary rule: {r.salary_rule}." + (f" Forced drops: {r.forced_drops}." if r.forced_drops else ""),
              f"- Draft (not sent): \"{r.draft_message}\"", ""]
    if not len(good):
        L.append("No deal found that is worth more to both teams together than it costs.")
    return "\n".join(L)


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 120)
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--sims", type=int, default=3000)
    a = ap.parse_args()
    out = run(a.top, a.sims)
    print(out[["partner", "you_get", "you_give", "jordan", "partner_gain", "joint", "your_1st", "salary_rule"]].to_string(index=False))
