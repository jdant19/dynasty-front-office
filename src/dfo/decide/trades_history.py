"""The trade evaluator on past trades: what each side should have expected when the trade was made.

    PYTHONPATH=src python -m dfo.decide.trades_history      # the approved sample: decide_trades_review_history.csv

Same question and units as decide/trades.py (P16), with the inputs a manager had then:
- When: archive trades have no date. Offseason if the receiver kept the player at that auction (or no
  player moved), otherwise midseason (diagnose.py's rule).
- This season: each team's roster at that week (rebuilt rosters), each player's expected points per
  game before that week (points so far plus 3 games of last season's rate), best-lineup points over the
  remaining weeks with waiver pickups at the waiver level. A player who has missed his team's last games
  counts at the share of the rest of the season such players went on to play (2014-2025), since injury
  reports before 2021 are not on file. Offseason trades also move this season's
  salary, charged at what it normally buys (PriceCurve); in-season cap is worth close to nothing.
  Weighted by the team's payout per point then (owner_season_posture.csv: preseason or midseason).
- Next two seasons: each player's expected lineup points over waiver (LineupValue of his rate x 16
  games, aged one year at a time) minus what his salary normally buys, while his contract runs (the
  contract length was set when he signed: read from the salaries he was later kept at), never below
  cutting him (dead cap); after it ends, 0 (rights not valued). Picks at the slot when the asset names
  it, else the round average; conditional picks at half. Later cap at what cap buys. Weighted by the
  league's average payout per point that preseason, x0.88 a year.
Known gaps: rookie and pick values use curves fit on 2014-2022 drafts (some hindsight for early
trades); conditional terms are taken at half.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.owners import diagnose as D
from dfo.rules.contracts import salary_schedule
from dfo.valuation.ours import _waiver_rows, lineup_points

HAIRCUT = 0.88
YEARS_AHEAD = 2
GAMES = 16


def _context():
    from dfo.valuation.aging import delta
    from dfo.valuation.lineup_value import load
    lv, curve = load()
    h = pd.read_csv(PROCESSED / "owner_season_rosters.csv")
    w = D.weekly()
    post = pd.read_csv(PROCESSED / "owner_season_posture.csv")
    slots = pd.read_csv(PROCESSED / "val_picks_slots.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).drop_duplicates("canonical_id").set_index("canonical_id")
    return dict(lv=lv, curve=curve, h=h, w=w, post=post, slots=slots, ids=ids, delta=delta,
                tr=D.trades(), waiver=_waiver_rows([0]))


def _streaks(w: pd.DataFrame) -> pd.DataFrame:
    """For every player-season-week: his team's games he missed in a row just before that week, and the
    share of his team's remaining games he went on to play (the known-injury signal before 2021)."""
    tg = w[w.pos != "DEF"].groupby(["season", "team"]).week.apply(lambda v: sorted(set(v)))
    played = w.groupby(["season", "player_id"]).agg(team=("team", "last"), weeks=("week", lambda v: set(v)))
    rows = []
    for (s, pid), r in played.iterrows():
        games = tg.get((s, r.team), [])
        miss = 0
        for i, g in enumerate(games):
            rest = games[i:]
            rows.append((s, pid, g, miss, np.mean([x in r.weeks for x in rest])))
            miss = 0 if g in r.weeks else miss + 1
    return pd.DataFrame(rows, columns=["season", "player_id", "week", "missed_in_a_row", "share_rest_played"])


def _availability(c) -> pd.Series:
    """Share of remaining games played, by games missed in a row (capped at 4), weeks 4-14, 2014-2025."""
    if "avail" not in c:
        st = _streaks(c["w"])
        c["streaks"] = st.set_index(["season", "player_id", "week"]).missed_in_a_row
        m = st[st.week.between(4, 14)]
        c["avail"] = m.groupby(m.missed_in_a_row.clip(upper=4)).share_rest_played.mean()
    return c["avail"]


