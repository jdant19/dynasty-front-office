"""Season $, Lifetime $ and Market $ for every rostered player and every ranked free agent.

    PYTHONPATH=src python -m dfo.valuation.prices      # writes data/processed/prices.csv

Units: league dollars at auction prices, fit to the 2020-2025 auctions (auction.fit_market). All
money rounds to whole dollars at the end.

Two replacement levels, by when a player would have to be replaced (principle P11):
  this season   the next startable player (first non-starter league-wide): waivers are thin with
                18-man rosters, so mid-tier starters are not freely available in season
  later seasons the level the auction prices imply: owners pay nothing for points up to it, since
                that production goes for $1-2 every August (QB +100, TE +40, WR +30, RB +25 over
                the structural level, last fit 2026-10-30)
Each has its own fitted price per point, both fit to the same auction prices.

Season $   rest of this season: expected points over replacement x price per point. The current-season
           salary is not subtracted: it is owed whether he is kept or cut (100% current-year dead
           cap), and in-season cap buys almost nothing.
Lifetime $ Season $ plus each later contract season through 2029 (value - salary, x 0.88 a year),
           keeping or cutting each offseason, whichever is better (cutting costs the dead cap in
           config/league.yaml), plus the best designation available when the contract ends
           (franchise tag, mid-level extension or RFA at market price).
Units (P15): points for what a player adds, cap dollars only for real cap money.
  points_now           points over in-season replacement for the rest of this season
  points_<year>        points over the market replacement level that season (proj_points = raw)
  auction_cost_<year>  cap $: what that production would cost at that August's auction
  cap_saved_<year>     cap $: auction cost minus his salary, seasons under contract
  cap_saved_2027_29    cap $: the contract's whole later value, x0.88 a year, keep/cut each offseason,
                       plus the best designation when it ends
This season and later seasons are never added together; the dial weights them (P14).

Market $   what this league would pay: what his next season would fetch at the auction
           (1 + price per point x expected points over the market replacement level).
FC reference  FantasyCalc's order (10-team, 1QB, half PPR) within his position, on our dollar
           scale, minus the cost of holding him through 2029. An outside view only: it is not this
           league's price and carries no odds that anyone here would pay it (owner models, Phase 6).
Projections enter after gap calibration (calibrate.py): rest of season and future seasons are
pulled toward their position's startable mean by the measured factor.
"""
from __future__ import annotations

import datetime as dt
import json

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression

from dfo.common import PROCESSED, ROOT, load_config
from dfo.rules.contracts import cut_dead_cap, mle_eligible, money
from dfo.valuation import calibrate
from dfo.valuation.auction import contract_value, haircut, market_params
from dfo.valuation.expected import STREAMED, ExpectedValue

SEASON = 2026
FUTURE = [2027, 2028, 2029]          # horizon: this season and the next three (settings objective)
SNAP = ROOT / "data" / "snapshots"


def trajectory(fut: pd.DataFrame) -> pd.DataFrame:
    """Points for 2028-2029: next season's points per game carried forward by the position's aging
    curve (aging.py), times the expected games the projections give for that season. Next season is
    the best-tested horizon; fit separately, the two- and three-year projections jumped around for
    single players (a quarter rose from 2028 to 2029, many past their peak)."""
    from dfo.valuation import aging
    f = fut.copy()
    skill = f.pos.isin(["QB", "RB", "WR", "TE"]) & f.age.notna()
    ppg = f[f"ppg_{FUTURE[0]}"].copy()
    age = f.age + 1                                  # age during next season (ages are as of now)
    for y in FUTURE[1:]:
        ppg = (ppg + aging.delta(f.pos, age)).clip(lower=0)
        age = age + 1
        f.loc[skill, f"ppg_{y}"] = ppg[skill]
        f.loc[skill, f"points_{y}"] = (ppg * f[f"games_{y}"])[skill]
    return f


def rfa_price_per_point() -> float:
    return float(pd.read_csv(PROCESSED / "val_rfa_overall.csv").rfa_price_per_point.iloc[0])


def _salaries(row) -> list[int]:
    vals = [int(v) for v in str(row.salaries).split()] if isinstance(row.salaries, str) else []
    start = int(row.salary_start_year) if pd.notna(row.salary_start_year) else SEASON
    by_year = {start + i: v for i, v in enumerate(vals)}
    return [by_year.get(y, 0) for y in [SEASON] + FUTURE]


def tag_costs() -> dict[int, dict[str, int]]:
    t = json.load(open(SNAP / "site" / "latest" / "ft_price_table.json"))["rows"]
    out: dict[int, dict[str, int]] = {}
    for r in t:
        out.setdefault(int(r["season_start_year"]), {})[r["position"]] = int(r["price"])
    last = max(out)
    for y in FUTURE:
        out.setdefault(y, out[last])
    return out


