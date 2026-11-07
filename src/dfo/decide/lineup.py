"""Start/sit: the lineup that gives the best chance to win this week's matchup.

    PYTHONPATH=src python -m dfo.decide.lineup              # this week, Jordan: decide_lineup_<week>.csv/.md
    PYTHONPATH=src python -m dfo.decide.lineup --review     # exit test on Jordan's Weeks 4-7 lineups

The question is "which lineup is most likely to beat this week's opponent" (P16): weekly lineup
points, with each player's 80% range from the projection backtest. A team's score is treated as
normal: mean = sum of medians, variance = sum of per-player variances (sd from the 80% range,
(high - low) / 2.563); win probability = P(our score > theirs). When we are favored a steadier
lineup can be right; when we are behind a higher-ceiling one can. A close call is any bench player
whose swap into a slot changes the median by under 2 points; each one is shown with both numbers.

Players listed Out, on IR or on a bye are not started. A Questionable player counts at his chance of
playing (params_questionable.csv: fantasy starters listed Questionable 2021-2025 played 52% of the
time at QB, 74-80% elsewhere) times his projection, with the extra spread that brings, and is flagged
with the start to use if he is ruled out. A FantasyPros rank that rose or fell 20+ places since last week
is flagged to check the news before trusting it. Players whose game has already kicked off are locked: the
recommendation fills only the open slots and says so.
"""
from __future__ import annotations

import argparse
import datetime as dt
import itertools

import numpy as np
import pandas as pd
from scipy.stats import norm

from dfo.common import PROCESSED, ROOT, load_config

Z80 = 2.563
CLOSE = 2.0
OUT = {"Out", "IR", "PUP", "Suspended", "NA", "Doubtful"}


def slots() -> tuple[dict[str, int], list[str]]:
    r = load_config("league")["roster"]
    st = dict(r["starters"])
    return st, list(r["flex_eligible"])


def _sd(df: pd.DataFrame) -> pd.Series:
    return ((df.high - df.low) / Z80).clip(lower=0.5)


def best_lineup(pool: pd.DataFrame, opp_mu: float | None = None, opp_sd: float | None = None,
                locked: pd.DataFrame | None = None, per_pos: int = 6) -> pd.DataFrame:
    """Search every legal lineup of the top `per_pos` players at each position (by median). Score
    = win probability against the opponent if given, else the median total. `locked` rows are
    already in the lineup (slot column set)."""
    st, flex_ok = slots()
    pool = pool.copy()
    pool["sd"] = _sd(pool)
    locked = locked if locked is not None else pool.iloc[0:0].assign(slot="")
    need = dict(st)
    for s in locked.slot:
        need[s] -= 1
    avail = pool[~pool.player_id.isin(locked.player_id)]
    cand = {p: avail[avail.pos == p].nlargest(per_pos, "proj") for p in st if p != "FLEX"}
    base_mu, base_var = locked.proj.sum(), (locked.sd ** 2).sum()
    best, best_score = None, -np.inf
    choices = [list(itertools.combinations(cand[p].index, need[p])) if need[p] > 0 else [()] for p in cand]
    for combo in itertools.product(*choices):
        used = [i for c in combo for i in c]
        rest = avail.loc[avail.pos.isin(flex_ok) & ~avail.index.isin(used)].nlargest(per_pos, "proj")
        flex_opts = list(itertools.combinations(rest.index, need["FLEX"])) if need["FLEX"] > 0 else [()]
        for fx in flex_opts:
            idx = used + list(fx)
            mu = base_mu + pool.loc[idx, "proj"].sum()
            var = base_var + (pool.loc[idx, "sd"] ** 2).sum()
            score = norm.cdf((mu - opp_mu) / np.sqrt(var + opp_sd ** 2)) if opp_mu is not None else mu
            if score > best_score + 1e-12:
                best_score, best = score, (combo, fx)
    combo, fx = best
    rows = []
    for p, c in zip(cand, combo):
        for i in c:
            rows.append(pool.loc[i].to_dict() | {"slot": p})
    for i in fx:
        rows.append(pool.loc[i].to_dict() | {"slot": "FLEX"})
    out = pd.concat([locked, pd.DataFrame(rows)], ignore_index=True)
    order = {s: k for k, s in enumerate(["QB", "RB", "WR", "TE", "FLEX", "K", "DEF"])}
    return out.sort_values("slot", key=lambda s: s.map(order)).reset_index(drop=True)


def team_score(lineup: pd.DataFrame) -> tuple[float, float]:
    sd = lineup["sd"] if "sd" in lineup else _sd(lineup)
    return float(lineup.proj.sum()), float(np.sqrt((sd ** 2).sum()))


