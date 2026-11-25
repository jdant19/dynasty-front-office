# Projections (Phase 3) — backtest report

As of 2026-10-28. All numbers are league points (our scoring, matching Yahoo on 239/239 checked
player-weeks). Every model was trained only on seasons before the season it was tested on.
Rebuild: `python -m dfo.projections.weekly backtest`, `python -m dfo.projections.seasons backtest`,
then `python -m dfo.projections.current` for the live 2026 files.

## The bar

The design doc requires the projections to beat FantasyPros expert consensus on 2021-2025
before any valuation work starts. "Error" = mean absolute error in points; "ranking" = average
Spearman rank correlation within each position and week (or season), i.e. did we order players right.

## 1. Weekly

Method: FantasyPros' weekly rank, turned into points per position from past seasons, averaged
50/50 with a gradient-boosting median model that sees usage (targets, carries, shares, expected
points), Vegas team totals, matchup and the FantasyPros rank. The 50/50 weight was fixed before
testing; 75% model scored marginally better on error and was not adopted.

Evaluated rows: ranked players whose game was still to come when the rankings were taken
(35,345 player-weeks).

| Group | FantasyPros MAE | Ours MAE | FantasyPros ranking | Ours ranking | Inside 80% range |
| --- | --- | --- | --- | --- | --- |
| All | 3.44 | **3.31** | 0.543 | 0.544 | 85% |
| QB | 5.11 | **4.99** | 0.686 | 0.691 | 82% |
| RB | 3.50 | **3.36** | 0.748 | 0.746 | 85% |
| WR | 3.42 | **3.24** | 0.687 | 0.686 | 86% |
| TE | 2.34 | **2.22** | 0.664 | 0.663 | 88% |
| K | 3.78 | 3.78 | 0.205 | 0.208 | 84% |
| DEF | 4.16 | **4.12** | 0.265 | 0.272 | 81% |
| 2025, vs FantasyPros' own projected points | 3.62 | **3.29** | 0.561 | 0.556 | |

By season, ours had lower error every year (2021 3.48 vs 3.60; 2022 3.24 vs 3.39; 2023 3.21 vs
3.35; 2024 3.33 vs 3.46; 2025 3.29 vs 3.41). Ranking was within ±0.004 every year.

The usage-only model (no FantasyPros input) was worse than FantasyPros on both measures
(3.56 MAE, 0.48 ranking). The value is in combining them.

**Verdict:** beats FantasyPros on error every season and at every position except kickers (tie).
Ties on ranking. Kickers and defenses are close to unpredictable for everyone (ranking about 0.2-0.27).

### The 80% range

The range is the 10th-90th percentile of what players at the same position with the same
projection actually scored (2021-2025, each season's range built from the other seasons; built
from earlier seasons only, coverage is the same within 0.01). Outcomes landed above the top of the
range 9.7% of the time (target 10%) and below the bottom 5.0%. The bottom misses less because for
depth players the 10th percentile is exactly 0 points, and a 0 counts as inside. The low end
includes the chance of a late scratch as of Friday-Sunday rankings; game-day injury news is not
in the model yet (start/sit module, Phase 7).

Fixed during the build: an earlier version fit 10th/90th-percentile boosting models directly.
With a third of training rows at exactly 0 points, and scikit-learn's automatic early stopping,
those models could stall and gave every current player a 0 low end. The empirical range replaced them.

## 2. Rest of season

Rate = 0.5 × FantasyPros-rank points + 0.25 × season-to-date average + 0.25 × expected points per
game; total = rate × team games left × position availability (share of remaining games that
ranked, active players actually played, 2021-2025). Tested at weeks 6, 8 and 10 on players with 4+
games left. Target: points per game over the rest of the season.

| Week | Season avg MAE | FantasyPros MAE | Ours MAE | FantasyPros ranking | Ours ranking |
| --- | --- | --- | --- | --- | --- |
| 6 | 2.73 | 2.32 | **2.30** | 0.740 | 0.732 |
| 8 | 2.56 | 2.42 | **2.26** | 0.733 | 0.742 |
| 10 | 2.55 | 2.31 | **2.25** | 0.730 | 0.746 |

Caveat: these weights were picked from a few candidates on these same seasons, so the edge is
slightly flattering. Using the weekly blend as the rest-of-season rate was worse than FantasyPros
(2.42-2.53), which is why it is not used.

Tuesday to Thursday, before the coming week's FantasyPros ranks are out, the FantasyPros term uses
last week's rank (one week stale; `proj_ros.csv` `fp_rank_week` says which week). Until 2026-11-18
the term was dropped on those days, leaving 0.5 season average + 0.5 expected points. Same
backtest, same players: last week's rank MAE 2.33 / 2.22 / 2.34 at weeks 6 / 8 / 10 against 2.55 /
2.37 / 2.47 with no rank term. Found when Jordan questioned a Wednesday trade card (Kenneth Walker
III was 21.6 points a game without the rank term, 19.7 with it).

