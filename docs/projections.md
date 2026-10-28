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
- No game-day injury or inactive news yet. The injury status column in `proj_week.csv` comes from the site.
- PFF data is not in yet (rankings downloads only). It can be added as another source and tested
  the same way.
- Kicker and defense ranges are wide because those outcomes are mostly noise.

## Files

`data/processed/proj_week.csv`, `proj_ros.csv`, `proj_future.csv` (live, stamped with stats as-of
and made-at times); `proj_*_backtest_summary.csv`, `proj_weekly_backtest_by_season.csv`,
`proj_weekly_backtest.csv` (every evaluated player-week). Tests: `tests/test_projections.py` fail if
the weekly blend stops beating FantasyPros on error, the range drifts off 10% at the top, the
games model regresses, or a current range collapses to 0.