def win_prob(mu, sd, opp_mu, opp_sd) -> float:
    return float(norm.cdf((mu - opp_mu) / np.sqrt(sd ** 2 + opp_sd ** 2)))


def eligible(df: pd.DataFrame) -> pd.DataFrame:
    """Startable players, with Questionable ones at their chance of playing."""
    status = df.injury_status.fillna("").astype(str)
    out = df[~df.bye.astype(bool) & ~status.isin(OUT)].copy()
    out[["proj", "low", "high"]] = out[["proj", "low", "high"]].astype(float)
    q = out.injury_status.fillna("").eq("Questionable")
    if q.any():
        play = pd.read_csv(PROCESSED / "params_questionable.csv").set_index("position")["mean"]
        pr = out.loc[q, "pos"].map(play).fillna(0.74)
        mu, sd = out.loc[q, "proj"], _sd(out.loc[q])
        new_mu = pr * mu
        new_sd = np.sqrt(pr * (sd ** 2 + mu ** 2) - new_mu ** 2)
        out.loc[q, "proj"] = new_mu.round(2)
        out.loc[q, "low"] = (new_mu - Z80 / 2 * new_sd).clip(lower=0)
        out.loc[q, "high"] = out.loc[q, "low"] + Z80 * new_sd
    return out


def close_calls(lineup: pd.DataFrame, pool: pd.DataFrame, opp_mu, opp_sd) -> pd.DataFrame:
    st, flex_ok = slots()
    mu, sd = team_score(lineup)
    base = win_prob(mu, sd, opp_mu, opp_sd)
    bench = pool[~pool.player_id.isin(lineup.player_id)].copy()
    bench["sd"] = _sd(bench)
    rows = []
    lk = lineup["locked"].fillna(False).astype(bool) if "locked" in lineup else pd.Series(False, index=lineup.index)
    for _, s in lineup[~lk].iterrows():
        ok = flex_ok if s.slot == "FLEX" else [s.slot]
        for _, b in bench[bench.pos.isin(ok)].iterrows():
            d_mu = b.proj - s.proj
            if abs(d_mu) <= CLOSE or b.proj > s.proj:
                new_sd = np.sqrt(sd ** 2 - s.sd ** 2 + b.sd ** 2)
                rows.append(dict(slot=s.slot, starter=s.player, starter_proj=s.proj, bench=b.player, bench_proj=b.proj,
                                 median_change=round(d_mu, 1),
                                 win_prob_change=round(win_prob(mu + d_mu, new_sd, opp_mu, opp_sd) - base, 3)))
    return pd.DataFrame(rows)


def rank_jumps(week: int, ids, places: float = 20) -> pd.DataFrame:
    """Players whose FantasyPros positional rank rose or fell `places`+ from last week: often injury news
    the rank caught before the projection inputs did (Chase went WR3 -> WR45 in Week 5 and played)."""
    e = pd.read_csv(PROCESSED / "ecr_weekly_recent.csv")
    e = e[(e.season == 2026) & e.player_id.isin(set(ids))]
    a = e[e.week == week].set_index("player_id").ecr
    b = e[e.week == week - 1].set_index("player_id").ecr
    d = (a - b).dropna()
    return d[d.abs() >= places].rename("rank_change").reset_index()


def kicked_off(week: int, now: dt.datetime | None = None) -> set[str]:
    from dfo.projections.features import implied_totals
    now = now or dt.datetime.now(dt.timezone.utc)
    imp = implied_totals([2026])
    w = imp[imp.week == week]
    played = w[pd.to_datetime(w.gameday).dt.date < (now - dt.timedelta(hours=6)).date()]
    return set(played.team)