## 3. Future seasons (dynasty, +1 to +3 years)

Per-game rate: FantasyPros dynasty rank, turned into points per position from past seasons, as
the backbone, with 25% weight on a stats model (age, production, usage, draft capital,
experience) at +1 and +2 years and 0% at +3. Players with 4+ games in the target season.

| Horizon | Last season's average MAE | Dynasty rank MAE | Stats model MAE | Used (blend) MAE | Ranking, rank vs used |
| --- | --- | --- | --- | --- | --- |
| +1 | 2.57 | 2.32 | 2.39 | **2.28** | 0.772 vs 0.773 |
| +2 | 3.15 | 2.31 | 2.42 | **2.28** | 0.795 vs 0.794 |
| +3 | 3.39 | **2.27** | 2.53 | 2.27 (rank only) | 0.768 |

**Verdict:** the expert dynasty rank carries the ordering. The stats model adds a small error
improvement at +1 and +2 years and nothing at +3.

Expected games (including seasons lost to injury or leaving the league) come from a small model on
position, age, production, games, experience and dynasty rank, trained on every season including
injury-shortened ones. The first version left out the dynasty rank and those seasons. That version
projected starters hurt this year 1.5-4.3 games short in later years:

| Horizon | All ranked, old MAE | All ranked, new MAE | Hurt starters, old bias | Hurt starters, new bias |
| --- | --- | --- | --- | --- |
| +1 | 4.18 | **4.00** | -1.47 | -0.51 |
| +2 | 4.66 | **4.14** | -3.69 | -0.64 |
| +3 | 4.93 | **4.15** | -4.27 | -0.15 |

(Hurt starters = fewer than 8 games and a top-60 positional dynasty rank; 95-157 players.)

## 4. Rookies, year 1

Draft-capital model and FantasyPros rookie rank (May-August), 50/50. 229 rookies, 2021-2025.

| Draft capital only | Rookie rank only | Blend |
| --- | --- | --- |
| 2.60 MAE / 0.563 | 2.53 / 0.568 | **2.44 / 0.572** |

## 5. Injury status (added 2026-11-25)

Found in the calibration review of Weeks 9-11: players listed IR or Out were projected about 11 points
(2 of 27 played), and rest-of-season totals counted every IR player at the full position availability
(Puka Nacua, two games into an IR stint, 124 points; he cannot play before Week 15). Free agents had no
status at all. `projections/injury.py`, fit 2021-2025 (regular season), joining the NFL's injury reports
and weekly reserve lists (nflverse) to who played:

| Status (ranked players) | Player-weeks | Played |
| --- | --- | --- |
| Out | 698 | 0% |
| Doubtful | 172 | 0.6% |
| IR / other reserve lists | 467 | 1.2% |
| Questionable | 1,671 | 63% (QB 40%; did not practice 43%, limited 64%, full 73%) |
| Not listed | 30,059 | 80% (includes deep backups who never get in) |

- **This week:** projection = chance of playing x projection if he plays; the 80% range is that mixture's.
  Out, Doubtful and reserve lists are 0. Questionable is a logistic fit on position, projection and
  practice participation (`params_injury_week.csv`); before the final report, when practice is unknown,
  a second fit without it. A Questionable player who plays scores like anyone else (actual/projected
  1.18 vs 1.14 unlisted), so no further discount. `proj_week.csv` keeps `proj_if_plays` and `play_prob`.
- **Rest of season:** the share of players with that status (and games already missed in a row) who
  played the next game, the one after, and so on, re-injuries included (`params_injury_return.csv`;
  QB/RB/WR/TE ranked earlier that season). IR adds the NFL's 4-game minimum on top. Each remaining
  game's chance is in `proj_avail.csv`; `proj_ros.csv` availability is their mean. Healthy players who
  played last week keep the flat position rate (no change).
- **Check, 2021-2025 weeks 3-14 (in-sample):** share of remaining games played, predicted vs actual: IR
  0.21 vs 0.22 (759), Out 0.58 vs 0.58 (477), Doubtful 0.60 vs 0.60 (84), Questionable 0.76 vs 0.76 and
  this week 0.71 vs 0.72 (775), unlisted but missed the last game 0.68 vs 0.66 (307).
- **Live status:** the league site (rostered players) and the NFL report and reserve list for the
  coming week (everyone). The more serious one is used, except that the site wins on IR for rostered
  players because it changes the moment a player is activated. `status_source` says which.
- **Who uses it:** start/sit (no longer applies its own Questionable discount), waivers and trades
  (week-by-week grid: a player due back in Week 16 counts in Weeks 16-18 only), the season simulator
  (this week from `play_prob`, later weeks from the fitted chances, else the absence chain), prices
  (rest-of-season value uses the lower availability).
