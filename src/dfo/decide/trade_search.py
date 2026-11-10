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
Versions: the gap between the two sides is closed with Jordan's draft picks, the currency this league
trades in (92% of the 52 trades on record 2014-2024 included a pick; cap moved in 54%; Jordan's
partners got picks in 76% of his trades). Every set of up to two of his picks is tried, and each
version is rebuilt through both teams' three-year outlook. Opening: the smallest add that leaves the
partner ahead by $5. Target: the joint gain split most evenly. Walk-away: the most Jordan can add and
still come out ahead. No cap is offered: Jordan's 2027 plan is already over the cap.
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
MAX_TARGETS = 5


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
                    if joint <= -25:              # the outlook re-score can move it; keep near misses
                        continue
                    out.append(dict(partner=partner, trade=trade, jordan=j["total"], partner_gain=q["total"],
                                    j_later=j["later"], p_later=q["later"], joint=joint, salary_rule=why))
        # best packages per target on the quick score, then re-scored through the three-year outlook
        by_t = {}
        for c in sorted(out, key=lambda c: -c["joint"]):
            by_t.setdefault(c["trade"].b_gives.players[0], [])
            if len(by_t[c["trade"].b_gives.players[0]]) < 4:
                by_t[c["trade"].b_gives.players[0]].append(c)
        best = []
        for t, cs in by_t.items():
            for c in cs:
                self.rescore(c)
            best.append(max(cs, key=lambda c: c["joint"]))
        return sorted(best, key=lambda c: -c["joint"])

    def rescore(self, c: dict) -> dict:
        t = c["trade"]
        lat = self.st.later(t)
        j_now = c["jordan"] - c["j_later"]
        p_now = c["partner_gain"] - c["p_later"]
        c["j_later"], c["p_later"] = lat[ME]["later_points"] * self.later, lat[t.b]["later_points"] * self.later
        c["jordan"], c["partner_gain"] = j_now + c["j_later"], p_now + c["p_later"]
        c["joint"] = c["jordan"] + c["partner_gain"]
        return c

    def versions(self, t: Trade, j_now: float, p_now: float) -> dict:
        """Opening, target and walk-away as sets of up to two of Jordan's picks added to the players."""
        picks = picks_held(ME)
        kinds = sorted(set(picks), key=lambda k: (-k[1], -k[0]))          # late, far picks first
        sets = [()] + [(k,) for k in kinds] + [c for c in itertools.combinations(picks, 2)]
        seen, rows = set(), []
        for ks in sets:
            key = tuple(sorted((k[0], k[1]) for k in ks))
            if key in seen:
                continue
            seen.add(key)
            tt = Trade(t.a, t.b, Assets(players=list(t.a_gives.players), picks=list(ks)), t.b_gives, t.label)
            lat = self.st.later(tt)
            rows.append(dict(picks=ks, trade=tt, lat=lat, jordan=j_now + lat[ME]["later_points"] * self.later,
                             partner=p_now + lat[t.b]["later_points"] * self.later))
        ok = [r for r in rows if r["jordan"] >= 0] or [max(rows, key=lambda r: r["jordan"])]
        opening = min([r for r in ok if r["partner"] >= OPENING_GAIN] or [max(ok, key=lambda r: r["partner"])],
                      key=lambda r: r["partner"])
        target = min(ok, key=lambda r: abs(r["jordan"] - r["partner"]))
        walk = max(ok, key=lambda r: r["partner"])
        return dict(opening=opening, target=target, walk_away=walk)


def _pick_text(ks) -> str:
    return " + ".join(f"{k[0]} round {k[1]}" + (f" ({k[2]}'s)" if k[2] != ME else "") for k in ks) or "no pick"


def describe_versions(st, t: Trade, v: dict) -> list[str]:
    return [f"{name.replace('_', '-')}: add {_pick_text(v[name]['picks'])} -> you {v[name]['jordan']:+.0f}, him {v[name]['partner']:+.0f}"
            for name in ("opening", "target", "walk_away")]


def message(st, t: Trade, v: dict) -> str:
    ks = v["opening"]["picks"]
    extra = f" and my {_pick_text(ks)}" if ks else ""
    return f"{t.b}, would you do {st.describe(t.b_gives)} for {st.describe(Assets(players=t.a_gives.players))}{extra}? Open to tweaking it."