def fantasycalc() -> pd.DataFrame:
    d = json.load(open(SNAP / "market" / "fantasycalc" / "latest.json"))
    rows = [dict(sleeper_id=str(x["player"].get("sleeperId")), fc_value=x["value"], fc_pos=x["player"]["position"])
            for x in d["data"]]
    fc = pd.DataFrame(rows)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["sleeper_id", "canonical_id"]].dropna()
    fc = fc.merge(ids, on="sleeper_id", how="left").rename(columns={"canonical_id": "player_id"})
    fc.attrs["as_of"] = d.get("as_of")
    return fc[fc.player_id.notna()].drop_duplicates("player_id")


def build() -> pd.DataFrame:
    shift, lam, lam_struct = market_params()
    lam_rfa = rfa_price_per_point()
    h = haircut()
    ev_now = ExpectedValue("auction")                    # structural replacement: this season
    ev = ExpectedValue("auction", shift=shift)           # market replacement: later seasons
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    fut = pd.read_csv(PROCESSED / "proj_future.csv")
    # next season's points per game, corrected for the projections' measured age bias
    c = calibrate.age_correction(fut.pos, fut.age + 1)
    fut[f"ppg_{FUTURE[0]}"] = (fut[f"ppg_{FUTURE[0]}"] + c).clip(lower=0)
    fut[f"points_{FUTURE[0]}"] = fut[f"ppg_{FUTURE[0]}"] * fut[f"games_{FUTURE[0]}"]
    fut = trajectory(fut)
    roster = pd.read_csv(PROCESSED / "site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)
    roster = roster.merge(ids[["site_player_id", "canonical_id"]].rename(columns={"site_player_id": "player_id",
                                                                                   "canonical_id": "pid"}),
                          on="player_id", how="left")
    roster = roster[roster.pid.notna()].drop_duplicates("pid")

    base = ros[["player_id", "player", "pos", "team", "owner", "ros_ppg", "games_left", "availability"]].copy()
    extra = roster[~roster.pid.isin(base.player_id)][["pid", "player", "pos", "nfl_team", "owner"]].rename(
        columns={"pid": "player_id", "nfl_team": "team"})
    base = pd.concat([base, extra], ignore_index=True)
    base["owner"] = base.player_id.map(roster.set_index("pid").owner).fillna("")

    # ---- this season, rest of season
    skill = base.pos.isin(["QB", "RB", "WR", "TE"])
    ppg = calibrate.shrink(base.ros_ppg, base.pos, "ros")
    season_equiv = ppg * 17 * base.availability.fillna(0.88)          # full-season points at this rate
    share_left = (base.games_left.fillna(0) / 17).clip(0, 1)
    base["por_2026"] = ev_now.por_from_points(base.pos, season_equiv.fillna(0), 0, share_left)
    base["value_2026"] = lam_struct * base.por_2026

    # ---- next three seasons
    f = fut.set_index("player_id")
    for k, y in enumerate(FUTURE, start=1):
        pts = base.player_id.map(f[f"points_{y}"]) if f"points_{y}" in f else pd.Series(np.nan, index=base.index)
        m = skill & pts.notna()
        pts[m] = calibrate.shrink(pts[m], base.pos[m], f"+{k}")
        base[f"proj_points_{y}"] = pts
        base[f"por_{y}"] = ev.por_from_points(base.pos, pts.fillna(0), k)
        base[f"value_{y}"] = lam * base[f"por_{y}"]

    # ---- contracts
    r = roster.set_index("pid")
    sal = base.player_id.map(lambda p: _salaries(r.loc[p]) if p in r.index else [0, 0, 0, 0])
    for i, y in enumerate([SEASON] + FUTURE):
        base[f"salary_{y}"] = sal.map(lambda v: v[i])
    base["acquisition"] = base.player_id.map(r.acquisition)
    base["rights"] = base.player_id.map(r.designation_rights).fillna("")
    tags = tag_costs()

    ext_sal = load_config("league")["designations"]["mid_level_extension"]["salaries"]
    lifetime, rights_val, keep_years, hold_cost = [], [], [], []
    for _, x in base.iterrows():
        vals = [float(x[f"value_{y}"]) for y in FUTURE]
        sals = [int(x[f"salary_{y}"]) for y in FUTURE]
        end = next((i for i, v in enumerate(sals) if v == 0), len(sals))   # index of first season off contract
        # value of the best designation when the contract ends (if this owner holds the rights)
        rv = 0.0
        if x.owner and end < len(FUTURE) and x.rights:
            y, v_end, left = FUTURE[end], vals[end], len(FUTURE) - end
            options = [0.0]
            if "Franchise Tag" in x.rights:
                options.append(v_end - tags[y].get(x.pos, 2))
            last_sal = sals[end - 1] if end > 0 else int(x[f"salary_{SEASON}"])
            if "Mid-Level Extension" in x.rights and mle_eligible(max(last_sal, 1), x.acquisition == "rookie_draft"):
                options.append(sum(h ** j * (vals[end + j] - ext_sal[j]) for j in range(min(3, left))))
            if "Restricted Free Agent" in x.rights:
                price = max(1, money(1 + lam_rfa * x[f"por_{y}"]))          # matching the going RFA price
                later = {j: np.array([vals[end + j]]) for j in range(1, left)}
                _, fut_surplus = contract_value(pd.Series([price]), later, h)
                options.append(v_end - price + float(fut_surplus[0]))
            rv = h ** (end + 1) * max(options)
        # keep or cut each offseason under contract (backward): cutting owes the dead cap on what remains
        cont = rv
        for i in reversed(range(end)):
            keep = h ** (i + 1) * (vals[i] - sals[i]) + cont
            dead = cut_dead_cap(sals[i:end])
            cut = -sum(h ** (i + 1 + j) * d for j, d in enumerate(dead))
            cont = max(keep, cut)
        lifetime.append(x.value_2026 + cont)
        rights_val.append(rv)
        keep_years.append(end)
        # cost to hold him through the horizon: his salaries, then the going auction price
        hold_cost.append(sum(h ** (i + 1) * (sals[i] if i < end else 1 + lam * x[f"por_{FUTURE[i]}"])
                             for i in range(len(FUTURE))))
    base["rights_value"] = rights_val
    base["contract_years_left"] = keep_years
    base["lifetime"] = lifetime
    base["hold_cost"] = hold_cost

    # ---- market
    nxt = FUTURE[0]
    base["market_auction"] = 1 + lam * base[f"por_{nxt}"]
    fc = fantasycalc()
    base = base.merge(fc[["player_id", "fc_value"]], on="player_id", how="left")
    free_value = base.value_2026 + sum(h ** k * base[f"value_{y}"] for k, y in enumerate(FUTURE, start=1))
    # outside reference only: FantasyCalc's order within his position, on our dollar scale, minus the
    # cost of holding him. Not this league's price, and says nothing about whether anyone would buy.
    base["fc_reference"] = np.nan
    for pos, g in base[base.fc_value.notna()].groupby("pos"):
        if len(g) < 8:
            continue
        iso = IsotonicRegression(increasing=True, out_of_bounds="clip").fit(g.fc_value, free_value[g.index])
        base.loc[g.index, "fc_reference"] = iso.predict(g.fc_value) - base.hold_cost[g.index]
    base["future_dollars"] = base.lifetime - base.value_2026

    # ---- output: points for this season; points and cap for later seasons (P14, P15)
    base["points_now"] = base.por_2026.round(1)                      # rest of 2026, over in-season replacement
    for y in FUTURE:
        base[f"points_{y}"] = base[f"por_{y}"].round(1)              # over the market replacement level
        base[f"proj_points_{y}"] = base[f"proj_points_{y}"].round(1)
        base[f"auction_cost_{y}"] = base[f"value_{y}"]                # what that production costs in August
        base[f"cap_saved_{y}"] = np.where(base[f"salary_{y}"] > 0, base[f"value_{y}"] - base[f"salary_{y}"], np.nan)
    base["cap_saved_2027_29"] = base.future_dollars
    for c in ["value_2026", "cap_saved_2027_29", "rights_value", "market_auction", "fc_reference"] + \
             [f"auction_cost_{y}" for y in FUTURE] + [f"cap_saved_{y}" for y in FUTURE]:
        base[c] = base[c].map(lambda v: money(v) if pd.notna(v) else v)
    # internal only: this season converted at the in-season auction rate, for the FantasyCalc reference map
    base = base.rename(columns={"value_2026": "_season_auction_equiv"})
    stamp = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    base["fantasycalc_as_of"] = fc.attrs.get("as_of")
    base["made_at"] = stamp
    cols = (["player_id", "player", "pos", "team", "owner", "points_now", "cap_saved_2027_29"]
            + [c for y in FUTURE for c in (f"proj_points_{y}", f"points_{y}", f"auction_cost_{y}", f"salary_{y}",
                                           f"cap_saved_{y}")]
            + ["salary_2026", "contract_years_left", "rights", "rights_value", "market_auction", "fc_reference",
               "fc_value", "_season_auction_equiv", "fantasycalc_as_of", "made_at"])
    out = base[cols].sort_values(["points_now", "cap_saved_2027_29"], ascending=False)
    out.to_csv(PROCESSED / "prices.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    p = build()
    print(p[p.owner == "Jordan"].drop(columns=["fantasycalc_as_of", "made_at", "rights"]).to_string(index=False))
