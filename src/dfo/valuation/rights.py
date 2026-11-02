"""Designation plan: which expiring players each team would keep with a tag, an RFA match or a
mid-level extension, offseason by offseason through 2029, within the league's limits.

Per team per offseason (config/league.yaml): 1 franchise tag, 1 mid-level extension, 2 RFAs.
Rights for the first offseason come from the site (it already applies the chain history: a player
tagged this year shows none). After a designation in the plan, the chain rules apply: no designation
the season after a tag; no repeating a type in a chain; at most three in a row.

Cost of each option (cap $):
  tag   that season's tag price at his position (site table; the latest year repeats)
  MLE   $15 / $30 / $45 (eligible if his last salary was under $10 and not a rookie-draft contract)
  RFA   matching the going price: the larger of the starting bid and 1 + RFA price per point at his
        position x his points over the market level (RBs have gone near auction rates, WRs well
        above: val_rfa_price.csv); length 1-4 years, with the usual raises
Chains are valued whole: every legal path through 2029 (for example RFA for one year, then the tag
the next season) is worth the sum of each covered season's auction cost of his production minus his
salary, 0.88 a year. Each player's best path is kept where it is worth more than letting him go;
teams fill their slots with the most valuable paths first, falling back to a player's next-best path
when a slot is taken.
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


def _paths(x, y0: int, first_rights: set[str], last_salary: int, rookie: bool, tags, rfa_ppp: dict, h: float,
           chain: tuple[str, ...] = ()) -> list[tuple[float, tuple]]:
    """Every legal designation path for player row x from offseason y0 through the horizon:
    [(worth valued at y0, ((offseason, type, {season: salary}), ...))]. Includes stopping (letting
    him go) at any point."""
    if y0 > FUTURE[-1]:
        return [(0.0, ())]
    if chain:
        if chain[-1] == "tag" or len(chain) >= 3:
            return [(0.0, ())]
        rights = {"rfa": {"tag", "mle"}, "mle": {"tag", "rfa"}}.get(chain[-1], set()) - set(chain)
    else:
        rights = first_rights
    out = [(0.0, ())]                                            # let him go
    i0 = FUTURE.index(y0)
    vals = {yy: float(x[f"value_{yy}"]) for yy in FUTURE}
    opts = []
    if "tag" in rights:
        opts.append(("tag", {y0: tags[y0].get(x.pos, 2)}))
    if "mle" in rights and mle_eligible(max(last_salary, 1), rookie):
        ext = load_config("league")["designations"]["mid_level_extension"]["salaries"]
        opts.append(("mle", {FUTURE[i0 + j]: ext[j] for j in range(min(3, len(FUTURE) - i0))}))
    if "rfa" in rights:
        price = max(rfa_starting_bid(max(last_salary, 0)),
                    money(1 + rfa_ppp.get(x.pos, 1.5) * float(x[f"por_{y0}"])))
        for length in range(1, 5):
            sched = salary_schedule(price, length)
            sal = {FUTURE[i0 + j]: sched[j] for j in range(min(length, len(FUTURE) - i0))}
            opts.append(("rfa", sal))
            if i0 + length >= len(FUTURE):
                break
    for typ, sal in opts:
        here = sum(h ** (yy - y0) * (vals[yy] - s) for yy, s in sal.items())
        nxt = max(sal) + 1
        for w, rest in _paths(x, nxt, first_rights, sal[max(sal)], False, tags, rfa_ppp, h, chain + (typ,)):
            out.append((here + h ** (nxt - y0) * w, ((y0, typ, sal),) + rest))
    return out


def plan(base: pd.DataFrame, tags: dict, rfa_ppp: dict, h: float) -> pd.DataFrame:
    """One row per designation used: owner, player_id, offseason, type, salaries, worth (cap $,
    valued at that offseason, this designation's covered seasons only)."""
    lim = _limits()
    site_rights = {"Restricted Free Agent": "rfa", "Franchise Tag": "tag", "Mid-Level Extension": "mle"}
    info = base[base.owner.fillna("") != ""].set_index("player_id")
    # each player's paths, from the offseason his contract runs out
    cands = []
    for pid, x in info.iterrows():
        sal = [int(x[f"salary_{yy}"]) for yy in [SEASON] + FUTURE]
        end = next((i for i in range(1, len(sal)) if sal[i] == 0 and sal[i - 1] > 0), None)
        if end is None:
            continue
        y0 = ([SEASON] + FUTURE)[end]
        rights = {site_rights[t.strip()] for t in str(x.rights).split(",") if t.strip() in site_rights}
        if not rights:
            continue
        paths = sorted((p for p in _paths(x, y0, rights, sal[end - 1], str(x.acquisition) == "rookie_draft",
                                          tags, rfa_ppp, h) if p[1] and p[0] > 0), key=lambda p: -p[0])
        if paths:
            cands.append((pid, x.owner, y0, paths))
    used: dict[tuple, int] = {}
    rows = []
    # most valuable best-paths first; fall back to a player's next path if a slot is taken
    for pid, owner, y0, paths in sorted(cands, key=lambda c: -c[3][0][0]):
        for worth, steps in paths:
            need = {}
            for (yy, typ, _) in steps:
                need[(owner, yy, typ)] = need.get((owner, yy, typ), 0) + 1
            if all(used.get(k, 0) + n <= lim[k[2]] for k, n in need.items()):
                for k, n in need.items():
                    used[k] = used.get(k, 0) + n
                x = info.loc[pid]
                for (yy, typ, sal) in steps:
                    w = sum(h ** (s - yy) * (float(x[f"value_{s}"]) - v) for s, v in sal.items())
                    rows.append(dict(owner=owner, player_id=pid, offseason=yy, type=typ, worth=round(w, 1),
                                     path_worth=round(worth, 1),
                                     **{f"salary_{s}": sal.get(s, 0) for s in FUTURE}))
                break
    return pd.DataFrame(rows, columns=["owner", "player_id", "offseason", "type", "worth", "path_worth"]
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
