"""Front door views: how the rest of 2026 plays out, and how 2027-2029 are planned.

    PYTHONPATH=src python -m dfo.frontdoor.views      # writes data/frontdoor/season.json, future.json

Season (from the season simulator and the actual results):
- path: Jordan's remaining matchups, each with his win chance and both teams' projected scores, and his
  projected final record.
- standings: every team's record and points so far, projected wins and odds (1st, top 4, title, expected
  payout) from the dial's simulation (sim_2026.csv), and deadline posture.
- trend: Jordan's odds after each finished week. Two series: "as run" (what the daily runs said at the
  time, sim_2026_history.csv) and "replay" (the simulator re-run with the results through each week but
  today's player projections, so it shows what the results did to the odds, not what was known then;
  cached in sim_2026_trend.csv, one row per week).

Future (decide/outlook.py with Jordan's plan, config/owner_plans.yaml):
- years: committed salary, cap room, planned designations, strength, rank in the league, odds and expected
  payout per season.
- contracts: every player with a salary in 2026-2029, planned tags and RFA matches marked.
- offseason: the 2027 plan step by step and the scenarios it was chosen from (decide_offseason_2027.csv).
- picks: every pick Jordan holds, its original team, expected slot (2027: from the 2026 finish odds) and
  value tier (params_pick_tiers.csv).
"""
from __future__ import annotations

import json
import re

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, ROOT, load_config

OUT = ROOT / "data" / "frontdoor"
ME = "Jordan"
TREND = PROCESSED / "sim_2026_trend.csv"


def _read(name: str) -> pd.DataFrame:
    f = PROCESSED / name
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def _teams() -> dict[str, str]:
    return {o: v.get("current_team", o) for o, v in load_config("owners")["owners"].items() if v.get("current_team")}


def _records(res: pd.DataFrame) -> pd.DataFrame:
    done = res[res.score_a.notna()]
    rows = []
    for _, r in done.iterrows():
        for me, op, s, t in ((r.team_a, r.team_b, r.score_a, r.score_b), (r.team_b, r.team_a, r.score_b, r.score_a)):
            rows.append(dict(owner=me, w=float(s > t), l=float(s < t), t=float(s == t), pf=s, pa=t))
    return pd.DataFrame(rows).groupby("owner").sum()


# ---------------------------------------------------------------- season
def season(n: int = 4000) -> dict:
    from dfo.sim import season as S
    state = S.load_state()
    teams, res, weeks, done = state
    sim = S.simulate(n, seed=11, state=state, return_detail=True)
    owners = sim["owners"]
    oi = {o: i for i, o in enumerate(owners)}
    W = sim["weekly"]
    path = []
    for k, w in enumerate(weeks):
        for _, r in res[(res.week == w)].iterrows():
            if ME not in (r.team_a, r.team_b):
                continue
            opp = r.team_b if r.team_a == ME else r.team_a
            a, b = W[k, :, oi[ME]], W[k, :, oi[opp]]
            path.append(dict(week=int(w), opp=opp, p_win=round(float((a > b).mean() + 0.5 * (a == b).mean()), 3),
                             me=round(float(a.mean()), 1), them=round(float(b.mean()), 1)))
    rec = _records(res)
    wins_now = float(rec.loc[ME, "w"]) if ME in rec.index else 0.0
    proj = dict(wins=round(float(sim["wins"][:, oi[ME]].mean()), 1),
                p_win_out=round(float(np.mean([p["p_win"] for p in path])) if path else 0, 3))
    s = _read("sim_2026.csv").set_index("owner")
    post = _read("decide_deadline_posture.csv")
    post = post.set_index("owner").posture if len(post) else pd.Series(dtype=str)
    names = _teams()
    standings = []
    for o in s.index:
        r = rec.loc[o] if o in rec.index else pd.Series(dict(w=0, l=0, t=0, pf=0))
        q = s.loc[o]
        standings.append(dict(owner=o, team=names.get(o, o), w=int(r.w), l=int(r.l), t=int(r.t), pf=round(float(r.pf), 1),
                              wins=round(float(q.mean_wins), 1), p_first=round(float(q.p_first), 3), p_top4=round(float(q.p_top4), 3),
                              p_title=round(float(q.p_playoff_champ), 3), exp_payout=int(round(q.exp_payout)),
                              posture=str(post.get(o, "")) or None))
    standings.sort(key=lambda x: (-(x["w"] + 0.5 * x["t"]), -x["pf"]))
    return dict(made_at=pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), weeks_left=[int(w) for w in weeks],
                record=dict(w=int(rec.loc[ME, "w"]), l=int(rec.loc[ME, "l"]), t=int(rec.loc[ME, "t"])) if ME in rec.index else None,
                projected=proj, path=path, standings=standings, trend=trend(), sim_n=n,
                note="Win chances from the season simulator (rest-of-season projections, injuries, byes); "
                     "odds and payouts from the daily simulation including the league playoffs.")


