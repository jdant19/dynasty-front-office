"""Designation plan: which expiring players each team would keep with a tag, an RFA match or a
mid-level extension, offseason by offseason through 2029, within the league's limits.

Per team per offseason (config/league.yaml): 1 franchise tag, 1 mid-level extension, 2 RFAs.
Rights for the first offseason come from the site (it already applies the chain history: a player
tagged this year shows none). After a designation in the plan, the chain rules apply: no designation
the season after a tag; no repeating a type in a chain; at most three in a row.

Cost of each option (cap $):
  tag   that season's tag price at his position (site table; the latest year repeats)
  MLE   $15 / $30 / $45 (eligible if his last salary was under $10 and not a rookie-draft contract)
  RFA   matching the going price: the larger of the starting bid and 1 + RFA price per point x his
        points over the market level (RFAs cost ~31% more per point than the auction); length 1-4
        years, whichever is worth most, with the usual raises
Worth of an option: each covered season's auction cost of his production minus his salary, 0.88 a
year. Each team uses its slots on the options worth most (greedy by worth), only where worth > 0.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import load_config
from dfo.rules.contracts import mle_eligible, money, rfa_starting_bid, salary_schedule

SEASON = 2026
FUTURE = [2027, 2028, 2029]


def _limits() -> dict[str, int]:
    d = load_config("league")["designations"]
    return {"tag": d["franchise_tag"]["per_team"], "mle": d["mid_level_extension"]["per_team"],
            "rfa": d["rfa"]["per_team"]}


def _options(x, y: int, rights: set[str], last_salary: int, rookie: bool, tags, lam_rfa: float, h: float):
    """(type, salaries by future season, worth) for each option open to player row x in offseason y."""
    i0 = FUTURE.index(y)
    vals = [float(x[f"value_{yy}"]) for yy in FUTURE]
    out = []
    if "tag" in rights:
        sal = {y: tags[y].get(x.pos, 2)}
        out.append(("tag", sal, vals[i0] - sal[y]))
    if "mle" in rights and mle_eligible(max(last_salary, 1), rookie):
        ext = load_config("league")["designations"]["mid_level_extension"]["salaries"]
        sal = {FUTURE[i0 + j]: ext[j] for j in range(min(3, len(FUTURE) - i0))}
        out.append(("mle", sal, sum(h ** j * (vals[i0 + j] - ext[j]) for j in range(len(sal)))))
    if "rfa" in rights:
        price = max(rfa_starting_bid(max(last_salary, 0)), money(1 + lam_rfa * float(x[f"por_{y}"])))
        best = None
        for length in range(1, 5):
            sched = salary_schedule(price, length)
            sal = {FUTURE[i0 + j]: sched[j] for j in range(min(length, len(FUTURE) - i0))}
            worth = sum(h ** j * (vals[i0 + j] - s) for j, s in enumerate(sal.values()))
            if best is None or worth > best[2]:
                best = ("rfa", sal, worth)
        out.append(best)
    return out


def plan(base: pd.DataFrame, tags: dict, lam_rfa: float, h: float) -> pd.DataFrame:
    """One row per designation used: owner, player_id, offseason, type, salaries, worth (cap $,
    valued at that offseason)."""
    lim = _limits()
    site_rights = {"Restricted Free Agent": "rfa", "Franchise Tag": "tag", "Mid-Level Extension": "mle"}
    rows = []
    # state per player: salaries he is owed per season (contract, then any designation), chain so far
    owed = {r.player_id: {yy: int(r[f"salary_{yy}"]) for yy in [SEASON] + FUTURE}
            for _, r in base[base.owner.fillna("") != ""].iterrows()}
    chain: dict[str, list[str]] = {}
    info = base.set_index("player_id")
    for y in FUTURE:
        cands = []
        for pid, sal in owed.items():
            if sal.get(y, 0) > 0 or sal.get(y - 1, 0) == 0:
                continue                                     # still under contract, or already gone
            x = info.loc[pid]
            c = chain.get(pid, [])
            if not c:
                rights = {site_rights[t.strip()] for t in str(x.rights).split(",") if t.strip() in site_rights}
            elif c[-1] == "tag" or len(c) >= 3:
                rights = set()                               # no designation the season after a tag
            else:
                allowed = {"rfa": {"tag", "mle"}, "mle": {"tag", "rfa"}}.get(c[-1], set())
                rights = allowed - set(c)
            if not rights:
                continue
            for typ, sal_new, worth in _options(x, y, rights, sal.get(y - 1, 0),
                                                str(x.acquisition) == "rookie_draft", tags, lam_rfa, h):
                if worth > 0:
                    cands.append((worth, pid, x.owner, typ, sal_new))
        used: dict[tuple[str, str], int] = {}
        done: set[str] = set()
        for worth, pid, owner, typ, sal_new in sorted(cands, key=lambda t: -t[0]):
            if pid in done or used.get((owner, typ), 0) >= lim[typ]:
                continue
            used[(owner, typ)] = used.get((owner, typ), 0) + 1
            done.add(pid)
            owed[pid].update(sal_new)
            chain[pid] = chain.get(pid, []) + [typ]
            rows.append(dict(owner=owner, player_id=pid, offseason=y, type=typ, worth=round(worth, 1),
                             **{f"salary_{yy}": sal_new.get(yy, 0) for yy in FUTURE}))
    return pd.DataFrame(rows, columns=["owner", "player_id", "offseason", "type", "worth"]
                        + [f"salary_{yy}" for yy in FUTURE])


def owed_salaries(base: pd.DataFrame, p: pd.DataFrame) -> pd.DataFrame:
    """Salaries per season including the planned designations (for team strength and cap room)."""
    out = base[["player_id", "owner", "pos"] + [f"salary_{y}" for y in FUTURE]].copy().set_index("player_id")
    for r in p.itertuples():
        for y in FUTURE:
            s = getattr(r, f"salary_{y}")
            if s:
                out.loc[r.player_id, f"salary_{y}"] = s
    return out.reset_index()
