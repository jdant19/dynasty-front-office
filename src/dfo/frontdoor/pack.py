"""The league data pack and the strategy memory, for the front door's Strategy tab.

    PYTHONPATH=src python -m dfo.frontdoor.pack      # writes data/frontdoor/pack/*.json

The Strategy tab's chat answers from these (through page tools), so a strategy conversation can look at
anything in the league without a session on the repo. Questions that need the models re-run are handed to
a Claude Code session (the "Front office analyst" scheduled task, or the next sync).

- players.json: every rostered player and the best free agents: owner, contract by season (2026-2029),
  rest-of-season points a game, projected 2027 points, surplus over salary 2027-29 (lineup points), rights,
  injury, and the model's read of his owner's 2027 designation.
- teams.json: each owner: team name, record and odds (2026), deadline posture, cap room now, 2027 room and
  strength from the outlook, picks held, trading and auction tendencies, observations.
- rules.json: config/league.yaml as text.
- memory.json: what is settled. Jordan's plan (config/owner_plans.yaml), standing strategy notes
  (data/journal/strategy_notes.csv: conclusions from earlier strategy talks and things he asked to keep),
  current observations, and the key numbers of his three-year outlook.
Every file carries its as-of time and sources.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd
import yaml

from dfo.frontdoor import strict
from dfo.common import PROCESSED, ROOT, load_config

OUT = ROOT / "data" / "frontdoor" / "pack"
NOTES = ROOT / "data" / "journal" / "strategy_notes.csv"
NOTE_COLS = ["id", "at", "source", "topic", "note", "status"]


def _read(name: str) -> pd.DataFrame:
    f = PROCESSED / name
    return pd.read_csv(f) if f.exists() else pd.DataFrame()


def _num(x, nd=0):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if np.isnan(v):
        return None
    return int(round(v)) if nd == 0 else round(v, nd)


def _now() -> str:
    return pd.Timestamp.now(tz="UTC").isoformat(timespec="seconds")


def players() -> dict:
    ros = _read("site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    ros = ros.merge(ids, left_on="player_id", right_on="site_player_id", how="left")
    ros["pid"] = ros.canonical_id.fillna(ros.player_id)
    pr = _read("prices.csv").set_index("player_id")
    rr = _read("proj_ros.csv").drop_duplicates("player_id").set_index("player_id")
    plans = _read("decide_owner_plans_2027.csv")
    plans = plans.drop_duplicates("player_id").set_index("player_id") if len(plans) else pd.DataFrame()
    rows = []
    for r in ros.itertuples():
        sal = [int(x) for x in str(r.salaries).split()] if isinstance(r.salaries, str) else []
        start = int(r.salary_start_year) if pd.notna(r.salary_start_year) else 2026
        by = {start + k: v for k, v in enumerate(sal)}
        p = r.pid
        row = dict(player=str(r.player).replace(" D/ST", ""), pos=r.pos, nfl=r.nfl_team, owner=r.owner, slot=r.slot,
                   salary={str(y): int(by.get(y, 0)) for y in (2026, 2027, 2028, 2029)},
                   free_agent_after=r.free_agency_year if isinstance(r.free_agency_year, str) else None,
                   rights=r.designation_rights if isinstance(r.designation_rights, str) else None,
                   injury=r.injury_status if isinstance(r.injury_status, str) else None,
                   ros_ppg=_num(rr.ros_ppg.get(p), 1) if p in rr.index else None,
                   proj_2027=_num(pr.proj_points_2027.get(p)) if p in pr.index else None,
                   surplus_2027_29=_num(pr.surplus_2027_29.get(p)) if p in pr.index else None,
                   market_value=_num(pr.fc_value.get(p)) if p in pr.index and "fc_value" in pr else None)
        if len(plans) and p in plans.index:
            q = plans.loc[p]
            row["owner_plan_2027"] = dict(action=q.action, expected_price=_num(q.expected_price), p_keep=_num(q.p_keep, 2))
        rows.append(row)
    fa = rr[~rr.index.isin(ros.pid) & rr.owner.isna()] if "owner" in rr else rr[~rr.index.isin(ros.pid)]
    fa = fa.sort_values("ros_ppg", ascending=False)
    fa = pd.concat([g.head(12) for _, g in fa.groupby("pos")])
    free = [dict(player=r.player, pos=r.pos, nfl=r.team, owner=None, ros_ppg=_num(r.ros_ppg, 1)) for r in fa.itertuples()]
    return dict(as_of=str(ros.as_of.max()) if len(ros) else None, made_at=_now(), rostered=rows, free_agents=free,
                sources="site_rosters (league site snapshot), proj_ros, prices (valuation), decide_owner_plans_2027",
                units="salary in $ by season; ros_ppg lineup points a game rest of 2026; proj_2027 and surplus in season lineup points "
                      "($0.53 a point later, P18); market_value is FantasyCalc dynasty value (an index, not $)")


def teams() -> dict:
    from dfo.rules.cap import cap_room
    owners = load_config("owners")["owners"]
    s = _read("sim_2026.csv").set_index("owner")
    post = _read("decide_deadline_posture.csv")
    post = post.set_index("owner") if len(post) else pd.DataFrame()
    cap = cap_room().set_index("owner")
    om = _read("owner_model.csv").set_index("owner")
    picks = _read("site_picks.csv")
    obs = _read("observations.csv")
    base = None
    try:
        from dfo.decide.outlook import Outlook
        base = Outlook().base
    except Exception:
        pass
    season = {}
    f = ROOT / "data" / "frontdoor" / "season.json"
    if f.exists():
        season = {r["owner"]: r for r in json.loads(f.read_text()).get("standings", [])}
    out = []
    for o in s.index:
        q = s.loc[o]
        m = om.loc[o] if o in om.index else pd.Series(dtype=object)
        row = dict(owner=o, team=owners.get(o, {}).get("current_team", o),
                   record=(f"{season[o]['w']}-{season[o]['l']}" if o in season else None),
                   odds_2026=dict(first=_num(q.p_first, 3), top4=_num(q.p_top4, 3), title=_num(q.p_playoff_champ, 3),
                                  exp_payout=_num(q.exp_payout), proj_wins=_num(q.mean_wins, 1)),
                   deadline_posture=(str(post.loc[o, "posture"]) if o in post.index else None),
                   cap_room_now=_num(cap.room.get(o)) if o in cap.index else None)
        if base is not None:
            b = base[base.owner == o].set_index("season")
            row["outlook"] = {str(y): dict(cap_room=_num(b.cap_room.get(y)), strength=_num(b.strength.get(y)),
                                           contracted=_num(b.contracted_players.get(y))) for y in b.index}
        pk = picks[(picks.owner == o) & (picks.used.astype(str).str.lower() != "true")] if len(picks) else picks
        row["picks"] = [f"{int(r.draft_year)} R{int(r.round)}" + (f" ({r.label})" if isinstance(r.label, str) and "from" in r.label else "")
                        for r in pk.itertuples()] if len(pk) else []
        row["tendencies"] = {k: (_num(m.get(k), 2) if isinstance(m.get(k), (int, float, np.floating)) else (None if pd.isna(m.get(k)) else str(m.get(k))))
                             for k in ("titles", "avg_regular_finish", "trades_per_season", "top_partners", "picks_bought_minus_sold",
                                       "auction_price_vs_league", "spend_RB", "spend_WR", "rfa_match_rate", "tags", "posture")
                             if k in m.index}
        if len(obs):
            row["observations"] = [dict(date=r.date, note=r.note, verdict=r.verdict) for r in obs[obs.owner == o].itertuples()
                                   if not isinstance(r.expires, str) or r.expires >= pd.Timestamp.now().strftime("%Y-%m-%d")]
        out.append(row)
    return dict(made_at=_now(), teams=out,
                sources="sim_2026 (season simulator), decide_deadline_posture, rules.cap.cap_room (site snapshot), "
                        "outlook (2027-29), site_picks, owner_model (2014-2025 history), observations",
                as_of=str(cap.as_of.max()) if "as_of" in cap else None)


def notes() -> pd.DataFrame:
    return pd.read_csv(NOTES, dtype=str).fillna("") if NOTES.exists() else pd.DataFrame(columns=NOTE_COLS)


def memory() -> dict:
    plan = (yaml.safe_load((ROOT / "config" / "owner_plans.yaml").read_text()) or {}).get("Jordan", {}) \
        if (ROOT / "config" / "owner_plans.yaml").exists() else {}
    n = notes()
    n = n[n.status.isin(["", "active"])] if len(n) else n
    fut = {}
    f = ROOT / "data" / "frontdoor" / "future.json"
    if f.exists():
        fu = json.loads(f.read_text())
        fut = dict(years=fu.get("years"), plan_steps=(fu.get("offseason") or {}).get("steps"),
                   scenarios=(fu.get("offseason") or {}).get("scenarios"))
    return dict(made_at=_now(), plan=plan, notes=n[["id", "at", "source", "topic", "note"]].to_dict("records") if len(n) else [],
                outlook=fut,
                principles="P10 kickers and defenses are streamed weekly; P18 values: this season by simulated payout, "
                           "later seasons at $0.53 a lineup point (1.6 lineup points per $1 of salary); next season's cap is "
                           "real money, in-season cap is worth little; trade ideas are not offers until Jordan proposes them.",
                sources="config/owner_plans.yaml, data/journal/strategy_notes.csv, future.json (outlook and offseason scenarios)")


def rules() -> dict:
    return dict(made_at=_now(), league_yaml=(ROOT / "config" / "league.yaml").read_text())


def _clean(x):
    """Strict JSON for the page: NaN and infinities become null, numpy numbers plain numbers."""
    if isinstance(x, dict):
        return {k: _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (float, np.floating)):
        return None if not np.isfinite(x) else float(x)
    return x


def run() -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    out = {k: _clean(v) for k, v in dict(players=players(), teams=teams(), memory=memory(), rules=rules()).items()}
    for k, v in out.items():
        (OUT / f"{k}.json").write_text(strict.dumps(v))
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    o = run()
    for k, v in o.items():
        print(k, len(json.dumps(v, default=str)), "bytes")
    print(o["players"]["rostered"][0])
    print(o["teams"]["teams"][0])
