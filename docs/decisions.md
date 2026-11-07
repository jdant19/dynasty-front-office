# Decision modules (Phase 7)

Each module answers one decision with the metric built for it (P16) and is checked against a real
past decision before it is used.

## Start/sit (`decide/lineup.py`)

Question: which lineup is most likely to beat this week's opponent.

- Weekly projections with 80% ranges (projections/weekly.py); a team score is treated as normal with
  the sum of medians and the sum of per-player variances; the lineup searched is the one with the
  highest win probability against the opponent's best projected lineup.
- Out, IR and bye players are never started. Questionable players count at their chance of playing
  (`params_questionable.csv`: fantasy starters listed Questionable 2021-2025, nflverse injury reports:
  QB 52%, RB 76%, WR 80%, TE 74%, K 78%) with the extra spread, and come with the start to use if ruled out.
- A FantasyPros rank that rose or fell 20+ places since last week is flagged: check the news first.
- Players whose game has kicked off are listed separately until Jordan says whether he started them;
  `--started "<name>" ...` locks them in at their actual points and re-optimizes the rest.
- Output: `decide_lineup_w<week>.md` and `.csv`, with close calls (swaps within 2 points) and the
  win-probability change of each.

Exit test (`--review`, `decide_lineup_review*.csv`): Jordan's real Weeks 4-8 lineups, transcribed from
the Yahoo screenshots (`jordan_lineups_2026.csv`), against the module's pick made only from data before
each week, both scored with what happened.

| Week | Jordan | Module | Best possible |
|---|---|---|---|
| 4 | 105.66 | 104.66 | 144.66 |
| 5 | 160.48 | 170.28 | 187.48 |
| 6 | 125.74 | 125.74 | 125.74 |
| 7 | 128.36 | 128.36 | 137.16 |
| 8 | 147.22 | 147.22 | 171.64 |

Same lineup in Weeks 6, 7 and 8 (+8.8 points over the five weeks); the module would have started Bucky Irving over Cam Skattebo in Weeks 4
and 5 (+24 in Week 5, -1 in Week 4) and Romeo Doubs over Ja'Marr Chase in Week 5 (-14.6). That miss
came from the input: FantasyPros had Chase at WR45 on the Saturday scrape (WR3 the week before); the
rank-jump flag now asks for a news check in that case.

## Waivers (`decide/waivers.py`)

Question: does swapping this free agent in for this rostered player raise expected payout?

- This season: the change in the team's best-lineup points over the remaining regular-season weeks,
  the coming week from its own projection (matchup, bye), later weeks from the rest-of-season rate
  (shrunk, times availability), with a waiver pickup always available at each position's waiver
  level, priced at what a lineup point is worth to the team now (the dial).
- Later seasons: an in-season add can be designated next offseason, so he carries an option (his 2027
  lineup points over waiver minus what his likely RFA price normally buys, if positive, x0.88) at the
  dial's 2027 weight; a drop loses the player's later surplus and leaves 75/50/25% dead cap.
- Moves are planned one at a time (best move, update the roster, look again) until none is worth $0.25.
- Bids: $1 when the move is worth $1+ of payout, else $0. Every 2026 add on record went for $0 or $1
  and ties go to the worse record.
- Output: `decide_waivers_w<week>.md` and `.csv` (the plan, then the next best free agents).

Exit test (`--review`, `decide_waivers_review.csv`): Jordan's waiver windows for Weeks 7-9 against the
module's moves from what was known then. Scored on lineup points: Jordan's actual roster that week vs
the same roster with his waiver moves swapped for the module's, best lineup from actual points (the
same hindsight rule for both, so the gap is the moves, not lineup calls).

| Window | Jordan's adds | Module's adds | Lineup that week (Jordan / module) | Through Week 8, roster held |
|---|---|---|---|---|
| Week 7 | Bass, Wicks, Jets, Reichard, Bourne | Vikings DEF, Reichard | 137.2 / 149.4 | 292.8 / 315.0 |
| Week 8 | Cooks, Tracy, Cooper, Commanders, Strange, Rodriguez, Deebo, Gainwell, Sutton | Bengals DEF, McLaughlin | 171.6 / 173.6 | 171.6 / 173.6 |
| Week 9 | Chiefs, Gesicki, Bates | Chiefs, Buccaneers | not played | |

Week 7's gap is mostly Vikings DEF over Jets (10 vs 1) and keeping Emanuel Wilson (18 in Week 7). Two
scored weeks are a small sample; one boom player swings them.

Known limits: kicker and defense values use rest-of-season rates after the coming week, so streaming
by matchup two or more weeks out is not valued; rival bids above $1 have not happened this season
and are not modeled.