def recommend(owner: str = "Jordan", proj_path=PROCESSED / "proj_week.csv", locked_starts: list[str] | None = None):
    """This week's lineup for `owner`. locked_starts: canonical ids already started in games that
    have kicked off (Jordan tells us; unknown otherwise)."""
    p = pd.read_csv(proj_path)
    p["injury_status"] = p.injury_status.fillna("")
    week = int(p.week.iloc[0])
    res = pd.read_csv(PROCESSED / "results_2026.csv")
    g = res[(res.week == week) & ((res.team_a == owner) | (res.team_b == owner))].iloc[0]
    opp = g.team_b if g.team_a == owner else g.team_a
    gone = kicked_off(week)
    mine = p[p.owner == owner].copy()
    theirs = eligible(p[p.owner == opp])
    opp_line = best_lineup(theirs)
    o_mu, o_sd = team_score(opp_line)
    mine["started_already"] = mine.team.isin(gone)
    locked = mine[mine.player_id.isin(locked_starts or [])].copy()
    pool = eligible(mine[~mine.started_already | mine.player_id.isin(locked.player_id)])
    if len(locked):
        locked["sd"] = _sd(locked)
        # a game already played counts at its actual points, with no spread left
        pts = pd.read_csv(PROCESSED / f"points_{int(p.season.iloc[0])}.csv")
        done = pts[(pts.season_type == "REG") & (pts.week == week)].groupby("player_id").points.sum()
        got = locked.player_id.map(done)
        locked.loc[got.notna(), ["proj", "low", "high"]] = np.repeat(got[got.notna()].to_numpy()[:, None], 3, axis=1)
        locked.loc[got.notna(), "sd"] = 0.0
        locked["locked"] = True
        locked["slot"] = locked.pos      # a locked FLEX should be passed with slot set by the caller
    line = best_lineup(pool, o_mu, o_sd, locked=locked if len(locked) else None)
    mu, sd = team_score(line)
    calls = close_calls(line, pool, o_mu, o_sd)
    q = mine[mine.injury_status.isin(["Questionable"]) & mine.player_id.isin(line.player_id)]
    backups = []
    for _, x in q.iterrows():
        alt = best_lineup(pool[pool.player_id != x.player_id], o_mu, o_sd)
        add = alt[~alt.player_id.isin(line.player_id)]
        backups.append(dict(questionable=x.player, start_instead=", ".join(add.player)))
    by_median = best_lineup(pool)
    jumps = rank_jumps(week, mine.player_id)
    return dict(week=week, opponent=opp, lineup=line, mu=mu, sd=sd, opp_mu=o_mu, opp_sd=o_sd,
                win_prob=win_prob(mu, sd, o_mu, o_sd), opp_lineup=opp_line, close_calls=calls,
                questionable=pd.DataFrame(backups), kicked_off=sorted(gone),
                thursday=mine[mine.started_already][["player", "pos", "team", "proj"]],
                same_as_median=set(by_median.player_id) == set(line.player_id), rank_jumps=jumps,
                made_at=str(p.made_at.iloc[0]), stats_as_of=str(p.stats_as_of.iloc[0]))


def to_markdown(r: dict) -> str:
    L = [f"# Start/sit, Week {r['week']} vs {r['opponent']}", "",
         f"Projections made {r['made_at']} (stats through {r['stats_as_of']}).",
         f"Win probability {r['win_prob']:.0%}: our median {r['mu']:.1f} (sd {r['sd']:.1f}) vs "
         f"{r['opponent']} {r['opp_mu']:.1f} (sd {r['opp_sd']:.1f}).", ""]
    L += ["| Slot | Player | Team | Median | 80% range | Status |", "|---|---|---|---|---|---|"]
    for _, x in r["lineup"].iterrows():
        L.append(f"| {x.slot} | {x.player} | {x.team} vs {x.opp} | {x.proj:.1f} | {x.low:.0f}-{x.high:.0f} | "
                 f"{x.injury_status or ''}{' locked' if x.get('locked') is True else ''} |")
    if len(r["thursday"]):
        L += ["", "Already played (Thursday): " + ", ".join(f"{x.player} ({x.pos})" for _, x in r["thursday"].iterrows())
              + ". Their points count only if you started them; tell me who you started and the rest re-optimizes around them."]
    if len(r.get("rank_jumps", [])):
        names = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str).set_index("canonical_id").name
        L += ["", "Check the news first: " + ", ".join(f"{names.get(x.player_id, x.player_id)} (FantasyPros rank "
                                                       f"{'fell' if x.rank_change > 0 else 'rose'} "
                                                       f"{abs(x.rank_change):.0f} places since last week)"
                                                       for _, x in r["rank_jumps"].iterrows())]
    if len(r["questionable"]):
        L += ["", "Questionable starters:"] + [f"- {x.questionable}: if ruled out, start {x.start_instead}"
                                              for _, x in r["questionable"].iterrows()]
    c = r["close_calls"]
    if len(c):
        L += ["", "Close calls (swap the bench player in):", "",
              "| Slot | Starter | Bench | Median change | Win prob change |", "|---|---|---|---|---|"]
        for _, x in c.sort_values("win_prob_change", ascending=False).iterrows():
            L.append(f"| {x.slot} | {x.starter} ({x.starter_proj:.1f}) | {x.bench} ({x.bench_proj:.1f}) | "
                     f"{x.median_change:+.1f} | {x.win_prob_change:+.1%} |")
    if not r["same_as_median"]:
        L += ["", "The win-probability lineup differs from the highest-median lineup: the matchup favors a "
                  "different risk profile this week."]
    return "\n".join(L)


