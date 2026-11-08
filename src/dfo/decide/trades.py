"""Trade evaluator: what a trade is worth to each side, in expected payout dollars.

    PYTHONPATH=src python -m dfo.decide.trades --review     # 2026 site trades at the time they were made

Question (P16, design section 8): does the deal, with the moves it forces, raise this team's expected
payout over the best plan that skips it?
- This season: change in the team's best-lineup points over the remaining regular-season weeks (lineup
  re-picked each week, byes, waiver pickups always available), times the team's dial weight now.
  If the team ends up over the roster limit it drops whoever costs it least (this season's lineup loss
  plus later surplus plus later dead cap); if under, the open spot is a waiver pickup.
- Later seasons (dial's 2027 weight): each player's surplus 2027-29 (prices.csv: lineup points over
  waiver minus what his salary normally buys, keep/cut chosen each season), dead cap left by forced
  drops, picks at their expected slot, and next season's cap at what cap buys (PriceCurve slope).
  This season's cap is worth close to nothing (CLAUDE.md) and is shown but not valued.
- Legal: the 25% salary rule (rules/contracts.py), the roster limit, the Week 14 deadline.
The same function scores both sides, each with its own dial, so a trade can be worth it to both.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.decide import waivers as W
from dfo.rules.contracts import TradeSide, trade_salaries_legal
from dfo.valuation.ours import _waiver_rows, lineup_points

SEASON = 2026
DEADLINE_WEEK = 14
HAIRCUT = 0.88


@dataclass
class Assets:
    players: list[str] = field(default_factory=list)                  # canonical ids
    picks: list[tuple] = field(default_factory=list)   # (year, round, original owner[, overall pick if known])
    cap: dict[int, int] = field(default_factory=dict)                 # season -> $ sent


@dataclass
class Trade:
    a: str
    b: str
    a_gives: Assets
    b_gives: Assets
    label: str = ""


def roster_max() -> int:
    r = load_config("league")["roster"]
    return sum(r["starters"].values()) + r["bench"] + r["ir"]


class State:
    """Everything the evaluator reads, at one moment."""

    def __init__(self, ros: pd.DataFrame, rosters: dict[str, set[str]], week0: int,
                 first: pd.Series | None = None, prices: pd.DataFrame | None = None,
                 salary: pd.Series | None = None, made_at: str = ""):
        from dfo.decide.streaming import adjustments
        from dfo.owners.grades import PriceCurve, _load
        from dfo.projections.features import implied_totals
        from dfo.valuation import calibrate
        self.week0, self.rosters, self.made_at = week0, rosters, made_at
        self.prices = prices if prices is not None else pd.read_csv(PROCESSED / "prices.csv")
        ros = ros.drop_duplicates("player_id").copy()
        ros["rate"] = calibrate.shrink(ros.ros_ppg, ros.pos, "ros") * ros.availability
        self.pool = ros.set_index("player_id")
        sched = implied_totals([SEASON])
        self.weeks = list(range(week0, 19))
        plays = {w: set(sched[sched.week == w].team) for w in self.weeks}
        self.grid = W._grid(ros, self.weeks, plays, first, adjustments(SEASON, week0)) if self.weeks else None
        self.waiver = _waiver_rows(self.weeks)
        self.dial = pd.read_csv(PROCESSED / "dial.csv").set_index("owner")
        option, self.drop_later = W._later_values(self.prices)
        p = self.prices.set_index("player_id")
        self.surplus = p.surplus_2027_29.fillna(0.0)
        self.salary = salary if salary is not None else p.salary_2026.fillna(0)
        self.names = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).drop_duplicates("canonical_id") \
            .set_index("canonical_id").name
        self.slope = PriceCurve(_load()[1]).slope
        self.slots = pd.read_csv(PROCESSED / "val_picks_slots.csv")
        sim = pd.read_csv(PROCESSED / "sim_2026.csv").set_index("owner")
        self.slot_mean = sim.rookie_slot_mean

    # ---------------------------------------------------------------- pieces
    def weights(self, owner: str) -> tuple[float, float]:
        return float(self.dial.per_point_2026.get(owner, 1.0)), float(self.dial.per_point_2027_haircut.get(owner, 1.0))

    def lineup(self, roster: set[str]) -> float:
        if self.grid is None:
            return 0.0
        g = self.grid.loc[self.grid.index.intersection(list(roster))]
        return lineup_points(pd.concat([g, self.waiver]))

    def pick_points(self, year: int, rnd: int, original: str, overall: int | None = None) -> float:
        """Lineup points over the rookie deal, at the slot when known, else the expected slot (2027: from
        the simulated finish of the pick's original team; later years: the round average), x0.88 a
        year after 2027."""
        if overall:
            v = float(np.interp(overall, self.slots.overall, self.slots.use_lineup))
        elif year == SEASON + 1 and original in self.slot_mean.index:
            ov = (rnd - 1) * 10 + float(self.slot_mean[original])
            v = float(np.interp(ov, self.slots.overall, self.slots.use_lineup))
        else:
            v = float(self.slots[self.slots["round"] == rnd].use_lineup.mean())
        return v * HAIRCUT ** max(0, year - (SEASON + 1))

    def cap_points(self, cap: dict[int, int]) -> float:
        return sum(amt * self.slope * HAIRCUT ** (yr - (SEASON + 1)) for yr, amt in cap.items() if yr > SEASON)

    # ---------------------------------------------------------------- one side
    def side(self, owner: str, gets: Assets, gives: Assets) -> dict:
        w_now, w_later = self.weights(owner)
        r0 = set(self.rosters[owner])
        r1 = (r0 - set(gives.players)) | set(gets.players)
        base = self.lineup(r0)
        drops = []
        while len(r1) > roster_max():
            cost = {}
            for pid in r1:
                loss = self.lineup(r1) - self.lineup(r1 - {pid})
                cost[pid] = w_now * loss + w_later * float(self.drop_later.get(pid, 0.0))
            d = min(cost, key=cost.get)
            drops.append(d)
            r1 = r1 - {d}
        now = self.lineup(r1) - base
        later = (sum(float(self.surplus.get(p, 0.0)) for p in gets.players)
                 - sum(float(self.surplus.get(p, 0.0)) for p in gives.players)
                 - sum(float(self.drop_later.get(p, 0.0)) for p in drops))
        picks = sum(self.pick_points(*k) for k in gets.picks) - sum(self.pick_points(*k) for k in gives.picks)
        cap = self.cap_points(gets.cap) - self.cap_points(gives.cap)
        return dict(owner=owner, lineup_now=round(now, 1), later_players=round(later, 1), later_picks=round(picks, 1),
                    later_cap=round(cap, 1), payout_now=round(w_now * now, 2),
                    payout_later=round(w_later * (later + picks + cap), 2),
                    payout=round(w_now * now + w_later * (later + picks + cap), 2),
                    forced_drops=", ".join(str(self.names.get(d, d)) for d in drops),
                    cap_this_season=gets.cap.get(SEASON, 0) - gives.cap.get(SEASON, 0))

    def legal(self, t: Trade) -> tuple[bool, str]:
        sa = [int(round(float(self.salary.get(p, 0)))) for p in t.a_gives.players]
        sb = [int(round(float(self.salary.get(p, 0)))) for p in t.b_gives.players]
        ok, why = trade_salaries_legal(TradeSide(sa, t.a_gives.cap.get(SEASON, 0)),
                                       TradeSide(sb, t.b_gives.cap.get(SEASON, 0)))
        if self.week0 > DEADLINE_WEEK + 1:
            return False, "after the Week 14 deadline"
        return ok, why

    def evaluate(self, t: Trade) -> pd.DataFrame:
        ok, why = self.legal(t)
        rows = [self.side(t.a, t.b_gives, t.a_gives), self.side(t.b, t.a_gives, t.b_gives)]
        out = pd.DataFrame(rows)
        out["gets"] = [self.describe(t.b_gives), self.describe(t.a_gives)]
        out["legal"], out["salary_rule"], out["trade"] = ok, why, t.label
        return out

    def describe(self, a: Assets) -> str:
        parts = [str(self.names.get(p, p)) for p in a.players]
        parts += [f"{k[0]} round {k[1]} ({k[2]})" + (f" #{k[3]}" if len(k) > 3 and k[3] else "") for k in a.picks]
        parts += [f"${v} cap {y}" for y, v in a.cap.items() if v]
        return "; ".join(parts) or "nothing"


# ---------------------------------------------------------------- states

def live() -> State:
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    r = pd.read_csv(PROCESSED / "site_rosters.csv").merge(ids, left_on="player_id", right_on="site_player_id", how="left")
    rosters = {o: set(g.canonical_id.dropna()) for o, g in r.groupby("owner")}
    sal = r.dropna(subset=["canonical_id"]).drop_duplicates("canonical_id").set_index("canonical_id").salary_now
    wk = pd.read_csv(PROCESSED / "proj_week.csv")
    week0 = int(ros.from_week.iloc[0])
    first = wk.groupby("player_id").proj.first() if int(wk.week.iloc[0]) == week0 else None
    return State(ros, rosters, week0, first, salary=sal, made_at=str(ros.made_at.iloc[0]))


def at(when: pd.Timestamp, week: int, df: pd.DataFrame | None = None) -> State:
    """The state just before `when`: rosters with later moves undone, rest-of-season rates and the
    coming week's projection from data before that week. Prices and the dial are today's (labeled)."""
    from dfo.decide.lineup import project_past
    from dfo.projections.features import build
    df = build() if df is None else df
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    s2c = dict(zip(ids.site_player_id, ids.canonical_id))
    before = W.rosters_at(when)
    before["pid"] = before.player_id.map(s2c)
    rosters = {o: set(g.pid.dropna()) for o, g in before.groupby("owner")}
    ros = W.ros_past(week, df)
    cand = ros[["player_id", "pos", "team"]].drop_duplicates("player_id").assign(week=week)
    first = project_past([week], cand).groupby("player_id").proj.first()
    return State(ros, rosters, week, first, made_at=f"as of {when.date()} (week {week} projections)")


# ---------------------------------------------------------------- 2026 site trades

def _owner_of_team(name: str) -> str | None:
    cfg = load_config("owners")
    for o, v in cfg["owners"].items():
        if v.get("current_team") and v["current_team"].lower() in name.lower():
            return o
    return None


def site_trades() -> list[tuple[pd.Timestamp, Trade]]:
    """The executed 2026 trades on the site (data/snapshots/site/latest/trades.json): players by
    canonical id (resolved in hist_trade_assets), picks with their slot when the site shows it, cap by
    season."""
    import json
    from dfo.common import ROOT
    snap = json.loads((ROOT / "data/snapshots/site/latest/trades.json").read_text())
    team = {v["site_team_id"]: o for o, v in load_config("owners")["owners"].items() if v.get("site_team_id")}
    a = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    a = a[a.trade_id.str.startswith("S2026") & (a.kind == "player")]
    pid = dict(zip(a.asset.str.lower(), a.player_id))
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    by_name = ids.drop_duplicates("name").set_index(ids.drop_duplicates("name").name.str.lower()).canonical_id
    out = []
    rows = sorted([r for r in snap["rows"] if r["status"] == "executed"], key=lambda r: r["executed_at"])
    for k, r in enumerate(rows, 1):
        teams = [team[t] for t in r["trade_data"]["teamIds"]]
        gives = {o: Assets() for o in teams}
        for x in r["trade_data"]["assets"]:
            s = gives[team[x["fromTeamId"]]]
            if x["type"] == "player":
                nm = x["player"]["name"].lower()
                c = pid.get(nm) or by_name.get(nm) or next((v for n, v in pid.items() if n.startswith(nm)), None)
                s.players.append(str(c) if c else x["player"]["name"])
            elif x["type"] == "pick":
                pk = x["pick"] if isinstance(x.get("pick"), dict) else x
                m = re.search(r"#(\d+)", str(pk.get("pick", "")))
                s.picks.append((int(pk["year"]), int(pk["round"]), team[x["fromTeamId"]], int(m.group(1)) if m else None))
            elif x["type"] == "cap_space":
                yr = int(str(x.get("season", f"{SEASON}/")).split("/")[0])
                s.cap[yr] = s.cap.get(yr, 0) + int(x["capAmount"])
        out.append((pd.Timestamp(r["executed_at"]), Trade(teams[0], teams[1], gives[teams[0]], gives[teams[1]],
                                                          label=f"S2026-{k:02d}")))
    return out


def review() -> pd.DataFrame:
    from dfo.projections.current import next_week
    from dfo.projections.features import build
    df = build()
    rows = []
    for when, t in site_trades():
        wk = next_week(when.date())
        st = at(when, wk, df)
        e = st.evaluate(t)
        e.insert(0, "date", when.date())
        e.insert(1, "week", wk)
        rows.append(e)
    out = pd.concat(rows, ignore_index=True)
    out.to_csv(PROCESSED / "decide_trades_review_2026.csv", index=False)
    return out


if __name__ == "__main__":
    import argparse
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 90)
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    a = ap.parse_args()
    if a.review:
        print(review().to_string(index=False))
