# Simulation and the dial (Phase 5)

As of 2026-10-30, after Week 7 (Week 8 in progress). Rebuild: `PYTHONPATH=src python -m dfo.sim.dial`
(about 2 minutes). Payout dollars are real prize money (P15); points are starting-lineup points.

## How it works

**Player weeks** (`sim/outcomes.py`). A player's week is his projection plus a miss drawn from
2021-2025 history for players at that position and projection level (weeks 17-18 drawn separately,
when NFL teams rest starters), so zeros and blowups come at their real rates. Absences come in
stretches: a player who missed last week misses again 35-51% of the time, against 5-10% for a new
absence (fit from 2021-2025, `params_absence.csv`).

**Regular season** (`sim/season.py`). Each remaining week, every team starts its best available
lineup (owners know who is out), with a waiver pickup always available at each position; scores are
drawn; matchups follow the league schedule; standings by wins, then points. Games already played
count as scored.

**Playoffs** (`sim/playoffs.py`, `sim/nfl.py`). The four playoff teams keep their own players and
draft the rest of their starting lineups in seed order from everyone else, valuing each player at
his rate x his NFL team's expected playoff games. NFL teams are rated from the betting market (every
2026 spread), the rest of the NFL season and the playoff bracket are played out, and each starter
scores over his team's playoff games. Highest total takes $200, second $100.

**Future seasons** (`sim/future.py`). Strength = projected points of players under contract (open
slots at the market replacement level), plus what the team's cap room buys at that August's auction,
plus rookies from its picks. Each auction clears at its own price: all teams' room against the
talent not under contract (2027 $1.06 a point, 2028 $1.20, 2029 $1.38, against the $1.14 fit on past
auctions: money piles up as contracts run out). Seasons are played out with strength uncertainty,
weekly noise, and playoff odds from the 2026 simulator.

**The dial** (`sim/dial.py`). For every team and season: the payout from one more lineup point,
measured by adding +10 points a week and replaying with the same random draws. Later seasons are also
shown after the 0.88-a-year haircut. A move's worth to a team = the points it adds or costs in each
season x that season's payout per point, plus the cap it saves or spends at that auction's price.

## Checks

| Check | Actual | Simulated |
| --- | --- | --- |
| League average team score a week (2026 Weeks 1-7 vs rest of season) | 118.4 | 119.3 |
| Week-to-week spread within a team | 22.1 | 22.6 |
| Expected payouts summed over the league | $1,000 prize pool | $1,000 |

**Past seasons** (2020-2025, 60 team-seasons): every team's opening-day roster rebuilt from the kept
players, auction buys and rookie draft, rated with the rankings owners had in August, and the season
played out. Preseason strength ranks finishes at 0.32; its odds beat giving everyone an equal chance,
modestly: Brier 0.088 vs 0.090 for 1st, 0.221 vs 0.240 for the top 4. Teams that won got about 12%
beforehand against 10% for the rest. **In this league the August roster explains only part of how a
season ends**; trades, waivers and injuries decide a lot, so future-season odds stay close to even
and the in-season model, which sees current rosters, carries more weight.

## The 2026 read (after Week 7)

| Owner | Wins (avg) | 1st | Top 4 | Playoff title | Expected payout 2026 | Payout per point now | 2027 (haircut) | Posture |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Jordan | 12.8 | 38% | 96% | 36% | $326 | $1.06 | $0.83 | push |
| Johnny | 13.0 | 36% | 95% | 26% | $307 | $1.13 | $0.89 | push |
| Russ | 11.8 | 21% | 87% | 16% | $212 | $1.12 | $0.77 | push |
| Zach | 10.4 | 3% | 55% | 12% | $80 | $1.05 | $0.92 | push (close) |
| Max | 10.1 | 2% | 40% | 5% | $47 | $0.82 | $0.84 | build (close) |
| Matt | 8.9 | 0% | 14% | 2% | $14 | $0.42 | $0.87 | build |
| Qasim | 8.6 | 0% | 13% | 3% | $13 | $0.39 | $0.86 | build |
| Ryan | 6.1 | 0% | 0% | 0% | $0 | $0.03 | $0.65 | build |
| Ben | 5.5 | 0% | 0% | 0% | $0 | $0.01 | $0.75 | build |
| Mike | 2.9 | 0% | 0% | 0% | $0 | $0.00 | $0.73 | build |

Jordan's future seasons: about $102, $106 and $103 expected (league average is $100 a season).

## Known limits

- Every owner sets his best lineup, and rosters stay as they are (no waivers or trades to come).
  Owner tendencies come with Phase 6.
- Playoff injuries score 0; the league substitutes the next player on the NFL depth chart.
- NFL tiebreakers are approximated by team rating.
- Future strength leaves out designation rights (tag, RFA, extension) and assumes every team spends
  its room at the clearing price; it takes no account of what is actually available at each position.
- The in-season simulator cannot be backtested on past seasons (no weekly rosters before 2026);
  it is checked against 2026's actual scores instead.
