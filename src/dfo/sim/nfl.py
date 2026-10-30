"""NFL rest of season and playoffs, to know how many playoff games each NFL team plays.

Team ratings come from the betting market: every 2026 game with a spread (played games and the
coming week) fit as spread = home rating - away rating + home field (ridge least squares). Remaining
games: margin ~ normal(rating difference + home field, 13.5). Seeding: division winners 1-4 by record,
three wild cards; ties broken by rating (a stand-in for the NFL's tiebreakers). Bracket: wild card
(2v7, 3v6, 4v5), divisional (1 plays the lowest seed left), conference title, Super Bowl (neutral).
"""
from __future__ import annotations

from functools import lru_cache

import numpy as np
import pandas as pd

from dfo.common import load_config
from dfo.sources.fetch import nflverse

SEASON = 2026


@lru_cache(maxsize=1)
def games() -> pd.DataFrame:
    g = pd.read_csv(nflverse("schedules", "games.csv"), low_memory=False)
    return g[(g.season == SEASON) & (g.game_type == "REG")].copy()


@lru_cache(maxsize=1)
def ratings() -> tuple[dict[str, float], float]:
    g = games()
    g = g[g.spread_line.notna()]
    teams = sorted(set(g.home_team) | set(g.away_team))
    ix = {t: i for i, t in enumerate(teams)}
    X = np.zeros((len(g), len(teams) + 1))
    for r, (h, a, loc) in enumerate(zip(g.home_team, g.away_team, g.location)):
        X[r, ix[h]] += 1
        X[r, ix[a]] -= 1
        X[r, -1] = 0.0 if str(loc) == "Neutral" else 1.0
    y = g.spread_line.to_numpy()
    lam = 1.0
    reg = np.eye(X.shape[1]) * lam
    reg[-1, -1] = 0
    beta = np.linalg.solve(X.T @ X + reg, X.T @ y)
    return {t: float(beta[ix[t]]) for t in teams}, float(beta[-1])


def simulate(n: int, rng: np.random.Generator) -> tuple[list[str], np.ndarray]:
    """(teams, playoff games played per sim and team: 0-4)."""
    cfg = load_config("nfl")
    rate, hfa = ratings()
    sd = cfg["game_margin_sd"]
    div = {t: d for d, ts in cfg["divisions"].items() for t in ts}
    teams = sorted(div)
    ix = {t: i for i, t in enumerate(teams)}
    g = games()
    wins = np.zeros((n, len(teams)))
    for r in g.itertuples():
        h, a = ix[r.home_team], ix[r.away_team]
        if pd.notna(r.home_score):
            hw = 1.0 if r.home_score > r.away_score else (0.5 if r.home_score == r.away_score else 0.0)
            wins[:, h] += hw
            wins[:, a] += 1 - hw
            continue
        mu = rate[r.home_team] - rate[r.away_team] + (0.0 if str(r.location) == "Neutral" else hfa)
        hw = (rng.normal(mu, sd, n) > 0).astype(float)
        wins[:, h] += hw
        wins[:, a] += 1 - hw
    R = np.array([rate[t] for t in teams])
    key = wins + 1e-3 * (R + rng.normal(0, 1, (n, len(teams))))   # rating as the tiebreak stand-in
    played = np.zeros((n, len(teams)), dtype=int)
    p_home = cfg["playoffs"]["home_field_points"]

    def game(i, j, home=True):
        mu = R[i] - R[j] + (p_home if home else 0.0)
        return rng.normal(mu, sd) > 0

    for s in range(n):
        champs = []
        for conf in ("AFC", "NFC"):
            divs = [d for d in cfg["divisions"] if d.startswith(conf)]
            winners = [max((ix[t] for t in cfg["divisions"][d]), key=lambda i: key[s, i]) for d in divs]
            winners.sort(key=lambda i: -key[s, i])
            rest = [ix[t] for d in divs for t in cfg["divisions"][d] if ix[t] not in winners]
            wild = sorted(rest, key=lambda i: -key[s, i])[:3]
            seeds = winners + wild                                # seed k = seeds[k-1]
            alive = [seeds[0]]
            for hi, lo in ((1, 6), (2, 5), (3, 4)):
                a, b = seeds[hi], seeds[lo]
                played[s, a] += 1
                played[s, b] += 1
                alive.append(a if game(a, b) else b)
            alive.sort(key=lambda i: seeds.index(i))
            nxt = []
            for a, b in ((alive[0], alive[3]), (alive[1], alive[2])):
                played[s, a] += 1
                played[s, b] += 1
                nxt.append(a if game(a, b) else b)
            a, b = sorted(nxt, key=lambda i: seeds.index(i))
            played[s, a] += 1
            played[s, b] += 1
            champs.append(a if game(a, b) else b)
        a, b = champs
        played[s, a] += 1
        played[s, b] += 1
    return teams, played


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    r, h = ratings()
    rng = np.random.default_rng(7)
    teams, played = simulate(5000, rng)
    out = pd.DataFrame(dict(team=teams, rating=[round(r[t], 1) for t in teams],
                            p_playoffs=(played > 0).mean(axis=0), exp_games=played.mean(axis=0).round(2),
                            p_super_bowl=(played >= 3).mean(axis=0))).sort_values("exp_games", ascending=False)
    print(f"home field {h:.2f}"); print(out.to_string(index=False))
