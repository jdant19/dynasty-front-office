"""Future seasons (2027-2029): each team's strength, and what it is worth in payout odds.

    PYTHONPATH=src python -m dfo.sim.future backtest   # preseason strength vs actual finishes 2020-2025
    PYTHONPATH=src python -m dfo.sim.future            # 2027-2029 odds for all ten teams

Strength = expected starting-lineup points for the season (QB, 2 RB, 3 WR, TE, FLEX, K, DEF):
  - players under contract that season, at their projected points (prices.csv: calibrated, aged);
    empty or weak slots filled at the market replacement level (what $1-2 buys in August; K/DEF at
    streaming level)
  - plus what the team's remaining cap buys at that August's auction: cap left after salaries, dead
    money, traded cap, rookie salaries and $1 fillers, at that auction's clearing price: all teams'
    room divided by the points over the market level of every player not under contract that season
    (2027 comes out at $1.06 a point, near the $1.14 fit; later years rise as contracts run out and
    money piles up)
  - plus rookies from the picks it owns (points by round from this league's drafts)
A season is then played out: each team's strength drawn around its expectation (spread from how far
future projections miss), weekly scores around strength/18 (week-to-week spread from 2026's actual
scores), a double round robin, then the playoffs among the top four (chance of winning them from
the 2026 simulator: playoff teams win in proportion to exp(beta x weekly strength)).
"""
from __future__ import annotations

import datetime as dt
import json
import sys

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, ROOT, load_config
from dfo.valuation.auction import market_params
from dfo.valuation.expected import ExpectedValue, rank_for, season_totals, streaming_level

FUTURE = [2027, 2028, 2029]
SLOTS = {"QB": 1, "RB": 2, "WR": 3, "TE": 1, "K": 1, "DEF": 1}
FLEX_POS = ("RB", "WR", "TE")
WEEKLY_SD = 22.1          # within-team sd of 2026 weekly scores, weeks 1-7 (results_2026.csv)
PARAMS = PROCESSED / "params_future_sim.csv"


def lineup_points(players: pd.DataFrame, repl: dict[str, float]) -> float:
    """Best lineup's season points; slots without a better player get the replacement level."""
    total, used = 0.0, set()
    for pos, k in SLOTS.items():
        g = players[players.pos == pos].sort_values("points", ascending=False)
        vals = list(g.points.head(k))
        used |= set(g.index[:k])
        vals += [repl[pos]] * (k - len(vals))
        total += sum(max(v, repl[pos]) for v in vals)
    flex = players[players.pos.isin(FLEX_POS) & ~players.index.isin(used)].points.max()
    best_repl = max(repl[p] for p in FLEX_POS)
    return total + max(flex if pd.notna(flex) else 0.0, best_repl)


def play_seasons(strength: np.ndarray, sd: np.ndarray, beta: float, n: int, rng) -> dict:
    """strength, sd: (teams,) season points. Returns ranks (n, teams) and playoff winners/second."""
    t = len(strength)
    S = rng.normal(strength, sd, (n, t))
    pairs = [(a, b) for a in range(t) for b in range(a + 1, t)] * 2           # double round robin
    wins = np.zeros((n, t))
    pf = np.zeros((n, t))
    for a, b in pairs:
        sa = rng.normal(S[:, a] / 18, WEEKLY_SD)
        sb = rng.normal(S[:, b] / 18, WEEKLY_SD)
        wins[:, a] += sa > sb
        wins[:, b] += sb > sa
        pf[:, a] += sa
        pf[:, b] += sb
    key = wins * 1e5 + pf
    rank = (-key).argsort(axis=1).argsort(axis=1) + 1
    champ = np.zeros((n, t), dtype=int)
    top4 = rank <= 4
    w = np.where(top4, np.exp(beta * S / 18), 0.0)
    p = w / w.sum(axis=1, keepdims=True)
    u = rng.random(n)
    first = (p.cumsum(axis=1) > u[:, None]).argmax(axis=1)
    champ[np.arange(n), first] = 1
    p2 = p.copy()
    p2[np.arange(n), first] = 0
    p2 = p2 / p2.sum(axis=1, keepdims=True)
    second = (p2.cumsum(axis=1) > rng.random(n)[:, None]).argmax(axis=1)
    champ[np.arange(n), second] = 2
    return dict(rank=rank, playoff=champ)


