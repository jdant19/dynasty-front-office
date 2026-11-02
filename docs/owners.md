# History grading and owner models (Phase 6)

As of 2026-11-02. Rebuild: `PYTHONPATH=src python -m dfo.owners.history && PYTHONPATH=src python -m dfo.owners.model`.

**Owner reads for review:** the doc "Owner reads 2026" (https://claude.ai/artifact/EPXsgfqoJrzxUg98ziQ2i5).
Jordan's notes there are logged as dated observations (knowledge level 4) and scored like any source.

## What is graded (`owners/history.py`)

- **Trades**: 56 real trades (52 from 2014-2025 from the archive, 4 in 2026 from the site). The old
  system's 11 "summary" entries for 2026 are trade ideas it logged, not trades, and are left out.
  Each side: points over the market replacement level its players produced in seasons someone kept
  them, salaries paid, the average points of each pick's round in its draft, and cap moved; the trade
  season is shown apart (trade dates before 2026 are unknown). 48 trades have two later seasons to grade.
  Archive rows typed "player" that are really cap ("$25 in 2022") or picks by slot ("2021 pick #16")
  are reclassified; conditional terms (swaps, cap if a player is cut) stay unresolved.
- **Auctions** 2014-2026 per owner: spend, positional mix, age, price per point vs the league and
  value returned over price (2020-2025, rankings as of each August).
- **Rookie picks**: points over four seasons against the slot average (2014-2022), and reach vs the
  FantasyPros rookie order (2021+).
- **RFAs** 2019-2026: bids placed, bid size relative to the final price, match rate on contested RFAs,
  matched players' return vs price.
- **Tags**: return in the tag season vs the tag cost.

## Owner model (`owners/model.py`, `owner_model.csv`)

One row per current owner with every parameter above, sample sizes, 2026 waiver activity and the dial.
A read lists a trait only when the owner is at least one standard deviation from the league average;
confidence is low under 5 events, medium 5-14, high 15+.

## Rankings, careers and Jordan's notes (`owners/rankings.py`)

- `owner_rankings.csv`: current owners ranked 1-10 on each aspect, with the sample size beside each rank:
  auction (cap $ returned over price per buy, 2020-2025), rookie draft (points over four seasons vs the
  slot average), RFA (cap $ returned over price on every RFA he ended up holding, matched or won), tags
  (cap $ over tag cost), trades (cap-equivalent per trade over the next two seasons) and results (average
  regular-season finish). `avg_decision_rank` averages the five decision aspects. Contract length (years
  a $5+ auction buy stays on its original schedule, buys through 2023) is a style column, not ranked.
- `owner_careers.csv`, `owner_finishes.csv`: every season 2013-2025 with finish, playoff result and payout.
  Payouts use today's table ($400/$200/$100 regular season, $200/$100 playoffs); earlier seasons may
  have paid differently.
- `observations.csv`: Jordan's notes on owners (raw copy in `data/raw/owner_notes/`), each with the
  evidence, a verdict and an expiry date one year out.

Small samples rank too: RFA and tag ranks rest on 1-9 events per owner and move a lot with one case.

## League-wide findings

- The trade market is thin: Jordan was in 32 of 56 trades and Ben in 23; Johnny, Russ, Mike, Zach 3-4 each.
- Pick buyers came out ahead in 13-17 of 20 scoreable pick trades.
- Owners match 75% of contested RFAs; matched players cost $41 on average against $31 of coming-season value.
- Tags rarely pay in their own season: 30 graded tags returned $9 less than cost on average; 19% returned more.

## Known limits

- Process grades (was the deal right at the time?) need values as of each trade's date; dates exist
  only from 2026, so history is graded on outcomes.
- Lineup habits per owner need weekly lineups, which exist only as screenshots from 2026.
- Former owners (Kevin, Winston) are graded in history tables but get no read.
