"""Playoff prep: who matters for the league playoffs, and what adding them now is worth.

    PYTHONPATH=src python -m dfo.decide.playoffs      # decide_playoffs.md / .csv

The league playoffs (config/league.yaml): the four playoff teams keep any of their own regular-season
players, then draft every other player in seed order (the regular-season champion first, no snake),
start one lineup with no bench, and score total points through the Super Bowl ($200 / $100).
So a player on Jordan's roster at the end of the regular season is a free keep, and a player on a
rival playoff team's roster is gone. Adding someone now matters when he would otherwise be kept by a
rival, or taken in the draft before Jordan's pick, or when he frees Jordan's draft picks for others.

- Playoff value of a player: his rest-of-season rate x his chance of being available x the playoff
  games his NFL team is expected to play (sim/nfl.py: market ratings, 4,000 seasons).
- Likely seeds: the simulator's most likely top four, in order of chance of finishing 1st.
- Jordan's keep-and-draft plan: keeps, the straight draft by the same rule the simulator uses, his
  starting lineup and its expected points against the other three.
- Adds: for each player not on a likely playoff team (free agents and the six other teams), the change
  in Jordan's title chance and expected playoff payout if he were on Jordan's roster at the end of the
  regular season (keeps and draft re-run, the playoffs simulated). Players on rival playoff teams are
  shown for what they would be worth, but come only by trade.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from dfo.common import PROCESSED, load_config
from dfo.sim import nfl
from dfo.sim import playoffs as P

ME = "Jordan"
N = 3000


def likely_seeds() -> list[str]:
    s = pd.read_csv(PROCESSED / "sim_2026.csv")
    s = s.sort_values(["p_top4", "p_first"], ascending=False).head(4)
    return list(s.sort_values("p_first", ascending=False).owner)


def exp_games(n: int = 4000) -> dict[str, float]:
    teams, played = nfl.simulate(n, np.random.default_rng(5))
    return dict(zip(teams, played.mean(axis=0))), dict(zip(teams, (played > 0).mean(axis=0)))


def title_odds(seeds: list[str], moves: dict[str, str] | None = None, n: int = N) -> pd.DataFrame:
    owners = list(pd.read_csv(PROCESSED / "sim_2026.csv").owner)
    rank = np.tile(np.array([seeds.index(o) + 1 if o in seeds else 10 for o in owners]), (n, 1))
    place = P.simulate(rank, owners, seed=31, moves=moves)
    return P.summarize(place, owners).set_index("owner").loc[seeds]


def run() -> str:
    seeds = likely_seeds()
    eg, mk = exp_games()
    players = P.player_table().reset_index(drop=True)
    players["exp_playoff_games"] = players.team.map(eg).fillna(0)
    players["p_nfl_playoffs"] = players.team.map(mk).fillna(0)
    players["playoff_value"] = players.rate * players.avail * players.exp_playoff_games
    lineups = P.draft(tuple(seeds), players, eg)
    base = title_odds(seeds)
    rows_lu = []
    for o in seeds:
        for i in lineups[o]:
            r = players.iloc[i]
            rows_lu.append(dict(team=o, player=r.player, pos=r.pos, nfl=r.team, how="keep" if r.owner == o else "draft",
                                playoff_value=round(r.playoff_value, 1), exp_games=round(r.exp_playoff_games, 2)))
    lu = pd.DataFrame(rows_lu)
    # candidates: the most valuable players not already Jordan's, best few per position
    cand = players[(players.owner != ME) & (players.playoff_value > 0)].sort_values("playoff_value", ascending=False)
    cand = cand.groupby("pos").head(8)
    adds = []
    for r in cand.itertuples():
        t = title_odds(seeds, moves={r.player_id: ME}, n=N)
        adds.append(dict(player=r.player, pos=r.pos, nfl=r.team, owner=r.owner or "free agent",
                         on_playoff_rival=r.owner in seeds and r.owner != ME,
                         playoff_value=round(r.playoff_value, 1), p_nfl_playoffs=round(r.p_nfl_playoffs, 2),
                         exp_games=round(r.exp_playoff_games, 2),
                         title_change=round(t.p_playoff_champ[ME] - base.p_playoff_champ[ME], 3),
                         payout_change=round(t.exp_playoff_payout[ME] - base.exp_playoff_payout[ME], 1)))
    adds = pd.DataFrame(adds).sort_values("payout_change", ascending=False)
    mine = players[players.owner == ME].sort_values("playoff_value", ascending=False)
    L = [f"# Playoff prep (built {pd.Timestamp.now(tz='UTC').date()})", "",
         f"Likely seeds: {', '.join(f'{k + 1}. {o}' for k, o in enumerate(seeds))} (simulator's most likely top four). "
         "Each keeps his own players, then drafts in seed order; one starting lineup, total points through the Super Bowl.", "",
         "| Team | Title | 2nd | Expected playoff payout | Lineup expected points |", "|---|---|---|---|---|"]
    for o in seeds:
        L.append(f"| {o} | {base.p_playoff_champ[o]:.0%} | {base.p_playoff_second[o]:.0%} | ${base.exp_playoff_payout[o]:.0f} | "
                 f"{lu[lu.team == o].playoff_value.sum():.0f} |")
    L += ["", f"## Your playoff lineup if the seeds hold", "", "| Player | Pos | NFL | Keep or draft | Expected playoff games | Expected points |",
          "|---|---|---|---|---|---|"]
    for r in lu[lu.team == ME].itertuples():
        L.append(f"| {r.player} | {r.pos} | {r.nfl} | {r.how} | {r.exp_games:.1f} | {r.playoff_value:.0f} |")
    L += ["", "Your players by playoff value (whose NFL teams go deep): " + ", ".join(
        f"{r.player} {r.playoff_value:.0f}" for r in mine.head(12).itertuples()) + ".", "",
          "## Who is worth adding before the end of the regular season", "",
          "Change in your title chance and expected playoff payout if he is on your roster at the end of Week 18 "
          "(keeps and draft re-run). Rival playoff teams' players come only by trade.", "",
          "| Player | Pos | NFL | Now with | NFL playoff chance | Expected games | Title change | Payout change |",
          "|---|---|---|---|---|---|---|---|"]
    for r in adds[adds.payout_change > 0.5].head(15).itertuples():
        L.append(f"| {r.player} | {r.pos} | {r.nfl} | {r.owner}{' (playoff rival)' if r.on_playoff_rival else ''} | "
                 f"{r.p_nfl_playoffs:.0%} | {r.exp_games:.1f} | {r.title_change:+.1%} | ${r.payout_change:+.0f} |")
    L += ["", "Known limits: the seeds are the most likely four in the most likely order; trades and waivers by other "
          "teams before Week 18 are not modeled; the league's injury replacement (next player on the depth chart) "
          "is not, so injured starters score 0 here."]
    md = "\n".join(L)
    (PROCESSED / "decide_playoffs.md").write_text(md)
    adds.to_csv(PROCESSED / "decide_playoffs_adds.csv", index=False)
    lu.to_csv(PROCESSED / "decide_playoffs_lineups.csv", index=False)
    return md


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    print(run())