def payouts(res: dict) -> np.ndarray:
    pay = load_config("league")["payouts"]
    reg = sum(pay["regular_season"].get(k, 0) * (res["rank"] == k) for k in (1, 2, 3))
    po = pay["playoffs"][1] * (res["playoff"] == 1) + pay["playoffs"][2] * (res["playoff"] == 2)
    return (reg + po).mean(axis=0)


def fit_beta() -> float:
    """Playoff win odds vs weekly strength, from the 2026 simulator (keeps, draft, NFL bracket)."""
    from dfo.sim import playoffs, season
    st = season.load_state()
    s = season.simulate(3000, state=st, return_detail=True)
    place = playoffs.simulate(s["rank"], s["owners"])
    m = s["weekly"].mean(axis=(0, 1))                          # each team's mean weekly score
    best, best_ll = 0.0, -np.inf
    for beta in np.linspace(0, 0.2, 81):
        ll = 0.0
        for i in range(s["rank"].shape[0]):
            four = np.where(s["rank"][i] <= 4)[0]
            win = np.where(place[i] == 1)[0]
            if len(win) != 1:
                continue
            z = beta * m[four]
            ll += beta * m[win[0]] - (np.log(np.exp(z - z.max()).sum()) + z.max())
        if ll > best_ll:
            best, best_ll = beta, ll
    pd.DataFrame([dict(beta=best, weekly_sd=WEEKLY_SD, fit_on="2026 simulator (3000 seasons)",
                       fit_date=dt.date.today().isoformat())]).to_csv(PARAMS, index=False)
    return best


def beta() -> float:
    return float(pd.read_csv(PARAMS).beta.iloc[0])


# ------------------------------------------------------------------ backtest on past seasons
def opening_rosters() -> pd.DataFrame:
    """Every team's roster as each season opened: kept players, auction buys, rookie draft."""
    a = pd.read_csv(PROCESSED / "auctions.csv")
    nm = pd.read_csv(PROCESSED / "name_matches.csv")
    am = nm[nm.table == "auctions"][["season", "name", "canonical_id"]]
    a = a.merge(am, left_on=["season", "player"], right_on=["season", "name"], how="left")
    r = pd.read_csv(PROCESSED / "rookie_drafts.csv")
    rm = nm[nm.table == "rookie_drafts"][["season", "name", "canonical_id"]]
    r = r.merge(rm, left_on=["season", "player"], right_on=["season", "name"], how="left")
    out = pd.concat([a[["season", "owner", "canonical_id"]], r[["season", "owner", "canonical_id"]]])
    return out.dropna().rename(columns={"canonical_id": "player_id"}).drop_duplicates(["season", "player_id"])


def backtest(n: int = 4000) -> pd.DataFrame:
    shift, _, _ = market_params()
    b = beta()
    rost = opening_rosters()
    res = pd.read_csv(PROCESSED / "season_results.csv")
    from dfo.valuation.expected import ranks
    rk = ranks("auction")
    rng = np.random.default_rng(5)
    rows = []
    for s in range(2020, 2026):
        ev = ExpectedValue("auction", exclude_season=s, shift=shift, horizons=(0,))
        repl = dict(ev.replacement[0])
        x = rost[rost.season == s].merge(rk[rk.season == s][["player_id", "pos", "redraft_rank", "dynasty_rank"]],
                                         on="player_id", how="left")
        x = x[x.pos.notna()]
        x["points"] = ev.predict(x.pos, rank_for(x, 0), 0).points
        owners = sorted(res[res.season == s].owner)
        strength = np.array([lineup_points(x[x.owner == o].dropna(subset=["points"]), repl) for o in owners])
        sd = np.full(len(owners), 0.10 * strength.mean())        # opening-day strength known to about 10%
        sim = play_seasons(strength, sd, b, n, rng)
        act = res[res.season == s].set_index("owner")
        for j, o in enumerate(owners):
            rows.append(dict(season=s, owner=o, strength=round(strength[j], 1),
                             p_first=(sim["rank"][:, j] == 1).mean(), p_top4=(sim["rank"][:, j] <= 4).mean(),
                             p_champ=(sim["playoff"][:, j] == 1).mean(),
                             finish=int(act.loc[o, "regular_season_finish"]),
                             champ=act.loc[o, "playoff_result"] == "champion"))
    out = pd.DataFrame(rows)
    out.to_csv(PROCESSED / "sim_future_backtest.csv", index=False)
    return out


