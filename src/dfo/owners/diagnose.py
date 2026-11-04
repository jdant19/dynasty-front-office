"""What wins in this league: each owner-season's weekly lineup rebuilt from its roster, and every started
point traced to the decision that brought the player to that owner.

    PYTHONPATH=src python -m dfo.owners.diagnose     # writes owner_season_rosters.csv, lineup_attribution.csv,
                                                     # owner_season_lineups.csv, diag_*.csv

Roster: the preseason roster (keepers + auction buys, auctions.csv), changed by in-season trades. Archive
trades carry a season but no date: a trade counts before the auction when the receiver kept the player
at that season's auction, otherwise it is in-season and assumed at midseason (Jordan, 2026-11-02).
Waiver moves are not recorded and are left out.

Lineup: each week the roster starts QB, 2 RB, 3 WR, TE, FLEX, K and DEF by what a manager could know then:
points per game so far this season, blended with last season's (3 games' weight; position median for
players with no history). Empty slots take the waiver level.

Credit: a started player's points above the waiver level for his position go to the decision that put
him on that owner's team: the latest auction buy, rookie pick, RFA (won or matched) or trade before
that week; "other" when none is on record (waivers, free agency).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from dfo.common import PROCESSED, load_config

FIRST, LAST = 2014, 2025
PRIOR_GAMES = 3


def _ids(table: str) -> pd.DataFrame:
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    return nm[nm.table == table][["season", "name", "canonical_id"]].drop_duplicates(["season", "name"])


def preseason() -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "auctions.csv")
    a = a.merge(_ids("auctions"), left_on=["season", "player"], right_on=["season", "name"], how="left")
    a = a[a.canonical_id.notna() & a.season.between(FIRST - 1, LAST)]
    return a[["season", "owner", "canonical_id", "player", "pos", "salary", "kind"]].rename(
        columns={"canonical_id": "player_id"}).drop_duplicates(["season", "owner", "player_id"])


def trades() -> pd.DataFrame:
    """Archive player moves, flagged offseason (kept by the receiver at that auction) or in-season."""
    pre = preseason()
    kept = set(map(tuple, pre[pre.kind == "kept"][["season", "owner", "player_id"]].values))
    tr = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    tr = tr[(tr.kind == "player") & tr.player_id.notna() & (tr.season <= LAST)].copy()
    tr["in_season"] = [(s, o, p) not in kept for s, o, p in zip(tr.season, tr.receiver, tr.player_id)]
    return tr


def events() -> pd.DataFrame:
    """Every acquisition on record: season, step (0 offseason trade, 1 rookie draft, 2 RFA, 3 auction,
    4 in-season trade), owner, player, channel, and an event id for grading."""
    pre = preseason()
    ev = []
    b = pre[pre.kind.isin(["auction", "startup_auction"])]
    ev.append(pd.DataFrame(dict(season=b.season, step=3, owner=b.owner, player_id=b.player_id, channel="auction",
                                event=[f"A{s}-{p}" for s, p in zip(b.season, b.player_id)], cost=b.salary)))
    rk = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    rk = rk.merge(_ids("rookie_drafts"), left_on=["season", "player"], right_on=["season", "name"], how="left")
    rk = rk[rk.canonical_id.notna()]
    ev.append(pd.DataFrame(dict(season=rk.season, step=1, owner=rk.owner, player_id=rk.canonical_id,
                                channel="rookie_draft", event=[f"D{s}-{o}" for s, o in zip(rk.season, rk.overall)],
                                cost=rk.salary)))
    rf = pd.read_csv(PROCESSED / "rfa_bids.csv").groupby(["season", "player"], as_index=False).first()
    rf = rf.merge(_ids("rfa_bids"), left_on=["season", "player"], right_on=["season", "name"], how="left")
    rf = rf[rf.canonical_id.notna() & rf.final_owner.notna() & (rf.season <= LAST)]
    ev.append(pd.DataFrame(dict(season=rf.season, step=2, owner=rf.final_owner, player_id=rf.canonical_id,
                                channel=np.where(rf.outcome == "matched", "rfa_matched", "rfa_won"),
                                event=[f"R{s}-{p}" for s, p in zip(rf.season, rf.canonical_id)], cost=rf.final_price)))
    tr = trades()
    ev.append(pd.DataFrame(dict(season=tr.season, step=np.where(tr.in_season, 4, 0), owner=tr.receiver,
                                player_id=tr.player_id, channel="trade", event=tr.trade_id, cost=np.nan)))
    out = pd.concat(ev, ignore_index=True)
    out["t"] = out.season * 10 + out.step
    return out.sort_values("t").reset_index(drop=True)


def holdings() -> pd.DataFrame:
    """Who held whom in which weeks of each season, with the acquiring event. One row per stint."""
    pre = preseason()
    pre = pre[pre.season >= FIRST]
    ev = events()
    by_player = {pid: g for pid, g in ev.groupby("player_id")}
    nweeks = {s: (18 if s >= 2021 else 17) for s in range(FIRST, LAST + 1)}
    rows = []
    for x in pre.itertuples():
        g = by_player.get(x.player_id)
        ch, eid = "other", f"O{x.season}-{x.player_id}"
        if x.kind != "kept":
            ch, eid = "auction", f"A{x.season}-{x.player_id}"
        elif g is not None:
            g = g[g.t < x.season * 10 + 3]
            if len(g) and g.owner.iloc[-1] == x.owner:
                ch, eid = g.channel.iloc[-1], g.event.iloc[-1]
        rows.append(dict(season=x.season, owner=x.owner, player_id=x.player_id, salary=x.salary,
                         channel=ch, event=eid, w0=1, w1=nweeks[x.season]))
    h = pd.DataFrame(rows)
    tr = trades()
    for r in tr[tr.in_season].itertuples():
        mid = nweeks[r.season] // 2
        m = (h.season == r.season) & (h.owner == r.sender) & (h.player_id == r.player_id) & (h.w1 > mid)
        sal = h.loc[m, "salary"].max() if m.any() else np.nan
        h.loc[m, "w1"] = mid
        h = pd.concat([h, pd.DataFrame([dict(season=r.season, owner=r.receiver, player_id=r.player_id, salary=sal,
                                             channel="trade", event=r.trade_id, w0=mid + 1, w1=nweeks[r.season])])],
                      ignore_index=True)
    tags = pd.read_csv(PROCESSED / "hist_tags.csv")[["season", "owner", "canonical_id"]].dropna()
    tagged = set(map(tuple, tags.values))
    h["tagged"] = [(s, o, p) in tagged for s, o, p in zip(h.season, h.owner, h.player_id)]
    return h


def weekly(held: pd.DataFrame | None = None) -> pd.DataFrame:
    """Player-weeks with what a manager could expect before the week. With `held` (holdings), also adds
    an absent row (points NaN) for each week a held player's NFL team played without him: the healthy
    counterfactual for injury luck. Players with no game that season and no prior season are left out."""
    frames = []
    for s in range(FIRST - 1, LAST + 1):
        p = pd.read_csv(PROCESSED / f"points_{s}.csv", low_memory=False,
                        usecols=["season", "season_type", "week", "player_id", "pos", "team", "points"])
        frames.append(p[(p.season_type == "REG") & p.week.between(1, 18)])
    w = pd.concat(frames, ignore_index=True)
    w["pos"] = w.pos.replace({"DST": "DEF"})
    w = w.groupby(["season", "week", "player_id"], as_index=False).agg(
        points=("points", "sum"), pos=("pos", "first"), team=("team", "first"))
    last = w.groupby(["season", "player_id"]).points.mean().rename("last_ppg").reset_index()
    last["season"] += 1
    if held is not None:
        team_weeks = w[w.pos != "DEF"].groupby(["season", "team"]).week.unique()
        info = w.groupby(["season", "player_id"]).agg(pos=("pos", "first"), team=("team", "last"))
        prev = w.groupby(["season", "player_id"]).agg(pos=("pos", "first"), team=("team", "last")).reset_index()
        prev["season"] += 1
        prev = prev.set_index(["season", "player_id"])
        played = set(zip(w.season, w.week, w.player_id))
        add = []
        for (s, pid) in set(zip(held.season, held.player_id)):
            if s < FIRST:
                continue
            if (s, pid) in info.index:
                pos, team = info.loc[(s, pid)]
            elif (s, pid) in prev.index:
                pos, team = prev.loc[(s, pid)]
            else:
                continue
            if pos == "DEF":
                continue
            for wk in team_weeks.get((s, team), []):
                if (s, wk, pid) not in played:
                    add.append((s, wk, pid, np.nan, pos, team))
        w = pd.concat([w, pd.DataFrame(add, columns=["season", "week", "player_id", "points", "pos", "team"])],
                      ignore_index=True)
    w = w.merge(last, on=["season", "player_id"], how="left").sort_values(["player_id", "season", "week"])
    g = w.assign(p0=w.points.fillna(0), n0=w.points.notna().astype(int)).groupby(["season", "player_id"])
    prior = w.last_ppg.fillna(w.groupby(["season", "pos"]).points.transform("median"))
    w["expected"] = (g.p0.cumsum() - w.points.fillna(0) + PRIOR_GAMES * prior) / (
        g.n0.cumsum() - w.points.notna().astype(int) + PRIOR_GAMES)
    w["absent"] = w.points.isna()
    return w[w.season >= FIRST]


def waiver_levels() -> dict[str, float]:
    return {"QB": 8.57, "RB": 1.91, "WR": 2.8, "TE": 4.69, "K": 7.61, "DEF": 6.05}   # ours.waiver_weekly, 2026-11-02


def lineups(h: pd.DataFrame, w: pd.DataFrame, healthy: bool = False) -> pd.DataFrame:
    """Weekly lineups. healthy=False: who actually played. healthy=True: absent players play at what they
    were expected to score (the counterfactual for injury luck)."""
    w = w.assign(points=w.points.fillna(w.expected)) if healthy else w[~w.absent]
    st = dict(load_config("league")["roster"]["starters"])
    flex = st.pop("FLEX")
    flex_ok = load_config("league")["roster"]["flex_eligible"]
    wl = waiver_levels()
    rows = []
    for (s, o), g in h.groupby(["season", "owner"]):
        ww = w[w.season == s].merge(g[["player_id", "channel", "event", "tagged", "w0", "w1"]], on="player_id")
        ww = ww[(ww.week >= ww.w0) & (ww.week <= ww.w1)].sort_values("expected", ascending=False)
        for wk, gw in ww.groupby("week"):
            used = []
            for pos, n in st.items():
                c = gw[gw.pos == pos].head(n)
                used += list(c.index)
                rows += [(s, o, wk, pos, r.player_id, r.channel, r.event, r.tagged, r.points, r.points - wl[pos])
                         for r in c.itertuples()]
                rows += [(s, o, wk, pos, None, "waiver", None, False, wl[pos], 0.0)] * (n - len(c))
            fl = gw[gw.pos.isin(flex_ok) & ~gw.index.isin(used)].head(flex)
            rows += [(s, o, wk, "FLEX", r.player_id, r.channel, r.event, r.tagged, r.points, r.points - wl[r.pos])
                     for r in fl.itertuples()]
    return pd.DataFrame(rows, columns=["season", "owner", "week", "slot", "player_id", "channel", "event", "tagged",
                                       "points", "over_waiver"])


def build():
    h = holdings()
    h.to_csv(PROCESSED / "owner_season_rosters.csv", index=False)
    w = weekly(h)
    L = lineups(h, w)
    L.to_csv(PROCESSED / "lineup_attribution.csv", index=False)
    H = lineups(h, w, healthy=True)
    weeks = L.groupby("season").week.nunique()
    team = L.groupby(["season", "owner"]).points.sum().div(weeks, level=0).rename("lineup_ppg").reset_index()
    team["healthy_ppg"] = (H.groupby(["season", "owner"]).points.sum().div(weeks, level=0)).values
    team["injury_cost_ppg"] = (team.healthy_ppg - team.lineup_ppg).round(2)
    ch = L.groupby(["season", "owner", "channel"]).over_waiver.sum().unstack(fill_value=0)
    ch = ch.div(weeks.reindex(ch.index.get_level_values(0)).values, axis=0).round(2).reset_index()
    res = pd.read_csv(PROCESSED / "season_results.csv")[["season", "owner", "regular_season_finish", "playoff_result"]]
    out = team.merge(ch, on=["season", "owner"]).merge(res, on=["season", "owner"], how="left")
    out["lineup_rank"] = out.groupby("season").lineup_ppg.rank(ascending=False)
    out.to_csv(PROCESSED / "owner_season_lineups.csv", index=False)
    return h, L, out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    h, L, out = build()
    print("lineup vs finish rank corr:", round(spearmanr(out.lineup_ppg, -out.regular_season_finish).statistic, 3))