def trend(n: int = 2500) -> dict:
    """Jordan's odds after each finished week: as run (history) and a replay with today's projections."""
    from dfo.sim import playoffs as P
    from dfo.sim import season as S
    res = _read("results_2026.csv")
    last = int(res[res.score_a.notna()].week.max())
    old = pd.read_csv(TREND) if TREND.exists() else pd.DataFrame(columns=["week"])
    have = set(old.week.astype(int)) if len(old) else set()
    # a replay is redone when the results it depends on change (a week added); weeks already done keep
    rows = []
    for k in range(0, last + 1):
        if k in have:
            continue
        st = S.load_state(inputs=dict(through_week=k))
        sim = S.simulate(n, seed=23, state=st)
        owners = sim["owners"]
        place = P.simulate(sim["rank"], owners, seed=31)
        ps = P.summarize(place, owners).set_index("owner")
        j = owners.index(ME)
        r = sim["rank"][:, j]
        rows.append(dict(week=k, p_first=round(float((r == 1).mean()), 3), p_top4=round(float((r <= 4).mean()), 3),
                         p_title=round(float(ps.loc[ME, "p_playoff_champ"]), 3),
                         made_at=pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")))
    if rows:
        old = pd.concat([old, pd.DataFrame(rows)], ignore_index=True).sort_values("week")
        old.to_csv(TREND, index=False)
    # the latest week is today's state: show the daily simulation's numbers (more draws, the same inputs) so
    # the chart ends where the scoreboard is
    cur = _read("sim_2026.csv")
    if len(cur) and len(old):
        me = cur.set_index("owner").loc[ME]
        i = old.week.astype(int) == last
        old.loc[i, ["p_first", "p_top4", "p_title"]] = [round(float(me.p_first), 3), round(float(me.p_top4), 3), round(float(me.p_playoff_champ), 3)]
    h = _read("sim_2026_history.csv")
    as_run = []
    if len(h):
        j = h[h.owner == ME].sort_values("as_of")
        as_run = [dict(at=str(x.as_of)[:16], week=int(x.week), p_first=round(float(x.p_first), 3),
                       p_title=round(float(x.p_playoff_champ), 3), exp_payout=int(round(x.exp_payout))) for x in j.itertuples()]
    return dict(replay=old[["week", "p_first", "p_top4", "p_title"]].to_dict("records"), as_run=as_run,
                note="Replay: the simulator re-run with the results through each week and today's player projections "
                     "(what the results did to your odds, not what was known then). As run: what the daily runs said.")


# ---------------------------------------------------------------- future
def future() -> dict:
    from dfo.decide.outlook import Outlook
    from dfo.decide.trade_search import picks_held
    from dfo.valuation import pick_tiers
    o = Outlook()
    t = o.team(ME)
    pay = o.payouts({ME: t})
    years = []
    for r in t.itertuples():
        y = int(r.season)
        lg = o.base[o.base.season == y].set_index("owner").strength.copy()
        lg[ME] = r.strength
        q = pay[(pay.owner == ME) & (pay.season == y)].iloc[0]
        years.append(dict(season=y, contracted=int(r.contracted), committed=int(r.committed), cap_room=int(r.cap_room),
                          strength=int(round(r.strength)), rank=int((lg > r.strength).sum() + 1),
                          league_median=int(round(lg.median())), p_first=round(float(q.p_first), 3),
                          p_top4=round(float(q.p_top4), 3), exp_payout=int(round(q.exp_payout)),
                          note=str(r.note) if isinstance(r.note, str) else "", released=str(r.released) if isinstance(r.released, str) else ""))
    # contracts: 2026 from the site, later seasons from the outlook (planned designations marked)
    ros = _read("site_rosters.csv")
    ros = ros[ros.owner == ME]
    plan = _read("designation_plan.csv")
    plan = plan[plan.owner == ME]
    kind = {(r.player_id, int(r.offseason)): r.type for r in plan.itertuples()}
    p = o.prices[o.prices.owner == ME].set_index("player_id")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    ros = ros.merge(ids, left_on="player_id", right_on="site_player_id", how="left")
    rows = []
    for r in ros.itertuples():
        pid = r.canonical_id if isinstance(r.canonical_id, str) else r.player_id
        row = dict(player=str(r.player).replace(" D/ST", ""), pos=r.pos, s2026=int(r.salary_now or 0), slot=r.slot)
        for y in (2027, 2028, 2029):
            v = float(p[f"salary_{y}"].get(pid, 0) or 0) if pid in p.index else 0.0
            row[f"s{y}"] = int(round(v))
            row[f"d{y}"] = kind.get((pid, y), "planned" if pid in o.planned.get(y, set()) else "")
        v = p.proj_points_2027.get(pid) if pid in p.index and "proj_points_2027" in p else None
        row["pts2027"] = int(round(float(v))) if v is not None and pd.notna(v) else None
        rows.append(row)
    rows.sort(key=lambda x: (-(x["s2027"] + x["s2028"] + x["s2029"]), -x["s2026"]))
    # offseason plan
    off = _read("decide_offseason_2027.csv")
    scen = [dict(scenario=r.scenario, payout_2027=int(r.payout_2027), payout_3yr=int(r.payout_3yr), room_2027=int(r.room_2027),
                 gibbs=f"${int(r.gibbs_rfa_price)} ({r.gibbs_price_range})" if pd.notna(r.gibbs_rfa_price) else "",
                 gibbs_to=str(r.gibbs_goes_to)) for r in off.itertuples()] if len(off) else []
    y27 = next((y for y in years if y["season"] == 2027), None)
    steps = []
    for r in plan.sort_values("offseason").itertuples():
        name = o.names.get(r.player_id, r.player_id)
        sal = int(round(float(getattr(r, f"salary_{int(r.offseason)}", 0) or 0)))
        steps.append(dict(offseason=int(r.offseason), action=("Franchise tag" if r.type == "tag" else "Match the RFA bid on" if r.type == "rfa" else r.type.title()),
                          player=name, salary=sal))
    if y27:
        steps.append(dict(offseason=2027, action="Auction", player="", salary=None,
                          text=("Over the cap after the plan: only $0 adds until the auction is over (leftovers)."
                                if y27["cap_room"] <= 0 else f"${y27['cap_room']} of room to bid with.")))
    steps.sort(key=lambda x: (x["offseason"], x["action"] == "Auction"))
    # picks
    s26 = _read("sim_2026.csv").set_index("owner")
    picks = []
    for y, rnd, orig in picks_held(ME):
        slot = float(s26.rookie_slot_mean.get(orig)) if y == 2027 and orig in s26.index else None
        val = pick_tiers.pick_value(rnd, slot)
        overall = (rnd - 1) * 10 + slot if slot is not None else None
        tier = None if overall is None else ("1-3" if overall <= 3.5 else "4-9" if overall <= 9.5 else "10-30")
        picks.append(dict(year=y, round=rnd, original=orig, slot=round(slot, 1) if slot is not None else None,
                          overall=round(overall, 1) if overall is not None else None, tier=tier, points=int(round(val))))
    held = {(p["year"], p["round"]) for p in picks if p["original"] == ME}
    missing = [dict(year=y, round=r) for y in (2027, 2028, 2029) for r in (1, 2, 3) if (y, r) not in held]
    return dict(made_at=pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds"), years=years, contracts=rows,
                offseason=dict(steps=steps, scenarios=scen, chosen=scen[0]["scenario"] if scen else None,
                               made_at=str(off.made_at.iloc[0]) if len(off) else None),
                picks=picks, own_picks_traded=missing,
                note="Cap and strength from the three-year outlook with your plan (config/owner_plans.yaml); "
                     "odds and payouts from the future-seasons simulation; pick values in lineup points over the rookie deal.")


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    s, f = season(), future()
    (OUT / "season.json").write_text(json.dumps(s, indent=1, default=str))
    (OUT / "future.json").write_text(json.dumps(f, indent=1, default=str))
    return dict(season=s, future=f)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    out = run()
    s, f = out["season"], out["future"]
    print("record", s["record"], "projected", s["projected"])
    for p in s["path"]:
        print(p)
    print("trend", s["trend"]["replay"])
    for y in f["years"]:
        print(y)
    print(f["offseason"]["steps"])
    print(f["picks"], f["own_picks_traded"])