def backtest_summary(bt: pd.DataFrame) -> pd.DataFrame:
    from scipy.stats import spearmanr
    rows = []
    for name, p, y in [("regular-season 1st", bt.p_first, bt.finish == 1),
                       ("top 4", bt.p_top4, bt.finish <= 4),
                       ("playoff title", bt.p_champ, bt.champ)]:
        base = y.mean()
        rows.append(dict(event=name, team_seasons=len(bt), brier=round(float(((p - y) ** 2).mean()), 4),
                         brier_no_skill=round(float(((base - y) ** 2).mean()), 4),
                         mean_prob_when_it_happened=round(float(p[y].mean()), 3),
                         mean_prob_otherwise=round(float(p[~y].mean()), 3)))
    s = pd.DataFrame(rows)
    s["strength_vs_finish_rank_corr"] = round(float(np.mean([
        spearmanr(g.strength, -g.finish).statistic for _, g in bt.groupby("season")])), 3)
    s.to_csv(PROCESSED / "sim_future_backtest_summary.csv", index=False)
    return s


# ------------------------------------------------------------------ 2027-2029
def _cap_by_year() -> dict[str, dict[int, float]]:
    """Cap committed per owner and season: salaries, dead money, traded cap, rookie salaries."""
    prices = pd.read_csv(PROCESSED / "prices.csv")
    roster_owner = prices[prices.owner.fillna("") != ""]
    out: dict[str, dict[int, float]] = {}
    for o, g in roster_owner.groupby("owner"):
        out[o] = {y: float(g[f"salary_{y}"].sum()) for y in FUTURE}
    dead = pd.read_csv(PROCESSED / "site_dead_money.csv")
    for r in dead.itertuples():
        for i, v in enumerate(str(r.amounts).split()):
            y = int(r.start_year) + i
            if y in FUTURE:
                out.setdefault(r.owner, {}).setdefault(y, 0.0)
                out[r.owner][y] += float(v)
    team_owner = dict(zip(pd.read_csv(PROCESSED / "site_rosters.csv").team_id, pd.read_csv(PROCESSED / "site_rosters.csv").owner))
    t = json.load(open(ROOT / "data" / "snapshots" / "site" / "latest" / "traded_cap_space.json"))
    for r in (t["rows"] if isinstance(t, dict) else t):
        o = team_owner.get(r["team_id"])
        for i in range(5):
            y = int(r["salary_start_year"]) + i
            if o and y in FUTURE:
                out[o][y] = out[o].get(y, 0.0) - float(r.get(f"amount_year{i + 1}", 0) or 0)  # received cap = more room
    picks = pd.read_csv(PROCESSED / "site_picks.csv")
    sal = load_config("league")["contracts"]["rookie"]["salary_by_round"]
    for r in picks[~picks.used].itertuples():
        for y in FUTURE:
            if y >= r.draft_year and r.owner in out:
                out[r.owner][y] = out[r.owner].get(y, 0.0) + sal.get(int(r.round), 1) + (y - r.draft_year)
    return out


