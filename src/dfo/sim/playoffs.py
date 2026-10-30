"""League playoffs: keeps, the seed-order draft, and total points through the Super Bowl.

For the four teams in a simulated season, in seed order:
  keep   each keeps his own players (rosters as they stand; trades and waivers to come are not modeled)
  draft  a straight draft in seed order (the regular-season champion first) from every player not on
         a playoff roster, free agents included; each pick is the player who adds the most expected
         playoff points to that team's starting lineup; rounds continue while anyone gains
  score  each starter's points over his NFL team's playoff games (NFL bracket from nfl.py, same sim),
         a draw per game from history, a game missed at the regular injury rate; no bench
Highest total wins $200, second $100 (config/league.yaml). Injured starters score 0 here; the league
substitutes the next player on the NFL depth chart, so this understates the bottom of a team's range.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.sim import nfl
from dfo.sim.outcomes import Pools, absence
from dfo.valuation import calibrate


def player_table() -> pd.DataFrame:
    ros = pd.read_csv(PROCESSED / "proj_ros.csv")
    ros["rate"] = calibrate.shrink(ros.ros_ppg, ros.pos, "ros").clip(lower=0)
    roster = pd.read_csv(PROCESSED / "site_rosters.csv")
    ids = pd.read_csv(PROCESSED / "player_ids.csv", dtype=str)[["site_player_id", "canonical_id"]]
    roster = roster.merge(ids.rename(columns={"site_player_id": "player_id", "canonical_id": "pid"}),
                          on="player_id", how="left")
    owner = roster.dropna(subset=["pid"]).drop_duplicates("pid").set_index("pid").owner
    t = ros[["player_id", "player", "pos", "team", "rate"]].copy()
    t["owner"] = t.player_id.map(owner).fillna("")
    t["team"] = t.team.replace({"LAR": "LA", "JAC": "JAX", "WSH": "WAS"})
    ab = absence()
    t["avail"] = t.pos.map(lambda p: 1 - ab.get(p, (0.0, 0.0))[0] / (1 + ab.get(p, (0.0, 0.0))[0]
                                                                         - ab.get(p, (0.0, 0.0))[1]))
    return t


def _slots():
    lg = load_config("league")["roster"]
    return [(p, k) for p, k in lg["starters"].items() if p != "FLEX"], lg["starters"].get("FLEX", 0), set(lg["flex_eligible"])


def _best_lineup(idx: list[int], v: np.ndarray, pos: np.ndarray) -> tuple[float, list[int]]:
    fixed, n_flex, flex_pos = _slots_cache
    order = sorted(idx, key=lambda i: -v[i])
    used, total = [], 0.0
    for p, k in fixed:
        pick = [i for i in order if pos[i] == p][:k]
        used += pick
        total += sum(v[i] for i in pick)
    flex = [i for i in order if pos[i] in flex_pos and i not in used][:n_flex]
    return total + sum(v[i] for i in flex), used + flex


_slots_cache = None


def draft(seeds: tuple[str, ...], players: pd.DataFrame, exp_games: dict[str, float]) -> dict[str, list]:
    """Starting lineups (player row positions) after keeps and the seed-order draft."""
    global _slots_cache
    _slots_cache = _slots_cache or _slots()
    v = (players.rate * players.avail * players.team.map(exp_games).fillna(0)).to_numpy()
    pos = players.pos.to_numpy()
    owner = players.owner.to_numpy()
    rosters = {o: list(np.where(owner == o)[0]) for o in seeds}
    pool = [i for i in np.argsort(-v) if owner[i] not in seeds and v[i] > 0]
    # only the best few left at each position can matter to anyone
    def candidates():
        seen, out = {}, []
        for i in pool:
            if seen.get(pos[i], 0) < 3:
                seen[pos[i]] = seen.get(pos[i], 0) + 1
                out.append(i)
        return out
    for _ in range(9):
        gained = False
        for o in seeds:
            cur, _ = _best_lineup(rosters[o], v, pos)
            best, best_i = 0.0, None
            for i in candidates():
                gain = _best_lineup(rosters[o] + [i], v, pos)[0] - cur
                if gain > best + 1e-9:
                    best, best_i = gain, i
            if best_i is not None:
                rosters[o].append(best_i)
                pool.remove(best_i)
                gained = True
        if not gained:
            break
    return {o: _best_lineup(rosters[o], v, pos)[1] for o in seeds}


def simulate(rank: np.ndarray, owners: list[str], seed: int = 11, boost: dict[str, float] | None = None) -> pd.DataFrame:
    """rank: (sims, teams) final regular-season places. Returns per sim the playoff place (1, 2 or 0)
    of each team."""
    n = rank.shape[0]
    rng = np.random.default_rng(seed)
    teams, played = nfl.simulate(n, rng)
    tix = {t: i for i, t in enumerate(teams)}
    exp_games = dict(zip(teams, played.mean(axis=0)))
    players = player_table().reset_index(drop=True)
    pools = Pools()
    place = np.zeros((n, len(owners)), dtype=int)
    seeds_by_sim = [tuple(owners[j] for j in np.argsort(rank[s])[:4]) for s in range(n)]
    groups: dict[tuple, list[int]] = {}
    for s, t in enumerate(seeds_by_sim):
        groups.setdefault(t, []).append(s)
    oi = {o: j for j, o in enumerate(owners)}
    for seeds, sims in groups.items():
        sims = np.array(sims)
        lineups = draft(seeds, players, exp_games)
        totals = np.zeros((len(sims), 4))
        for k, o in enumerate(seeds):
            for i in lineups[o]:
                p = players.iloc[i]
                if p.team not in tix:
                    continue
                g = played[sims, tix[p.team]]                         # games his NFL team plays
                draws = pools.draw(p.pos, np.full((len(sims), 4), p.rate), late=False, played_only=True, rng=rng)
                plays = (rng.random((len(sims), 4)) < p.avail) & (np.arange(4) < g[:, None])
                totals[:, k] += (draws * plays).sum(axis=1)
        for k, o in enumerate(seeds):          # the dial's probe: extra points per week over ~2 playoff games
            totals[:, k] += 2 * (boost or {}).get(o, 0.0)
        order = np.argsort(-totals, axis=1)
        for k, o in enumerate(seeds):
            place[sims, oi[o]] = np.where(order[:, 0] == k, 1, np.where(order[:, 1] == k, 2, 0))
    return place


def summarize(place: np.ndarray, owners: list[str]) -> pd.DataFrame:
    pay = load_config("league")["payouts"]["playoffs"]
    return pd.DataFrame(dict(owner=owners, p_playoff_champ=(place == 1).mean(axis=0),
                             p_playoff_second=(place == 2).mean(axis=0),
                             exp_playoff_payout=pay[1] * (place == 1).mean(axis=0) + pay[2] * (place == 2).mean(axis=0)))
