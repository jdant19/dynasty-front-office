"""Regular season: the remaining schedule played out thousands of times.

    PYTHONPATH=src python -m dfo.sim.season [n_sims]    # writes data/processed/sim_regular.csv

Each simulated week, for every team:
  - who is available: injury chain per player (outcomes.absence), starting from the site's injury
    status (Out or IR: out now; Questionable: out 25%, Doubtful 75%)
  - the lineup: the best legal lineup by expected points among available players (owners know who
    is out before kickoff), with a waiver pickup at each position always available at the waiver level
  - the score: each starter's draw from history (outcomes.Pools); byes score 0
This week uses the week's projections (games already played count as scored); later weeks use the
calibrated rest-of-season rate. Standings: wins, then points for. Payouts from config/league.yaml.
Assumes every owner sets his best lineup and rosters stay as they are (no waivers or trades);
both are noted limits until owner models (Phase 6).
"""
from __future__ import annotations

import datetime as dt
import sys
from dataclasses import dataclass

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.projections.features import implied_totals
from dfo.sim.outcomes import Pools, absence
from dfo.valuation import calibrate

SEASON = 2026
STATUS_OUT = {"Out": 1.0, "IR": 1.0, "Doubtful": 0.75, "Questionable": 0.25, "DNR": 1.0, "Suspended": 1.0}


@dataclass
class Team:
    owner: str
    pos: np.ndarray            # (players,)
    rate: np.ndarray           # (weeks, players) expected points if he plays (0 on bye)
    fixed: np.ndarray          # (players,) points already scored this week, nan if not yet
    start_out: np.ndarray      # (players,) chance he is out now
    phantom: np.ndarray        # (players,) waiver stand-ins


def _waiver_levels() -> dict[str, float]:
    from dfo.valuation.ours import waiver_weekly
    return waiver_weekly()


def load_state(today: dt.date | None = None) -> tuple[list[Team], pd.DataFrame, list[int], pd.DataFrame]:
    today = today or dt.datetime.now(dt.timezone.utc).date()
    lg = load_config("league")
    res = pd.read_csv(PROCESSED / "results_2026.csv")
    done = res[res.score_a.notna()]
    weeks = sorted(res[res.score_a.isna()].week.unique())
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    ros["rate"] = calibrate.shrink(ros.ros_ppg, ros.pos, "ros").clip(lower=0)
    wk = pd.read_csv(PROCESSED / "proj_week.csv")
    cur_week = int(wk.week.iloc[0])
    roster = pd.read_csv(PROCESSED / "site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    roster = roster.merge(ids.rename(columns={"site_player_id": "player_id", "canonical_id": "pid"}),
                          on="player_id", how="left")
    sched = implied_totals([SEASON])
    plays = {w: set(sched[sched.week == w].team) for w in weeks}
    played = sched[(sched.week == cur_week) & (pd.to_datetime(sched.gameday).dt.date < today)]
    pts = pd.read_csv(PROCESSED / f"points_{SEASON}.csv")
    pts = pts[(pts.season_type == "REG") & (pts.week == cur_week)].set_index("player_id").points
    rate = ros.set_index("player_id")
    proj_now = wk.set_index("player_id").proj
    team_of = rate.team
    slots = lg["roster"]["starters"]
    waiver = _waiver_levels()
    teams = []
    for owner, g in roster.groupby("owner"):
        g = g[g.pid.notna()].drop_duplicates("pid")
        pids = list(g.pid)
        pos = [rate.pos.get(p, q) for p, q in zip(pids, g.pos)]
        R = np.zeros((len(weeks), len(pids)))
        for j, p in enumerate(pids):
            nfl = team_of.get(p, g.nfl_team.iloc[j])
            for i, w in enumerate(weeks):
                if nfl not in plays[w]:
                    continue
                R[i, j] = proj_now.get(p, rate.rate.get(p, 0.0)) if w == cur_week else rate.rate.get(p, 0.0)
        fixed = np.array([pts.get(p, np.nan) if team_of.get(p) in set(played.team) else np.nan for p in pids])
        start = np.array([STATUS_OUT.get(str(s), 0.0) for s in g.injury_status.fillna("")])
        start = np.where(g.slot.eq("IR").to_numpy(), 1.0, start)
        # waiver stand-ins: one per starting slot at each position (+1 RB/WR for the flex)
        ph_pos = [p for p, n in slots.items() if p != "FLEX" for _ in range(n + (1 if p in ("RB", "WR") else 0))]
        PH = np.tile([waiver.get(p, 0.0) for p in ph_pos], (len(weeks), 1))
        teams.append(Team(owner=owner, pos=np.array(pos + ph_pos),
                          rate=np.concatenate([R, PH], axis=1),
                          fixed=np.concatenate([fixed, np.full(len(ph_pos), np.nan)]),
                          start_out=np.concatenate([start, np.zeros(len(ph_pos))]),
                          phantom=np.array([False] * len(pids) + [True] * len(ph_pos))))
    return teams, res, weeks, done


