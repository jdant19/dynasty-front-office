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

## What wins, and decision grades (`owners/diagnose.py`, `owners/grades.py`, `owners/rankings.py`)

Each metric answers one question (principle P16, `docs/metrics.md`).

- **Rebuilt lineups** (`lineup_attribution.csv`, `owner_season_lineups.csv`): every owner-season 2014-2025
  from its preseason roster (keepers + auction buys) plus in-season trades (archive trades have no date:
  offseason if the receiver kept the player at that auction, otherwise assumed at midseason). Each week it
  starts the best lineup by points per game so far (3 games of last season's rate as a prior). Rebuilt
  lineup strength tracks the final standings at 0.67 rank correlation a season (preseason roster alone 0.57).
  Every started point over the waiver level is credited to the decision that brought the player to that
  owner. A healthy counterfactual (absent players at their expected points) measures injury luck.
- **Why owners win** (`owner_bridge.csv`): each owner's average weekly lineup edge over the league, split by
  channel (auction, rookie draft, RFA, trade, waiver/FA), injury cost vs the league, and finish vs lineup rank.
- **Decision grades** (`grades_decisions.csv`, about 1,450 rows), in lineup points over waiver:
  - cap decisions (auction buys, RFA wins and matches, tags, kept waiver/FA pickups): every season held, the
    points he started for, minus what that salary normally buys (`params_price_curve.csv`: what auction buys at
    each price and position started for in the season bought); later seasons haircut 0.88 a year;
  - letting an RFA go: minus what the winner's deal returned over its price;
  - rookie picks: the player's own points over waiver in his first four seasons minus the slot average
    (fewer seasons for recent drafts, against the slot average over the same seasons);
  - trades: what each side's received players started for, net of salary (none for the in-season half), trade
    season + 2 seasons, minus the same for what it sent, plus picks (round average) and cap moved; each season
    weighted by that owner's posture (`owner_season_posture.csv`: payout per point before the season from the
    preseason simulator 2020-2025 or prior finish before; at midseason from first-half lineup strength).
- **Rankings** (`owner_rankings.csv`): skill = average grade per decision of each kind, with its count.
  Volume (how many decisions, and the lineup points they brought) is in the bridge, not the skill rank.
- `owner_careers.csv`, `owner_finishes.csv`: every season 2013-2025; payouts at today's table.
- `observations.csv`: Jordan's notes on owners, each with evidence, a verdict and an expiry.

Known gaps: waiver moves inside a season are not recorded (kept pickups show up as "waiver/FA"; a few may be
unrecorded trades); in-game injuries count as games played; no trades are on record for 2017 or 2025
(Jordan: there were none).

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