def _rate(c, season: int, week: int, pids) -> pd.DataFrame:
    """Expected points per game before `week` (the latest row at or before it), times the share of the
    remaining games a player with his current run of missed games goes on to play, and position."""
    w = c["w"]
    x = w[(w.season == season) & (w.week <= week) & w.player_id.isin(list(pids))].sort_values("week")
    last = x.groupby("player_id").agg(rate=("expected", "last"), pos=("pos", "first"))
    if week > 1:
        av = _availability(c)
        k = c["streaks"]
        miss = [int(k.get((season, p, week), k.get((season, p, week + 1), 0))) for p in last.index]
        base = float(av.get(0, 1.0))
        last["missed_in_a_row"] = miss
        last["rate"] = last.rate * [float(av.get(min(m, 4), base)) / base for m in miss]
    # players with no game yet this season: last season's rate
    miss = set(pids) - set(last.index)
    if miss:
        p = w[(w.season == season - 1) & w.player_id.isin(list(miss))].groupby("player_id").agg(
            rate=("points", "mean"), pos=("pos", "first"))
        last = pd.concat([last, p])
    return last


def _lineup(c, roster: set[str], rates: pd.DataFrame) -> float:
    g = rates.loc[rates.index.intersection(list(roster))][["rate", "pos"]].rename(columns={"rate": 0})
    return lineup_points(pd.concat([g, c["waiver"]]))


def _contract(c, pid: str, season: int, salary: float) -> list[float | None]:
    """Salary in each of the next YEARS_AHEAD seasons while the contract set at signing runs, else None."""
    h = c["h"]
    out = []
    sched = salary_schedule(int(round(salary)), 4) if salary and salary > 0 else None
    for k in range(1, YEARS_AHEAD + 1):
        s = h[(h.season == season + k) & (h.player_id == pid) & (h.w0 == 1)].salary
        nxt = None
        if len(s) and sched is not None:
            v = float(s.iloc[0])
            # on the same schedule (raises of 5-15%, at least $1) means the contract was still running
            if v <= max(salary * 1.2, salary + k + 1) and v >= salary:
                nxt = v
        out.append(nxt)
        if nxt is None:
            break
    return out + [None] * (YEARS_AHEAD - len(out))


def _future(c, pid: str, pos: str, rate: float, season: int, salary: float, weight: float) -> float:
    """Payout $ of holding him the next seasons (expected lineup points over waiver - what his salary buys)."""
    if not pos or np.isnan(rate):
        return 0.0
    born = pd.to_datetime(c["ids"].birthdate.get(pid), errors="coerce")
    age = (season - born.year) if pd.notna(born) else 26
    total, ppg = 0.0, rate
    for k, sal in enumerate(_contract(c, pid, season, salary), 1):
        ppg = max(0.0, ppg + float(c["delta"](pd.Series([pos]), pd.Series([float(age + k - 1)])).iloc[0]))
        if sal is None:
            break
        val = float(c["lv"]([pos], [ppg * GAMES])[0]) - float(c["curve"]([pos], [sal])[0])
        dead = 0.75 * sal * c["curve"].slope            # cutting him instead: a year of dead cap at 75%
        total += weight * HAIRCUT ** k * max(val, -dead)
    return total


def _pick_points(c, asset: str, year, rnd) -> tuple[float, str]:
    s = c["slots"]
    m = re.search(r"#\s*(\d+)", str(asset))
    cond = 0.5 if re.search(r"\bif\b", str(asset)) else 1.0
    if m:
        n = int(m.group(1))
        ov = n if (n > 10 or not rnd or rnd == 1) else (int(rnd) - 1) * 10 + n
        return cond * float(np.interp(ov, s.overall, s.use_lineup)), f"slot {ov}" + (" (conditional, half)" if cond < 1 else "")
    if pd.notna(rnd):
        return cond * float(s[s["round"] == int(rnd)].use_lineup.mean()), "round average" + (" (conditional, half)" if cond < 1 else "")
    if re.search(r"1st/2nd", str(asset)):
        return float(s[s.overall.between(7, 16)].use_lineup.mean()), "slots 7-16 average"
    return 0.0, "unreadable"


