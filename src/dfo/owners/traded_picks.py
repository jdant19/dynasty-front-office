"""Which player each traded rookie pick became.

    PYTHONPATH=src python -m dfo.owners.traded_picks     # writes traded_picks.csv

Draft order: within each round, slot k belongs to the team that finished 11-k in the previous regular
season (checked against every untraded slot 2014-2026; a new owner takes over his predecessor's slot:
Eric -> Kevin -> Ryan, Winston -> Max). A traded pick's original team comes from the asset text
("#16", "Zach's", "Matts", "(from Qasim)", "his own back"), otherwise from the picks the sender held
at the time of that trade (his own first, then picks he had received). Its slot gives the overall pick
and the player drafted there. The pick is "used" when the team that drafted at that slot is the
trade's receiver, "re-traded" when a later holder drafted, and "unverified" when the drafting team is
not in the chain on record (an unrecorded trade, or a conditional pick that never conveyed).
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dfo.common import PROCESSED

SUCCESSOR = {"Eric": "Kevin", "Kevin": "Ryan", "Winston": "Max"}
OWNERS = ["Ben", "Johnny", "Jordan", "Matt", "Max", "Mike", "Qasim", "Russ", "Ryan", "Zach",
          "Winston", "Eric", "Kevin"]


def draft_order() -> dict[int, dict[str, int]]:
    """season -> {original team: slot within each round}."""
    res = pd.read_csv(PROCESSED / "season_results.csv")
    out = {}
    for s in range(2014, 2028):
        prev = res[res.season == s - 1]
        if not len(prev):
            continue
        cur = set(res[res.season == s].owner) if s in set(res.season) else set(prev.owner)
        order = {}
        for r in prev.itertuples():
            o = r.owner
            while o not in cur and o in SUCCESSOR and s <= 2026:
                o = SUCCESSOR[o]
            order[o] = 11 - int(r.regular_season_finish)
        out[s] = order
    return out


def _named_owner(text: str, receiver: str, sender: str) -> str | None:
    t = str(text)
    if "his own" in t:
        return receiver
    m = re.search(r"from (\w+)", t)
    if m and m.group(1) in OWNERS:
        return m.group(1)
    for o in OWNERS:
        if re.search(rf"\b{o}'?s\b", t) or re.search(rf"\b{o}s\b", t):
            return o
    return None


def _later_holders(out: pd.DataFrame, after: str, team: str, y: int, rnd: int) -> set[str]:
    """Teams a (year, round) pick could have passed to after a trade: whoever `team` later sent such
    a pick to, and whoever they sent one to after that."""
    same = out[(out.pick_year == y) & (out["round"] == rnd)]
    found, frontier = set(), [(after, team)]
    while frontier:
        t0, who = frontier.pop()
        for r in same[(same.trade_id > t0) & (same.sender == who)].itertuples():
            if r.receiver not in found:
                found.add(r.receiver)
                frontier.append((r.trade_id, r.receiver))
    return found


def resolve() -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    a = a[a.kind == "pick"].copy()
    a["order_key"] = a.trade_id.str.extract(r"(\d{4}-\d+)")[0]
    a = a.sort_values(["season", "trade_id"])
    rk = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    draft = rk.set_index(["season", "overall"])
    order = draft_order()
    # who holds which pick (year, round, original) going into each trade
    held: dict[tuple, str] = {}

    def holder(key):
        return held.get(key, key[2])

    rows = []
    for r in a.itertuples():
        y = int(r.pick_year) if pd.notna(r.pick_year) else None
        rnd = int(r.pick_round) if pd.notna(r.pick_round) else None
        m = re.search(r"#\s*(\d+)", str(r.asset))
        overall = original = None
        how = ""
        if y and m:
            n = int(m.group(1))
            overall = n if n > 10 or rnd in (None, 1) else n          # "#12" is overall; "1st (#6)" is slot 6
            if rnd and n <= 10 and rnd > 1:
                overall = (rnd - 1) * 10 + n
            rnd = (overall - 1) // 10 + 1
            slot = (overall - 1) % 10 + 1
            original = next((o for o, k in order.get(y, {}).items() if k == slot), None)
            how = "pick number in the asset"
        elif y and rnd:
            vague = re.search(r"best|weaker|whichever|highest|better|worse", str(r.asset), re.I)
            original = None if vague else _named_owner(r.asset, r.receiver, r.sender)
            how = "team named in the asset" if original else ""
            if vague:
                mine = sorted((order[y][k[2]], k[2]) for k in [(y, rnd, o) for o in order.get(y, {})]
                              if holder(k) == r.sender)
                if mine and re.search(r"best|better|highest", str(r.asset), re.I):
                    original, how = mine[0][1], "best of the sender's picks in that round"
                elif mine and re.search(r"weaker|worse", str(r.asset), re.I):
                    original, how = mine[-1][1], "weaker of the sender's picks in that round"
            if not original and not vague:
                mine = [k for k in [(y, rnd, o) for o in order.get(y, {})] if holder(k) == r.sender]
                own = (y, rnd, r.sender)
                if own in mine:
                    original, how = r.sender, "sender's own pick"
                elif len(mine) == 1:
                    original, how = mine[0][2], "only such pick the sender held"
                elif mine:
                    how = "sender held several; not named"
            if original and original in order.get(y, {}):
                overall = (rnd - 1) * 10 + order[y][original]
        status, player, pid, drafted_by = "unresolved", None, None, None
        if overall and (y, overall) in draft.index:
            d = draft.loc[(y, overall)]
            d = d.iloc[0] if isinstance(d, pd.DataFrame) else d
            player, drafted_by = d.player, d.owner
            status = "drafted"
        elif y and y > 2026:
            status = "future pick"
        if original and rnd:
            held[(y, rnd, original)] = r.receiver
        rows.append(dict(trade_id=r.trade_id, season=r.season, receiver=r.receiver, sender=r.sender, asset=r.asset,
                         pick_year=y, round=rnd, original_team=original, overall=overall, how=how,
                         player=player, drafted_by=drafted_by, status=status))
    out = pd.DataFrame(rows)
    # second pass: a pick whose default reading is drafted by a team outside the chain (or that could
    # not be read) is matched to the one slot in that round the receiver, or a later holder, drafted
    # and no other traded pick already accounts for
    claimed = set()
    for r in out.itertuples():
        if r.status == "drafted" and r.drafted_by == r.receiver:
            claimed.add((r.pick_year, r.overall))
    for i, r in out.iterrows():
        named = r.how in ("pick number in the asset", "team named in the asset")
        if pd.isna(r.pick_year) or pd.isna(r["round"]):
            continue
        y, rnd = int(r.pick_year), int(r["round"])
        later_recv = _later_holders(out, r.trade_id, r.receiver, y, rnd)
        if r.status == "drafted" and (r.drafted_by == r.receiver or r.drafted_by in later_recv or named):
            continue
        cands = []
        for o, k in order.get(y, {}).items():
            if o == r.receiver:                        # his own pick was never traded to him
                continue
            ov = (rnd - 1) * 10 + k
            if (y, ov) in draft.index and (y, ov) not in claimed:
                d = draft.loc[(y, ov)]
                d = d.iloc[0] if isinstance(d, pd.DataFrame) else d
                if d.owner == r.receiver or d.owner in later_recv:
                    cands.append((o, ov, d.player, d.owner))
        mine = [c for c in cands if c[3] == r.receiver] or cands
        if len(mine) == 1:
            o, ov, pl, by = mine[0]
            out.loc[i, ["original_team", "overall", "player", "drafted_by", "status", "how"]] = \
                [o, ov, pl, by, "drafted", "matched to the slot the receiver drafted"]
            if by == r.receiver:
                claimed.add((y, ov))
    # later holders of the same pick, to tell "used" from "re-traded"
    later = {}
    for r in out.itertuples():
        if r.original_team and r.round:
            later.setdefault((r.pick_year, r.round, r.original_team), []).append((r.trade_id, r.receiver))
    use = []
    for r in out.itertuples():
        if r.status != "drafted":
            use.append(r.status)
            continue
        chain = later.get((r.pick_year, r.round, r.original_team), [])
        after = [rec for tid, rec in chain if tid > r.trade_id] + list(
            _later_holders(out, r.trade_id, r.receiver, r.pick_year, r.round))
        if r.drafted_by == r.receiver and not after:
            use.append("used by receiver")
        elif after and r.drafted_by in after:
            use.append("re-traded")
        elif r.drafted_by == r.receiver:
            use.append("used by receiver")
        else:
            use.append("unverified")
    out["use"] = use
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    nm = nm[nm.table == "rookie_drafts"][["season", "name", "canonical_id"]].drop_duplicates(["season", "name"])
    out = out.merge(nm, left_on=["pick_year", "player"], right_on=["season", "name"], how="left",
                    suffixes=("", "_d")).drop(columns=["season_d", "name"]).rename(columns={"canonical_id": "player_id"})
    out["event"] = [f"D{int(y)}-{int(o)}" if pd.notna(o) and pd.notna(y) else None
                    for y, o in zip(out.pick_year, out.overall)]
    out.to_csv(PROCESSED / "traded_picks.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 45)
    o = resolve()
    print(o.use.value_counts())
    print(o[["trade_id", "receiver", "asset", "original_team", "overall", "player", "drafted_by", "use", "how"]].to_string(index=False))