def _lineup(E: np.ndarray, avail: np.ndarray, pos: np.ndarray) -> np.ndarray:
    """Chosen starters (sims, players): best legal lineup by expected points among the available."""
    lg = load_config("league")["roster"]
    slots, flex_pos = lg["starters"], lg["flex_eligible"]
    chosen = np.zeros_like(avail)
    for p, k in slots.items():
        if p == "FLEX":
            continue
        idx = np.where(pos == p)[0]
        if not len(idx):
            continue
        order = idx[np.argsort(-E[idx], kind="stable")]
        A = avail[:, order]
        chosen[:, order] = A & (np.cumsum(A, axis=1) <= k)
    idx = np.where(np.isin(pos, flex_pos))[0]
    order = idx[np.argsort(-E[idx], kind="stable")]
    A = avail[:, order] & ~chosen[:, order]
    chosen[:, order] |= A & (np.cumsum(A, axis=1) <= slots.get("FLEX", 0))
    return chosen


def simulate(n: int = 10000, seed: int = 7, boost: dict[str, float] | None = None,
             state=None, return_detail: bool = False):
    """`boost`: extra points per remaining week for a team (the dial's probe)."""
    teams, res, weeks, done = state or load_state()
    boost = boost or {}
    rng = np.random.default_rng(seed)
    pools = Pools()
    ab = absence()
    owners = [t.owner for t in teams]
    oi = {o: i for i, o in enumerate(owners)}
    wins = np.zeros((n, len(owners)))
    pf = np.zeros((n, len(owners)))
    for _, r in done.iterrows():
        a, b = oi[r.team_a], oi[r.team_b]
        pf[:, a] += r.score_a
        pf[:, b] += r.score_b
        if r.score_a > r.score_b:
            wins[:, a] += 1
        elif r.score_b > r.score_a:
            wins[:, b] += 1
        else:
            wins[:, [a, b]] += 0.5
    out_state = {t.owner: rng.random((n, len(t.pos))) < t.start_out for t in teams}
    weekly = np.zeros((len(weeks), n, len(owners)))
    for i, w in enumerate(weeks):
        late = w >= 17
        for t in teams:
            if i > 0:                                   # injury chain moves a week
                po = np.array([ab.get(p, (0.0, 0.0))[0] for p in t.pos])
                oo = np.array([ab.get(p, (0.0, 0.0))[1] for p in t.pos])
                prev = out_state[t.owner]
                u = rng.random(prev.shape)
                out_state[t.owner] = np.where(prev, u < oo, u < po) & ~t.phantom
            E = t.rate[i]
            fixed_now = (i == 0) & ~np.isnan(t.fixed)
            avail = ~out_state[t.owner] | fixed_now
            if i == 0:
                avail = avail | fixed_now
            chosen = _lineup(np.where(fixed_now, np.nan_to_num(t.fixed), E), avail, t.pos)
            score = np.zeros(n)
            for p in np.unique(t.pos):
                cols = np.where((t.pos == p))[0]
                cols = cols[chosen[:, cols].any(axis=0)]
                if not len(cols):
                    continue
                proj = np.broadcast_to(E[cols], (n, len(cols)))
                draws = pools.draw(p, np.ascontiguousarray(proj), late, played_only=(i > 0), rng=rng)
                draws = np.where(E[cols] > 0, draws, 0.0)
                if i == 0:
                    f = t.fixed[cols]
                    draws = np.where(np.isnan(f), draws, f)
                score += (draws * chosen[:, cols]).sum(axis=1)
            weekly[i, :, oi[t.owner]] = score + boost.get(t.owner, 0.0)
        for _, r in res[(res.week == w)].iterrows():
            a, b = oi[r.team_a], oi[r.team_b]
            sa, sb = weekly[i, :, a], weekly[i, :, b]
            pf[:, a] += sa
            pf[:, b] += sb
            wins[:, a] += (sa > sb) + 0.5 * (sa == sb)
            wins[:, b] += (sb > sa) + 0.5 * (sa == sb)
    key = wins * 1e5 + pf + rng.random(pf.shape) * 1e-6
    rank = (-key).argsort(axis=1).argsort(axis=1) + 1          # 1 = first place
    out = dict(owners=owners, rank=rank, wins=wins, pf=pf)
    if return_detail:
        out["weekly"] = weekly
    return out


def summarize(sim: dict) -> pd.DataFrame:
    pay = load_config("league")["payouts"]["regular_season"]
    rows = []
    for j, o in enumerate(sim["owners"]):
        r = sim["rank"][:, j]
        rows.append(dict(owner=o, mean_wins=sim["wins"][:, j].mean(), mean_points=sim["pf"][:, j].mean(),
                         p_first=(r == 1).mean(), p_second=(r == 2).mean(), p_third=(r == 3).mean(),
                         p_top4=(r <= 4).mean(),
                         exp_regular_payout=sum(pay.get(k, 0) * (r == k).mean() for k in (1, 2, 3)),
                         rookie_slot_mean=(11 - r).mean()))
    return pd.DataFrame(rows).sort_values("exp_regular_payout", ascending=False).round(3)


if __name__ == "__main__":
    import time
    import warnings
    warnings.filterwarnings("ignore")
    t0 = time.time()
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 5000
    s = summarize(simulate(n))
    s.to_csv(PROCESSED / "sim_regular.csv", index=False)
    print(s.to_string(index=False))
    print(f"{n} sims in {time.time() - t0:.0f}s")
