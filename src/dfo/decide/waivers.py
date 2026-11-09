"""Waivers: which free agent to add, whom to drop, and what to bid.

    PYTHONPATH=src python -m dfo.decide.waivers              # this week, Jordan: decide_waivers_w<week>.md/.csv
    PYTHONPATH=src python -m dfo.decide.waivers --review     # exit test on Jordan's Weeks 7-9 waiver moves

Question (P16): does swapping this free agent in for this rostered player raise expected payout?
- This season: the change in the team's best-lineup points over the remaining regular-season weeks
  (the coming week from its own projection, so matchups and bye fills count; later weeks from each
  player's rest-of-season rate, byes from the schedule, a waiver pickup always available at
  each position's waiver level), at what a lineup point is worth to the team now (the dial).
- Later seasons: an in-season add signs for one year but can be designated (RFA) next offseason, so
  he carries an option: his expected 2027 lineup points over waiver minus what his likely RFA price
  normally buys, if positive, x0.88, at the dial's 2027 weight. Dropping a player loses his later
  surplus (if positive) and leaves dead cap in later seasons (75/50/25% of the remaining salary),
  priced at what that cap normally buys.
In-season cap does not carry over, so this season's dead cap and the FAAB dollar cost close to nothing.
Bids: every 2026 add on record went for $0 or $1 (one $2), and ties go by rolling reverse standings, so a
$1 bid on a contested player is close to free and beats any $0 claim; $0 for everyone else. Never more
than the team's cap room; at or over the cap, only $0 adds (rules/cap.py).
"""
from __future__ import annotations

import argparse

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.projections.features import implied_totals
from dfo.valuation import calibrate
from dfo.valuation.ours import _waiver_rows, lineup_points

SEASON = 2026
TOP_FA = 40          # free agents evaluated per run, by rest-of-season rate


def _grid(df: pd.DataFrame, weeks: list[int], plays: dict, first: pd.Series | None = None,
          adj: pd.DataFrame | None = None) -> pd.DataFrame:
    """Expected points by week: the rest-of-season rate, except the coming week, which uses that
    week's projection (matchup and bye) when there is one: streaming and bye fills live there.
    `adj` (decide/streaming.py): matchup adjustments for defenses in the tested weeks after that."""
    g = pd.DataFrame({w: np.where(df.team.isin(plays[w]), df.rate, 0.0) for w in weeks}, index=df.player_id)
    if adj is not None and len(adj):
        for (pos, w), a in adj.groupby(["pos", "week"]):
            if w in g.columns:
                m = (df.pos == pos).to_numpy() & (g[w].to_numpy() > 0)
                add = df.team.map(a.set_index("team").adj).fillna(0).to_numpy()
                g[w] = np.where(m, np.maximum(0.0, g[w].to_numpy() + add), g[w].to_numpy())
    if first is not None and len(weeks):
        wk = df.player_id.map(first)
        g[weeks[0]] = np.where(wk.notna(), wk, g[weeks[0]].to_numpy())
    g["pos"] = df.pos.to_numpy()
    return g


def _weights(owner: str) -> tuple[float, float]:
    d = pd.read_csv(PROCESSED / "dial.csv").set_index("owner")
    return float(d.per_point_2026.get(owner, 1.0)), float(d.per_point_2027_haircut.get(owner, 1.0))