def strengths() -> pd.DataFrame:
    shift, lam, _ = market_params()
    ev = ExpectedValue("auction", shift=shift, horizons=(0,))
    repl = dict(ev.replacement[0])
    prices = pd.read_csv(PROCESSED / "prices.csv")
    cap = _cap_by_year()
    dist = pd.read_csv(PROCESSED / "val_picks_distribution.csv")
    by_round = {1: dist[dist.slots.str.startswith("1.")][["points_y1", "points_y2", "points_y3"]].mean(),
                2: dist[dist.slots.str.startswith("2.")][["points_y1", "points_y2", "points_y3"]].mean(),
                3: dist[dist.slots == "3rd"][["points_y1", "points_y2", "points_y3"]].mean()}
    picks = pd.read_csv(PROCESSED / "site_picks.csv")
    picks = picks[~picks.used]
    owners = sorted(prices.owner.dropna().loc[lambda s: s != ""].unique())
    spread = {}
    rows = []
    for k, y in enumerate(FUTURE, start=1):
        for o in owners:
            g = prices[(prices.owner == o) & (prices[f"salary_{y}"] > 0)][["pos", f"proj_points_{y}"]]
            g = g.rename(columns={f"proj_points_{y}": "points"}).dropna()
            base = lineup_points(g, repl)
            n_players = len(g)
            room = 300 - cap.get(o, {}).get(y, 0.0) - max(0, 18 - n_players)            # $1 fillers
            bought = max(room, 0.0)                                                   # converted below
            rookies = sum(by_round[int(r.round)].iloc[min(y - r.draft_year, 2)]
                          for r in picks[(picks.owner == o) & (picks.draft_year <= y)].itertuples()
                          if int(r.round) in by_round)
            # uncertainty: contracted starters' projection spread at this horizon, plus the auction part
            starters = g.sort_values("points", ascending=False).head(9)
            sd_players = np.sqrt(sum((0.12 + 0.05 * k) ** 2 * p ** 2 for p in starters.points))
            rows.append(dict(season=y, owner=o, contracted_players=n_players, lineup_points=round(base, 1),
                             cap_room=round(room), rookie_points=round(rookies, 1), _sd_players=sd_players))
    out = pd.DataFrame(rows)
    # each auction clears: all teams' room against the talent not under contract that season
    for y in FUTURE:
        pool = prices[(prices[f"salary_{y}"].fillna(0) == 0) & (prices[f"points_{y}"] > 0)][f"points_{y}"].sum()
        m = out.season == y
        money = out.loc[m, "cap_room"].clip(lower=0).sum()
        price = max(money / pool, 0.5) if pool > 0 else lam
        out.loc[m, "price_per_point"] = round(price, 3)
        out.loc[m, "points_bought"] = (out.loc[m, "cap_room"].clip(lower=0) / price).round(1)
    out["strength"] = (out.lineup_points + out.points_bought + out.rookie_points).round(1)
    out["strength_sd"] = np.sqrt(out._sd_players ** 2 + (0.15 * out.points_bought) ** 2 + 20 ** 2).round(1)
    return out.drop(columns="_sd_players")


def odds(st: pd.DataFrame | None = None, n: int = 8000, boost: dict | None = None, seed: int = 3) -> pd.DataFrame:
    st = strengths() if st is None else st
    b = beta()
    out = []
    for y, g in st.groupby("season"):
        g = g.reset_index(drop=True)
        s = g.strength.to_numpy() + np.array([(boost or {}).get((y, o), 0.0) for o in g.owner])
        sim = play_seasons(s, g.strength_sd.to_numpy(), b, n, np.random.default_rng(seed + y))
        pay = payouts(sim)
        for j, o in enumerate(g.owner):
            out.append(dict(season=y, owner=o, strength=g.strength[j], p_first=(sim["rank"][:, j] == 1).mean(),
                            p_top4=(sim["rank"][:, j] <= 4).mean(), p_champ=(sim["playoff"][:, j] == 1).mean(),
                            exp_payout=pay[j]))
    return pd.DataFrame(out)


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    if len(sys.argv) > 1 and sys.argv[1] == "backtest":
        bt = backtest()
        print(backtest_summary(bt).to_string(index=False))
        print(bt[bt.season == 2025].sort_values("strength", ascending=False).to_string(index=False))
    else:
        st = strengths()
        st.to_csv(PROCESSED / "sim_future_strength.csv", index=False)
        o = odds(st)
        o.to_csv(PROCESSED / "sim_future_odds.csv", index=False)
        print(st.to_string(index=False))
        print(o.round(3).to_string(index=False))
