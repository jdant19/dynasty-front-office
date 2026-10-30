"""Our $: what a player is worth to one team (Jordan's by default), not to the league in general.

    PYTHONPATH=src python -m dfo.valuation.ours [owner]   # writes data/processed/ours_<owner>.csv

This season: the change in the team's expected starting-lineup points over the remaining regular
season weeks if the player joins (dropping whichever current player costs the least) or leaves, with
the lineup re-picked every week (byes from the NFL schedule, each player's calibrated rest-of-season
rate x his position's availability). A waiver pickup at each position is always available at the
waiver level (weekly points of the best unrostered player by weekly rank, 2020-2025, roster depth from
the site), so losing a kicker costs his edge over streaming, not his points. Converted at the
in-season price per point.
Later seasons: his Future $ (next year's roster is rebuilt at the auction, so the market level is
the team's real alternative then).
The two stay separate columns: pushing now weights this season, building weights later seasons, and
the dial (Phase 5) sets the weights. Until then there is no combined number.
"""
from __future__ import annotations

import sys

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.projections.features import implied_totals
from dfo.valuation import calibrate
from dfo.valuation.auction import market_params

SEASON = 2026
DIAL_WEIGHT = 1.0          # placeholder until Phase 5


def _slots() -> tuple[dict[str, int], list[str]]:
    r = load_config("league")["roster"]
    return dict(r["starters"]), r["flex_eligible"]


def lineup_points(week_pts: pd.DataFrame) -> float:
    """Sum over weeks of the best legal lineup's expected points. week_pts: player rows x week columns,
    with a 'pos' column."""
    slots, flex_pos = _slots()
    total = 0.0
    weeks = [c for c in week_pts.columns if c != "pos"]
    for w in weeks:
        col = week_pts[[w, "pos"]].sort_values(w, ascending=False)
        used = set()
        for pos, n in slots.items():
            if pos == "FLEX":
                continue
            pick = col[(col.pos == pos) & ~col.index.isin(used)].head(n)
            used |= set(pick.index)
            total += pick[w].sum()
        flex = col[col.pos.isin(flex_pos) & ~col.index.isin(used)].head(slots.get("FLEX", 0))
        total += flex[w].sum()
    return float(total)


def waiver_weekly() -> dict[str, float]:
    ev = pd.read_csv(PROCESSED / "proj_weekly_backtest.csv", usecols=["season", "week", "pos", "ecr", "points"])
    ev["wrank"] = ev.groupby(["season", "week", "pos"]).ecr.rank(method="first")
    rostered = pd.read_csv(PROCESSED / "site_rosters.csv").groupby("pos").size()
    out = {}
    for pos in ["QB", "RB", "WR", "TE", "K", "DEF"]:
        n = int(rostered.get(pos, 10))
        g = ev[(ev.pos == pos) & ev.wrank.between(n + 1, n + 3)]
        out[pos] = float(g.points.mean()) if len(g) else 0.0
    return out


def _waiver_rows(weeks: list[int]) -> pd.DataFrame:
    slots, _ = _slots()
    lvl = waiver_weekly()
    rows = []
    for pos, v in lvl.items():
        for i in range(slots.get(pos, 0) + (1 if pos in ("RB", "WR") else 0)):
            row = {w: v for w in weeks}
            row.update(player_id=f"waiver-{pos}-{i}", pos=pos)
            rows.append(row)
    return pd.DataFrame(rows).set_index("player_id")


def build(owner: str = "Jordan") -> pd.DataFrame:
    _, _, lam_now = market_params()
    prices = pd.read_csv(PROCESSED / "prices.csv")
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    week0 = int(ros.from_week.iloc[0])
    ros["rate"] = calibrate.shrink(ros.ros_ppg, ros.pos, "ros") * ros.availability
    sched = implied_totals([SEASON])
    weeks = list(range(week0, 19))
    plays = {w: set(sched[sched.week == w].team) for w in weeks}
    def grid(df: pd.DataFrame) -> pd.DataFrame:
        g = pd.DataFrame({w: np.where(df.team.isin(plays[w]), df.rate, 0.0) for w in weeks}, index=df.player_id)
        g["pos"] = df.pos.to_numpy()
        return g
    pool = ros.set_index("player_id")
    roster = prices[prices.owner == owner].player_id
    waiver = _waiver_rows(weeks)
    mine = pd.concat([grid(pool.loc[pool.index.intersection(roster)].reset_index()), waiver])
    base = lineup_points(mine)
    rows = []
    # current players: what the lineup loses without him
    for pid in mine.index.difference(waiver.index):
        rows.append(dict(player_id=pid, on_roster=True, lineup_change=base - lineup_points(mine.drop(index=pid))))
    # everyone else: add him, dropping the current player whose loss hurts least, counting his
    # future value too (a negative future is owed whether he is cut now or later, so it counts as 0)
    life = prices.set_index("player_id")
    def drop_cost(r):
        fut = float(life.future_dollars.get(r["player_id"], 0))
        return lam_now * r["lineup_change"] + max(0.0, fut)
    cheapest_drop = min(rows, key=drop_cost)["player_id"] if rows else None
    others = pool.drop(index=pool.index.intersection(mine.index))
    others = others[others.rate > 0]
    g_others = grid(others.reset_index())
    without = mine.drop(index=cheapest_drop) if cheapest_drop is not None else mine
    for pid in g_others.index:
        rows.append(dict(player_id=pid, on_roster=False,
                         lineup_change=lineup_points(pd.concat([without, g_others.loc[[pid]]])) - base))
    out = pd.DataFrame(rows).merge(prices, on="player_id", how="left")
    out["ours_season"] = (DIAL_WEIGHT * lam_now * out.lineup_change).round().astype(int)
    out["ours_future"] = out.future_dollars.fillna(0).round().astype(int)
    out["dial_weight"] = DIAL_WEIGHT
    out["dial_note"] = "placeholder 1.0 until Phase 5 computes the dial"
    out["drop_if_added"] = cheapest_drop
    cols = ["player_id", "player", "pos", "team", "owner", "on_roster", "lineup_change", "ours_season",
            "ours_future", "season_dollars", "future_dollars", "market_auction", "fc_reference",
            "dial_weight", "dial_note", "drop_if_added", "made_at"]
    out = out[cols].sort_values(["ours_season", "ours_future"], ascending=False)
    out.to_csv(PROCESSED / f"ours_{owner.lower()}.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    o = build(sys.argv[1] if len(sys.argv) > 1 else "Jordan")
    print(o[o.on_roster].drop(columns=["dial_note", "made_at", "player_id", "drop_if_added"]).to_string(index=False))
    print("\nTop free agents / other teams' players by lineup gain:")
    print(o[~o.on_roster].head(12).drop(columns=["dial_note", "made_at", "player_id"]).to_string(index=False))