def run(top: int = 10, sims: int = 3000, targets: tuple[str, ...] = (), partners_only: tuple[str, ...] = (),
        save: bool = True) -> pd.DataFrame:
    """`targets`: player names Jordan asked about; their best package is always evaluated."""
    s = Search()
    st = s.st
    partners = [o for o in st.rosters if o != ME and (not partners_only or o in partners_only)]
    cands, asked = [], []
    for p in partners:
        cs = s.candidates(p)
        asked += [c for c in cs if str(st.names.get(c["trade"].b_gives.players[0])) in targets]
        cands += [c for c in cs if c["joint"] > 0][:3]
    cands = sorted(cands, key=lambda c: -c["joint"])[:top]
    cands += [c for c in asked if c not in cands]
    st.drop_order = {}                                # full pass: exact forced drops
    base = st.season_odds(None, sims)
    rows = []
    for c in cands:
        t = c["trade"]
        e0 = st.evaluate(t, n=sims, base=base).set_index("owner")          # players only: this season's part
        v = s.versions(t, float(e0.payout_now[ME]), float(e0.payout_now[t.b]))
        tg = v["target"]
        lat = tg["lat"]
        jb, ja = lat[ME]["before"].set_index("season"), lat[ME]["after"].set_index("season")
        outlook = "; ".join(f"{y}: room ${int(jb.cap_room[y])} -> ${int(ja.cap_room[y])}, strength {lat[ME]['delta'][y]:+.0f}"
                            for y in (2027, 2028, 2029)) + f"; picks given {lat[ME]['pick_points']:+.0f} points over their rookie deals"
        was = {r.season: set(r.released.split("; ")) - {""} for r in lat[ME]["before"].itertuples()}
        fit = " | ".join(f"{r.season}: " + "; ".join(sorted(set(r.released.split("; ")) - {""} - was[r.season]))
                         for r in lat[ME]["after"].itertuples() if set(r.released.split("; ")) - {""} - was[r.season])
        j, p = tg["jordan"], tg["partner"]
        rows.append(dict(
            partner=t.b, you_get=st.describe(t.b_gives), you_give=st.describe(tg["trade"].a_gives),
            jordan_now=e0.payout_now[ME], jordan_later=round(j - e0.payout_now[ME]), jordan=round(j),
            partner_now=e0.payout_now[t.b], partner_later=round(p - e0.payout_now[t.b]), partner_gain=round(p),
            joint=round(j) + round(p), your_1st=f"{e0.p_first_before[ME]:.0%} -> {e0.p_first_after[ME]:.0%}",
            your_title=f"{e0.p_playoff_champ_before[ME]:.0%} -> {e0.p_playoff_champ_after[ME]:.0%}",
            your_outlook=outlook, your_plan_gives_up=fit,
            partner_trades_per_season=float(s.trades_per_season.get(t.b, 0.0)),
            forced_drops="; ".join(x for x in e0.forced_drops if x),
            salary_rule=c["salary_rule"],
            opening=describe_versions(st, t, v)[0], target=describe_versions(st, t, v)[1],
            walk_away=describe_versions(st, t, v)[2], draft_message=message(st, t, v),
            status="idea", inputs=st.made_at))
    out = pd.DataFrame(rows).sort_values("jordan", ascending=False)
    # a team still chasing the top four (simulator 40%+, or Jordan's dated read that it sees itself
    # contending) does not sell this season for later value: it must not lose this season either
    sim = pd.read_csv(PROCESSED / "sim_2026.csv").set_index("owner").p_top4
    obs = pd.read_csv(PROCESSED / "observations.csv")
    obs = obs[obs.note.str.contains("contending", case=False) & (pd.to_datetime(obs.expires) >= pd.Timestamp.now())]
    contending = set(sim[sim >= 0.4].index) | set(obs.owner)
    out["partner_contending"] = out.partner.isin(contending)
    out["works_for_both"] = (out.jordan > 0) & (out.partner_gain > 0) & (~out.partner_contending | (out.partner_now >= 0))
    if save:
        out.to_csv(PROCESSED / "decide_trade_search.csv", index=False)
        (PROCESSED / "decide_trade_search.md").write_text(to_markdown(out, st))
    return out


def to_markdown(d: pd.DataFrame, st) -> str:
    L = [f"# Trade ideas for Jordan (from Week {st.week0} projections)", "",
         f"Inputs: projections {st.made_at}; rosters and cap from the site. Ideas only: nothing is proposed until "
         "Jordan says so. Values in expected payout $ (P18); 'later' at $0.53 a point.", ""]
    good = d[d.works_for_both]
    L += ["Asked about or searched, but no version leaves both sides ahead (or the partner is still contending "
          "and would lose this season): "
          + ("; ".join(f"{r.you_get} ({r.partner}: you {r.jordan:+d}, him {r.partner_gain:+d}"
                       + (", contending" if r.partner_contending else "") + ")" for r in d[~d.works_for_both].itertuples())
             or "none") + ".", ""]
    for r in good.itertuples():
        L += [f"## {r.partner}: you get {r.you_get}", "",
              f"- You give {r.you_give} (target version). You {r.jordan:+d} (this season {r.jordan_now:+.0f}, later {r.jordan_later:+.0f}); "
              f"{r.partner} {r.partner_gain:+d} (this season {r.partner_now:+.0f}, later {r.partner_later:+.0f}).",
              f"- Your 1st-place odds {r.your_1st}, title {r.your_title}. {r.partner} trades {r.partner_trades_per_season:.1f} times a season.",
              f"- Your next three seasons: {r.your_outlook}." + (f" To fit the cap you give up: {r.your_plan_gives_up}." if isinstance(r.your_plan_gives_up, str) and r.your_plan_gives_up else ""),
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
    ap.add_argument("--targets", nargs="*", default=[])
    a = ap.parse_args()
    out = run(a.top, a.sims, tuple(a.targets))
    print(out[["partner", "you_get", "you_give", "jordan", "partner_gain", "joint", "your_1st", "salary_rule"]].to_string(index=False))