def _later_values(prices: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    """(option value of adding him, cost of dropping him) in later-season lineup points."""
    from dfo.owners.grades import PriceCurve, _load
    from dfo.valuation.prices import rfa_price_per_point
    _, h = _load()
    curve = PriceCurve(h)
    ppp = rfa_price_per_point()
    p = prices.set_index("player_id")
    rfa_price = np.maximum(2, (p.pos.map(ppp).fillna(np.mean(list(ppp.values()))) * p.por_market_2027.fillna(0)).round())
    option = (0.88 * (p.lineup_2027.fillna(0) - curve(p.pos, rfa_price))).clip(lower=0)
    rules = load_config("league")["cuts"]["future_year_dead_cap"]
    frac = [rules["year2"], rules["year3"], rules["year4"]]
    dead = sum(0.88 ** (k + 1) * frac[k] * p.get(f"salary_{2027 + k}", pd.Series(0, index=p.index)).fillna(0)
               for k in range(3)) * curve.slope
    keep = p.surplus_2027_29.fillna(0).clip(lower=0)
    return option, keep + dead


def evaluate(owner: str, ros: pd.DataFrame, rostered: set[str], mine: set[str], prices: pd.DataFrame,
             week0: int, first: pd.Series | None = None) -> pd.DataFrame:
    """Every (add, drop) pair worth considering, best first."""
    ros = ros.copy()
    ros["rate"] = calibrate.shrink(ros.ros_ppg, ros.pos, "ros") * ros.availability
    sched = implied_totals([SEASON])
    weeks = list(range(week0, 19))
    plays = {w: set(sched[sched.week == w].team) for w in weeks}
    pool = ros.drop_duplicates("player_id").set_index("player_id")
    waiver = _waiver_rows(weeks)
    from dfo.decide.streaming import adjustments
    adj = adjustments(SEASON, week0)
    team = pd.concat([_grid(pool.loc[pool.index.intersection(mine)].reset_index(), weeks, plays, first, adj), waiver])
    base = lineup_points(team)
    w_now, w_later = _weights(owner)
    option, drop_later = _later_values(prices)
    loss = {pid: base - lineup_points(team.drop(index=pid)) for pid in team.index.difference(waiver.index)}
    fa = pool[~pool.index.isin(rostered) & (pool.rate > 0)].nlargest(TOP_FA, "rate")
    g_fa = _grid(fa.reset_index(), weeks, plays, first, adj)
    names = pool.player
    rows = []
    for add in g_fa.index:
        with_add = pd.concat([team, g_fa.loc[[add]]])
        for drop in loss:
            gain = lineup_points(with_add.drop(index=drop)) - base
            now = w_now * gain
            later = w_later * (float(option.get(add, 0.0)) - float(drop_later.get(drop, 0.0)))
            rows.append(dict(add=add, add_player=names.get(add, add), add_pos=pool.pos.get(add),
                             drop=drop, drop_player=names.get(drop, drop), drop_pos=pool.pos.get(drop),
                             lineup_points=round(gain, 1), payout_now=round(now, 2), payout_later=round(later, 2),
                             net_payout=round(now + later, 2)))
    out = pd.DataFrame(rows).sort_values("net_payout", ascending=False)
    # one line per free agent (his best drop), then keep moves that are worth making
    best = out.drop_duplicates("add")
    return best.reset_index(drop=True), out


def bid(best: pd.DataFrame, max_bid: int | None = None) -> pd.Series:
    """$1 when the move is worth $1+ of payout, else $0; never more than the team's room (at or over
    the cap only $0 adds are allowed: rules/cap.py)."""
    b = pd.Series(np.where(best.net_payout >= 1.0, 1, 0), index=best.index)
    return b.clip(upper=max_bid) if max_bid is not None else b


def plan_moves(owner: str, ros: pd.DataFrame, rostered: set[str], mine: set[str], prices: pd.DataFrame,
               week0: int, max_moves: int = 3, min_net: float = 0.25, first: pd.Series | None = None):
    """Moves one at a time: take the best (add, drop), update the roster, look again; stop when no
    move is worth min_net of expected payout. Returns (moves, the first look's full free-agent list)."""
    mine, rostered = set(mine), set(rostered)
    moves, look = [], None
    for _ in range(max_moves):
        best, _ = evaluate(owner, ros, rostered, mine, prices, week0, first)
        look = best if look is None else look
        if not len(best) or best.net_payout.iloc[0] < min_net:
            break
        m = best.iloc[0]
        moves.append(m)
        mine = (mine - {m["drop"]}) | {m["add"]}
        rostered = (rostered - {m["drop"]}) | {m["add"]}
    p = pd.DataFrame(moves)
    if len(p):
        from dfo.rules.cap import cap_room
        room = cap_room().set_index("owner").max_bid
        p["bid"] = bid(p, int(room.get(owner, 0)) if owner in room else None)
    return p, look


# ---------------------------------------------------------------- live
def live(owner: str = "Jordan"):
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    prices = pd.read_csv(PROCESSED / "prices.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    r = pd.read_csv(PROCESSED / "site_rosters.csv").merge(ids, left_on="player_id", right_on="site_player_id", how="left")
    rostered = set(r.canonical_id.dropna())
    mine = set(r[r.owner == owner].canonical_id.dropna())
    week0 = int(ros.from_week.iloc[0])
    wk = pd.read_csv(PROCESSED / "proj_week.csv")
    first = wk.groupby("player_id").proj.first() if int(wk.week.iloc[0]) == week0 else None
    moves, best = plan_moves(owner, ros, rostered, mine, prices, week0, first=first)
    return dict(week=week0, plan=moves, best=best, made_at=str(ros.made_at.iloc[0]))


def to_markdown(res: dict, owner: str = "Jordan") -> str:
    w_now, w_later = _weights(owner)
    L = [f"# Waivers, Week {res['week']} ({owner})", "",
         f"Rest-of-season projections made {res['made_at']}. A lineup point is worth ${w_now:.2f} of expected "
         f"payout to you this season and ${w_later:.2f} in 2027 (the dial).", ""]
    from dfo.rules.cap import cap_room
    cr = cap_room().set_index("owner")
    if owner in cr.index:
        c = cr.loc[owner]
        L += [f"Cap room: ${int(c.room)} (salary ${int(c.salary)}, dead money ${int(c.dead)}, traded cap "
              f"${int(c.traded_cap)}; site as of {c.as_of})."
              + (" At or over the cap, only $0 adds are allowed, so every claim below is $0 and goes by waiver priority."
                 if c.room <= 0 else ""), ""]
    p = res["plan"]
    if not len(p):
        L += ["No add beats your current roster by enough to be worth a move this week."]
    else:
        L += ["| Add | Drop | Lineup points rest of season | Payout now | Later seasons | Net | Bid |",
              "|---|---|---|---|---|---|---|"]
        for _, x in p.iterrows():
            L.append(f"| {x.add_player} ({x.add_pos}) | {x.drop_player} ({x.drop_pos}) | {x.lineup_points:+.1f} | "
                     f"${x.payout_now:+.2f} | ${x.payout_later:+.2f} | ${x.net_payout:+.2f} | ${int(x.bid)} |")
    L += ["", "Next best free agents (with the drop each would need):", "",
          "| Free agent | Drop | Lineup points | Net |", "|---|---|---|---|"]
    rest = res["best"][~res["best"]["add"].isin(p["add"] if len(p) else [])].head(8)
    for _, x in rest.iterrows():
        L.append(f"| {x.add_player} ({x.add_pos}) | {x.drop_player} | {x.lineup_points:+.1f} | ${x.net_payout:+.2f} |")
    return "\n".join(L)


# ---------------------------------------------------------------- exit test
def rosters_at(when: pd.Timestamp) -> pd.DataFrame:
    """Every team's roster (site player ids) just before `when`: today's rosters with every later
    add, drop and trade undone."""
    r = pd.read_csv(PROCESSED / "site_rosters.csv")[["owner", "player_id", "player", "pos"]]
    s = pd.read_csv(PROCESSED / "site_transactions.csv")
    s["t"] = pd.to_datetime(s.occurred_at, format="mixed", utc=True)
    later = s[(s.t >= when) & s.player_id.notna()].sort_values("t", ascending=False)
    held = {(o, p) for o, p in zip(r.owner, r.player_id)}
    names = dict(zip(r.player_id, r.player))
    for x in later.itertuples():
        if x.type == "fa_signing":
            held.discard((x.to_owner, x.player_id))
        elif x.type == "drop":
            held.add((x.from_owner, x.player_id))
        elif x.type == "trade":
            held.discard((x.to_owner, x.player_id))
            held.add((x.from_owner, x.player_id))
        names.setdefault(x.player_id, x.player)
    return pd.DataFrame([dict(owner=o, player_id=p, player=names.get(p)) for o, p in held])


def ros_past(week: int, df: pd.DataFrame) -> pd.DataFrame:
    """Rest-of-season table as current.py would have written it before `week` (same rate weights)."""
    from dfo.projections.current import ROS_WEIGHTS, availability
    from dfo.projections.weekly import RangeMap, WeeklyModel
    wk = df[(df.season == SEASON) & (df.week == week)].copy()
    train = df[(df.season < SEASON) | ((df.season == SEASON) & (df.week < week))]
    wk = wk.join(WeeklyModel(train).predict(wk, RangeMap.from_backtest())[["fp_pts"]])
    rate = sum(w * wk[c] for c, w in ROS_WEIGHTS.items())
    fallback = wk[["points_std", "xfp_std"]].mean(axis=1)
    wk["ros_ppg"] = np.where(wk[list(ROS_WEIGHTS)].notna().all(axis=1), rate,
                             np.where(wk.fp_pts.notna(), 0.5 * wk.fp_pts + 0.5 * fallback.fillna(wk.fp_pts), fallback))
    wk["ros_ppg"] = wk.ros_ppg.fillna(wk.points_std).fillna(0).clip(lower=0)
    wk["availability"] = wk.pos.map(availability(df, min(week, 17))).fillna(0.85)
    names = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").name
    wk["player"] = wk.player_id.map(names)
    return wk[["player_id", "player", "pos", "team", "ros_ppg", "availability"]]


def _actual_lineup(roster: set[str], pts: pd.DataFrame, weeks: list[int]) -> float:
    """Best-lineup points the roster actually scored over `weeks` (hindsight starts, the same rule for
    both rosters, so the difference is the moves, not lineup calls)."""
    p = pts[pts.player_id.isin(roster) & pts.week.isin(weeks)]
    if not len(p) or not weeks:
        return 0.0
    g = p.pivot_table(index="player_id", columns="week", values="points", aggfunc="sum").reindex(columns=weeks).fillna(0)
    g["pos"] = p.groupby("player_id").pos.first()
    return lineup_points(g)


def review(owner: str = "Jordan") -> pd.DataFrame:
    """For each waiver window Jordan used (Weeks 7-9), the module's best moves from what was known
    then, against his moves. Scored (P16) on lineup points: the roster after his moves vs the roster
    after the module's, best lineup from actual points, in the week the moves were for and through
    the last completed week holding the roster (later moves are left out of both)."""
    from dfo.projections.features import build
    s = pd.read_csv(PROCESSED / "site_transactions.csv")
    s["t"] = pd.to_datetime(s.occurred_at, format="mixed", utc=True)
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    site2c = dict(zip(ids.site_player_id, ids.canonical_id))
    prices = pd.read_csv(PROCESSED / "prices.csv")
    windows = {7: ("2026-10-19", "2026-10-23"), 8: ("2026-10-26", "2026-10-31"), 9: ("2026-11-02", "2026-11-06")}
    df = build()
    pts = pd.read_csv(PROCESSED / f"points_{SEASON}.csv")
    pts = pts[pts.season_type == "REG"]
    rows = []
    for week, (start, end) in windows.items():
        t0 = pd.Timestamp(start, tz="America/Chicago")
        before = rosters_at(t0)
        before["pid"] = before.player_id.map(site2c)
        rostered = set(before.pid.dropna())
        mine = set(before[before.owner == owner].pid.dropna())
        ros = ros_past(week, df)
        from dfo.decide.lineup import project_past
        cand = ros[["player_id", "pos", "team"]].assign(week=week)
        wp = project_past([week], cand.drop_duplicates("player_id")).groupby("player_id").proj.first()
        mod, _ = plan_moves(owner, ros, rostered, mine, prices, week, first=wp)
        j = s[(s.t >= t0) & (s.t < pd.Timestamp(end, tz="America/Chicago") + pd.Timedelta(days=1))
              & (((s.type == "fa_signing") & (s.to_owner == owner)) | ((s.type == "drop") & (s.from_owner == owner)))]
        adds = [site2c.get(p) for p in j[j.type == "fa_signing"].player_id]
        drops = [site2c.get(p) for p in j[j.type == "drop"].player_id]
        after = pts[pts.week >= week].groupby("player_id").points.sum()
        done = sorted(pts.groupby("week").player_id.nunique().loc[lambda x: x > 150].index)
        # score on the roster at the end of the window (trades in the window count for both), with
        # his waiver moves swapped for the module's
        t1 = pd.Timestamp(end, tz="America/Chicago") + pd.Timedelta(days=1)
        endr = rosters_at(t1)
        r_j = set(endr[endr.owner == owner].player_id.map(site2c).dropna())
        if owner == "Jordan" and (PROCESSED / "jordan_lineups_2026.csv").exists():
            lu = pd.read_csv(PROCESSED / "jordan_lineups_2026.csv")
            if week in set(lu.week):                       # the roster he actually had that week
                r_j = set(lu[lu.week == week].player_id.dropna())
        r_m = (r_j - set(a for a in adds if a)) | (set(d for d in drops if d) & mine)
        if len(mod):
            r_m = (r_m - set(mod["drop"])) | set(mod["add"])
        w1 = [week] if week in done else []
        wall = [w for w in done if w >= week]
        nm = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").name
        rows.append(dict(week=week,
                         jordan_adds=", ".join(str(nm.get(a, a)) for a in adds),
                         jordan_drops=", ".join(str(nm.get(d, d)) for d in drops),
                         module_adds=", ".join(mod.add_player) if len(mod) else "",
                         module_drops=", ".join(mod.drop_player) if len(mod) else "",
                         jordan_adds_points_since=round(sum(float(after.get(a, 0)) for a in adds), 1),
                         module_adds_points_since=round(sum(float(after.get(a, 0)) for a in mod["add"]), 1) if len(mod) else 0.0,
                         overlap=len(set(adds) & set(mod["add"])) if len(mod) else 0,
                         lineup_that_week_jordan=round(_actual_lineup(r_j, pts, w1), 1),
                         lineup_that_week_module=round(_actual_lineup(r_m, pts, w1), 1),
                         lineup_through_jordan=round(_actual_lineup(r_j, pts, wall), 1),
                         lineup_through_module=round(_actual_lineup(r_m, pts, wall), 1),
                         weeks_scored=len(wall)))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "decide_waivers_review.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    a = ap.parse_args()
    if a.review:
        pd.set_option("display.width", 250)
        pd.set_option("display.max_colwidth", 80)
        print(review().to_string(index=False))
    else:
        res = live()
        md = to_markdown(res)
        (PROCESSED / f"decide_waivers_w{res['week']}.md").write_text(md)
        rost_as_of = str(pd.read_csv(PROCESSED / "site_rosters.csv").as_of.max())
        res["best"].assign(rosters_as_of=rost_as_of).to_csv(PROCESSED / f"decide_waivers_w{res['week']}.csv", index=False)
        print(md)