# ---------------------------------------------------------------- exit test on past weeks
def project_past(weeks: list[int], players: pd.DataFrame) -> pd.DataFrame:
    """Projections for past 2026 weeks, each made only from data before it (ranks scraped before that
    Sunday, stats through the week before). `players`: week, player_id, pos, team."""
    from dfo.projections.features import build
    from dfo.projections.weekly import RangeMap, WeeklyModel
    extra = players.assign(season=2026)[["season", "week", "player_id", "pos", "team"]]
    df = build(extra=extra)
    rm = RangeMap.from_backtest()
    out = []
    for w in weeks:
        train = df[(df.season < 2026) | ((df.season == 2026) & (df.week < w))]
        ids = set(players[players.week == w].player_id)
        wk = df[(df.season == 2026) & (df.week == w) & df.player_id.isin(ids)].copy()
        p = WeeklyModel(train).predict(wk, rm)
        wk = wk.join(p[["proj", "low", "high"]])
        wk["bye"] = wk.implied.isna()
        wk.loc[wk.bye, ["proj", "low", "high"]] = 0.0
        out.append(wk[["week", "player_id", "pos", "team", "opp", "proj", "low", "high", "bye", "ecr"]])
    return pd.concat(out, ignore_index=True)


def review(weeks=(4, 5, 6, 7, 8)) -> pd.DataFrame:
    """Jordan's real lineups (screenshots) against the module's pick, both scored with what happened."""
    lu = pd.read_csv(PROCESSED / "jordan_lineups_2026.csv")    # transcribed from the Yahoo screenshots
    rows, detail = [], []
    lu = lu[lu.week.isin(weeks) & lu.player_id.notna()]
    allp = project_past(list(weeks), lu[["week", "player_id", "pos", "team"]])
    for w in weeks:
        g = lu[lu.week == w].copy()
        pr = allp[allp.week == w]
        x = g.merge(pr.drop(columns=["pos", "team", "week"]), on="player_id", how="left")
        x["proj"] = x.proj.fillna(0.0)
        x["low"], x["high"] = x.low.fillna(0.0), x.high.fillna(0.0)
        x["bye"] = x.bye.fillna(True).astype(bool)
        x["injury_status"] = np.where(x.slot == "IR", "IR", x.status.fillna(""))
        pool = eligible(x)
        mod = best_lineup(pool)
        actual = x[~x.slot.isin(["BN", "IR"])]
        hind = best_lineup(pool.assign(proj=pool.points, low=pool.points, high=pool.points + 1))
        rows.append(dict(week=w, jordan_points=round(actual.points.sum(), 2),
                         module_points=round(x.set_index("player_id").loc[mod.player_id].points.sum(), 2),
                         best_possible=round(x.set_index("player_id").loc[hind.player_id].points.sum(), 2),
                         jordan_yahoo_proj=round(actual.yahoo_proj.sum(), 1),
                         module_median=round(mod.proj.sum(), 1),
                         jordan_median=round(x.set_index("player_id").loc[actual.player_id].proj.sum(), 1),
                         differences=len(set(mod.player_id) - set(actual.player_id))))
        for pid in set(mod.player_id) ^ set(actual.player_id):
            r = x.set_index("player_id").loc[pid]
            detail.append(dict(week=w, player=r.player, pos=r.pos, module_starts=pid in set(mod.player_id),
                               jordan_starts=pid in set(actual.player_id), our_median=round(r.proj, 1),
                               fp_rank=r.ecr, yahoo_proj=r.yahoo_proj, points=r.points))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "decide_lineup_review.csv", index=False)
    pd.DataFrame(detail).to_csv(PROCESSED / "decide_lineup_review_detail.csv", index=False)
    return out


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    ap = argparse.ArgumentParser()
    ap.add_argument("--review", action="store_true")
    ap.add_argument("--started", nargs="*", default=[],
                    help="players Jordan already started in games that have kicked off (names as in proj_week)")
    a = ap.parse_args()
    if a.review:
        print(review().to_string(index=False))
        print(pd.read_csv(PROCESSED / "decide_lineup_review_detail.csv").to_string(index=False))
    else:
        pw = pd.read_csv(PROCESSED / "proj_week.csv")
        ids = pw[pw.player.isin(a.started)].player_id.tolist()
        missing = set(a.started) - set(pw.player)
        if missing:
            raise SystemExit(f"not in proj_week: {sorted(missing)}")
        r = recommend(locked_starts=ids)
        md = to_markdown(r)
        (PROCESSED / f"decide_lineup_w{r['week']}.md").write_text(md)
        r["lineup"].to_csv(PROCESSED / f"decide_lineup_w{r['week']}.csv", index=False)
        print(md)