def evaluate(c, tid: str) -> pd.DataFrame:
    a = pd.read_csv(PROCESSED / "hist_trade_assets.csv")
    g = a[a.trade_id == tid]
    y0 = int(g.season.iloc[0])
    teams = sorted(set(g.receiver) | set(g.sender))
    nweeks = 18 if y0 >= 2021 else 17
    tr = c["tr"][c["tr"].trade_id == tid]
    in_season = bool(len(tr) and tr.in_season.any())
    week = nweeks // 2 + 1 if in_season else 1
    h = c["h"]
    post = c["post"].set_index(["season", "owner"])
    lg = c["post"][c["post"].season == y0].per_point.mean()
    held = h[(h.season == y0) & (h.w0 <= week) & (h.w1 >= week)]
    players = g[(g.kind == "player") & g.player_id.notna()]
    moved = set(players.player_id)
    # rosters just before the trade: the receiver's stint begins at the trade, so swap back
    roster = {o: set(held[held.owner == o].player_id) for o in teams}
    for r in players.itertuples():
        roster[r.receiver].discard(r.player_id)
        roster[r.sender].add(r.player_id)
    allp = set().union(*roster.values()) | moved
    rates = _rate(c, y0, week, allp)
    sal = h[(h.season == y0) & h.player_id.isin(list(moved))].groupby("player_id").salary.max()
    rows = []
    for o in teams:
        other = [t for t in teams if t != o][0]
        gets = players[players.receiver == o].player_id.tolist()
        gives = players[players.sender == o].player_id.tolist()
        r0, r1 = roster[o], (roster[o] - set(gives)) | set(gets)
        n_left = nweeks - week + 1
        lineup = (_lineup(c, r1, rates) - _lineup(c, r0, rates)) * n_left
        charge = 0.0
        if not in_season:
            for p, sign in [(p, 1) for p in gets] + [(p, -1) for p in gives]:
                pos = rates.pos.get(p)
                if pos and pd.notna(sal.get(p)):
                    charge += sign * float(c["curve"]([pos], [float(sal[p])])[0])
        wt = float(post.per_point_mid.get((y0, o), lg) if in_season else post.per_point.get((y0, o), lg))
        now = wt * (lineup - charge)
        later = 0.0
        for p, sign in [(p, 1) for p in gets] + [(p, -1) for p in gives]:
            later += sign * _future(c, p, rates.pos.get(p), float(rates.rate.get(p, np.nan)), y0,
                                    float(sal.get(p, 0) or 0), lg)
        picks_txt, pk = [], 0.0
        for r in g[g.kind == "pick"].itertuples():
            v, how = _pick_points(c, r.asset, r.pick_year, r.pick_round)
            yr = int(r.pick_year) if pd.notna(r.pick_year) else y0 + 1
            v *= lg * HAIRCUT ** max(0, yr - y0)
            pk += v if r.receiver == o else -v
            if r.receiver == o:
                picks_txt.append(f"{r.asset} ({how})")
        cap = 0.0
        for r in g[g.kind == "cap"].itertuples():
            m = re.search(r"(\d{4})", str(r.asset))
            yr = int(m.group(1)) if m else y0
            if yr == y0 and in_season:
                continue                                  # in-season cap: close to nothing
            v = float(r.cap or 0) * c["curve"].slope * lg * HAIRCUT ** max(0, yr - y0)
            cap += v if r.receiver == o else -v
        rows.append(dict(trade_id=tid, season=y0, timing="in-season (assumed midseason)" if in_season else "offseason",
                         owner=o, partner=other, lineup_points_now=round(lineup, 1), salary_charge_now=round(charge, 1),
                         per_point_now=round(wt, 3), payout_now=round(now, 0), payout_players_later=round(later, 0),
                         payout_picks=round(pk, 0), payout_cap=round(cap, 0),
                         payout=round(now + later + pk + cap, 0), picks_got="; ".join(picks_txt)))
    return pd.DataFrame(rows)


def review() -> pd.DataFrame:
    c = _context()
    sample = pd.read_csv(PROCESSED / "decide_trade_sample.csv")
    out = pd.concat([evaluate(c, t) for t in sample.trade_id], ignore_index=True)
    grades = pd.read_csv(PROCESSED / "grades_decisions.csv")
    grades = grades[grades.type == "trade"][["trade_id", "owner", "payout_grade"]]
    out = out.merge(grades, on=["trade_id", "owner"], how="left").rename(columns={"payout_grade": "outcome_payout_grade"})
    out.to_csv(PROCESSED / "decide_trades_review_history.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    pd.set_option("display.width", 250)
    pd.set_option("display.max_colwidth", 60)
    print(review().drop(columns=["picks_got"]).to_string(index=False))