- Limits: the fit uses the final (Friday) report; a midweek "Questionable" on the site is a looser label,
  so early in the week the chance is conservative. Positions are pooled for the return shares.

## 6. Starter check from depth charts (added 2026-11-25)

Also from the Weeks 9-11 review: from Tuesday until the week's FantasyPros ranks arrive, the projection
runs without ranks, and quarterbacks who had lost the job were projected on their earlier starts
(Malik Willis 14.6, Carson Wentz 12.2; neither played). The rest-of-season rate had the same fault every
day, since two of its three terms come from his own games. `projections/depth.py`, fit 2021-2025 on
nflverse depth charts (the weekly chart through 2024, ESPN snapshots before each game from 2025): a
team's starter is its highest-listed quarterback not Out, Doubtful or on a reserve list.

| Role | Player-weeks | Started | Points |
| --- | --- | --- | --- |
| Starter | 2,972 | 82% | 14.2 |
| Backup who started earlier this season | 813 | 16% (20-25% of later games) | 2.4 |
| Other backup, second | 2,165 | 4% (11-17% later) | 0.7 |
| Other backup, third or lower | 980 | 1% (4-11% later) | 0.3 |

- **This week, no rank:** a backup's projection and range are capped at his role's (never raised).
  With ranks nothing changes: ranked backups were projected 2.54 and scored 2.42.
- **Rest of season:** rate if he starts (a former starter his own; anyone else 14.7, what backups scored
  in a first start) x his chance of starting each remaining game. Back-test, all 617 backups at Weeks
  6/8/10 2021-2025, points per remaining team game: predicted 1.91 / 1.10 / 2.58, actual 1.78 / 1.02 / 3.11
  (second / third / former starter; bias -0.02). The old rate gave a former starter 9.2.
- A quarterback FantasyPros ranks in the top 32 that week is never treated as a backup: the rank has news
  the chart may lack (Week 12: Minnesota's chart lists Kirk Cousins over Kyler Murray, back from injury).
- Quarterbacks only: the other positions share work, and their usage features already carry it.

## 7. Weekly scorecard (added 2026-11-25)

`projections/scorecard.py`, run after every projection refresh. Each run replaces a player's frozen row
for the coming week until his game day (13:00 UTC), so `proj_week_frozen.csv` keeps the last projection
made before his game (Weeks 9-11 rebuilt from git history). Each finished week is scored on fantasy-
relevant players (`calibration_weekly.csv`, `calibration_status.csv`): bias, error against FantasyPros'
rank in points, ordering against FantasyPros', share inside / below / above the 80% range, and by
injury status how many played. Drift flags over the last three scored weeks (`calibration_flags.csv`):
inside the range outside 70-90%, bias beyond +/-1 (+/-2 at a position), error 10% worse or ordering 0.05
worse than FantasyPros, any Out/IR player projected above 1. Shown on the page's Data tab.

## What this means for valuation

- Use these projections as the points input for Phase 4. They are at least as good as the best
  free consensus on every measure tested, and better on error.
- Don't expect an ordering edge from the projections themselves; there isn't one. Any edge in
  this league will come from salary, contract and rule math, roster fit, and reading the other
  nine owners, not from out-projecting FantasyPros.
- Long-range values lean on one expert source (the FantasyPros dynasty rank via DynastyProcess).
  When it is stale, the as-of date in `proj_future.csv` shows it.

## Known limits

- Weekly rankings for 2025-2026 are recovered from DynastyProcess's git history (the last version
  before each Sunday). 2020 has partial weekly coverage. Before 2020, the weekly model trains on
  stats only.
- No game-day inactive news (90 minutes before kickoff). Injury status is in (section 5).
- PFF data is not in yet (rankings downloads only). It can be added as another source and tested
  the same way.
- Kicker and defense ranges are wide because those outcomes are mostly noise.
- The coming week's FantasyPros ranks arrive Thursday or Friday. From Tuesday until then, the pool
  keeps every player ranked the week before, projected without ranks for the week (the old rank is
  stale and not used there; the rest-of-season rate does use it, see section 2), and `proj_week.csv` says so in `ranks_note`. Before 2026-11-17 the pool shrank to rostered
  players on those days, so the Tuesday waiver run found no free agents and failed.

## Files

`data/processed/proj_week.csv`, `proj_ros.csv`, `proj_future.csv` (live, stamped with stats as-of
and made-at times); `proj_*_backtest_summary.csv`, `proj_weekly_backtest_by_season.csv`,
`proj_weekly_backtest.csv` (every evaluated player-week). Tests: `tests/test_projections.py` fail if
the weekly blend stops beating FantasyPros on error, the range drifts off 10% at the top, the
games model regresses, or a current range collapses to 0.
