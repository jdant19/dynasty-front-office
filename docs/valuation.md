# Valuation (Phase 4) — method and backtest report

As of 2026-10-30 (2026 auction included). Rebuild everything with `PYTHONPATH=src python -m dfo.valuation.build` (about 90
seconds). Every backtest scores a season with models fit without that season.

## How a dollar value is built

1. **Expected points for a season.** This season: the calibrated rest-of-season rate (Phase 3).
   Next season: the Phase 3 projection, corrected for its measured age bias (it ran about 1 point
   per game high for players 22 and under and up to 1 low for productive players past 30;
   `params_age_bias.csv`). 2028-2029: next season's points per game carried forward by
   the position's aging curve, times the projected games (`params_aging.csv`).
2. **Gap calibration.** Projections spread players too far apart, so each is pulled toward its
   position's startable mean by the measured factor (`params_gap_shrink.csv`): rest of season RB
   0.84, WR 0.84, QB 0.78, TE 0.61, K 0.35, DEF 0.28; future seasons 0.89-0.93.
3. **Value over replacement.** A season is worth E[max(0, points - replacement)]: a season below
   replacement is benched or cut. Full seasons use what history says players with that expectation
   actually returned; the rest of this season uses a spread scaled to the weeks left. Kickers and
   defenses are measured against weekly streaming with no floor (P10).
4. **Replacement level depends on when he'd be replaced (P11).** In season: the next startable
   player (the first non-starter league-wide). Later seasons: the level the auction prices imply.
5. **Dollars.** One price per point, fit to the 2020-2025 auctions.

| Position | First non-starter (season pts) | Market level shift | Market replacement |
| --- | --- | --- | --- |
| QB | 243 | +105 | 348 (about the 4th QB) |
| RB | 132 | +30 | 162 |
| WR | 134 | +25 | 159 |
| TE | 113 | +45 | 158 |

Fit to the 2020-2026 auctions (292 buys). At that level the league pays **$1.14 per expected point**
for the coming season (in-season level: $0.66), and one price fits stars and mid-tier players alike:
average pricing error $7.5, against about $10.4 with the first-non-starter level, where mid-tier
players cost $0.31 a point and stars $0.70.

## Two values, used for different things (`prices.csv`, `ours_jordan.csv`)

Every player carries two values that are never added together for a decision (P14):

- **Season $**: what he adds this season. For a team pushing to win now, this is the one that counts.
  Current salary is not subtracted (owed either way; in-season cap buys almost nothing).
- **Future $**: what his contract is worth from 2027 through 2029: each season's value minus that
  season's salary (x0.88 a year), keeping or cutting each offseason, whichever is better (dead cap per
  the constitution), plus the best designation when it ends (tag at the site's price, mid-level
  extension $15/$30/$45, or RFA at the going RFA price). For a team building, this is the one that counts.
  A season's value is what the same production would cost at that August's auction, so a cheap
  salary is surplus only to the extent his production would cost more than that salary to replace.

How much each counts for Jordan is the dial's job (Phase 5), computed from payout odds; until then the
two are shown side by side. Lifetime $ (their sum) is kept only as a reference.

Also per player:
- **Market $**: what his next season would fetch at this league's auction.
- **FC reference**: FantasyCalc's order within his position on our dollar scale, minus the cost of
  holding him. An outside view: not this league's price, and no odds that anyone here would pay it
  (owner models, Phase 6). Kittle is the example: FantasyCalc rates him like a mid QB, but this
  league bought him for $1 in August 2026.
- **Our $**: this season, Jordan's lineup gain (lineup re-picked each remaining week with byes; a
  waiver pickup always available at each position; the cheapest current player dropped to make
  room); later seasons, his Future $. Shown as two columns, never summed.

## Backtest 1: auctions 2020-2025 (258 skill-position buys)

| Predictor of what the coming season returned | Average miss | Ranking |
| --- | --- | --- |
| League price | $20.3 | 0.463 |
| Engine | $21.8 | 0.457 |
| 50/50 | $20.8 | 0.474 |

**Owners already price the coming season about as well as the engine.** No edge for the coming
season by itself; combining the two orders players slightly better than either.

Later contract years on auction buys are worth little: at the market replacement level, the best
quarter of buys by projected future value returned $3 each over years 2-4 (engine: $1). Owners do
not pay for them, and they are right not to.

Position surplus (value returned minus price) depends on the replacement level chosen (RB was -$2
under one, +$8 under the other), so no position-level buy or sell rule is drawn from it.

*Correction to the interim message of 2026-10-30:* "QBs go cheap" was an artifact of the first
replacement level. Mid-tier QBs sell for $1-2 because they are freely available (Goff, Dak, Stafford
at $1-2), so only top QBs carry value. "Later contract years are free" was the same artifact.

## Backtest 2: RFA bids 2020-2026 (102 players with rankings as of July 1)

- RFAs cost **31% more per point** than the auction: $1.50 vs $1.14 per point of coming-season value.
  RBs go at about auction rates; WRs, TEs and QBs well above (buyers pay for multi-year control).
- RFA prices rank what players returned better than the engine (0.65 vs 0.57): bidders know
  something the rankings don't, or price future years the coming-season value leaves out.
- So the right to match is worth little unless a player is clearly better than his likely RFA price;
  the franchise tag is often the cheaper tool for a star (tag prices from the site).

## Backtest 3: picks

**Use value by slot** (this league's 2014-2022 rookie drafts, four seasons each, auction-price value
over the market level minus rookie salary, 0.88 a year):

| Slots | Picks | Bust | Starter season | Top-5 season | Avg value |
| --- | --- | --- | --- | --- | --- |
| 1.01-1.03 | 27 | 26% | 78% | 44% | $121 |
| 1.04-1.07 | 36 | 39% | 69% | 19% | $48 |
| 1.08-1.10 | 27 | 74% | 44% | 19% | $55 |
| 2.01-2.03 | 27 | 48% | 63% | 11% | $28 |
| 2.04-2.07 | 36 | 75% | 33% | 11% | $15 |
| 2.08-2.10 | 27 | 63% | 44% | 19% | $30 |
| 3rd round | 90 | 78% | 36% | 3% | $10 |

The averages include the busts as $0. The spread behind them (`val_picks_distribution.csv`):

| Slots | Average | Chance of $0 | 25th pct | Median | 75th pct | 90th pct |
| --- | --- | --- | --- | --- | --- | --- |
| 1.01-1.03 | $119 | 30% | $0 | $76 | $199 | $332 |
| 1.04-1.07 | $51 | 42% | $0 | $25 | $72 | $148 |
| 1.08-1.10 | $55 | 74% | $0 | $0 | $19 | $235 |
| 2nd round | $15-31 | 48-78% | $0 | $0-4 | $0-33 | $46-79 |
| 3rd round | $10 | 79% | $0 | $0 | $0 | $50 |

A pick is lumpy: an early 1st is a 30% chance of nothing and a 1-in-4 chance of $200+. An
established player with the same average comes with a much narrower spread. Which matters more
depends on the team's position, and the simulator (Phase 5) prices that by playing out the real
spread (bust, starter, star) in the payout odds instead of using the average. The smoothed curve has
a cliff after the first two picks.

**Against the outside reference** (2027 picks, one year out; FantasyCalc on our dollar scale vs use
value with its 90% band): a buyer paying FantasyCalc's price would overpay for a mid 1st ($70 vs
$36) and a late 3rd; everything else is inside the band. That is a ceiling on what to ask, not a
price anyone in this league has paid.

**Past pick trades in this league** (20 trades that could be scored: two teams, picks and players,
three later seasons observed; conditional picks and swaps left out):

| Counting | Pick buyer ahead | Average margin to buyer | 90% range |
| --- | --- | --- | --- |
| Next three seasons only | 17 of 20 | +$63 | +$34 to +$95 |
| Plus a full trade season for every player | 13 of 20 | +$29 | +$5 to +$52 |

Trade dates aren't recorded, so the true answer sits between the two rows. **Inside this league,
picks have sold cheap:** the hypothesis that the league overvalues picks (P7) is not supported.
Dollars don't capture a contender's title odds, so some of those sellers may still have been right
for their season. Phase 5 can price that.

## Known limits

- The 2026 auction is not in yet (Jordan will upload it); the fit uses 2020-2025.
- Rankings before Aug 2020 are not available, so auctions and RFAs before 2020 are not scored.
- The only outside trade reference is FantasyCalc; KTC and DynastyProcess values are not wired in,
  and none of them says whether a sale is possible (owner models, Phase 6).
- Old productive players are the weakest case for the future projection (few comparable players).
  Kittle's 2027 is projected at about 8 points per game after the age correction; at 10-11 his 2027
  would be worth about $15 at auction prices, and at his current 12.5 about $34, against a $2 salary.
- Our $ uses expected points; injury risk beyond position availability, and the dial, come in Phase 5.
- Designation slots are limited (2 RFA, 1 tag, 1 extension per team); rights are valued one player
  at a time, so a team with several tag-worthy players is overstated until the simulator allocates them.

## Files

Parameters: `params_market.csv`, `params_gap_shrink.csv`, `params_aging.csv`, `params_age_bias.csv`. Backtests:
`val_auction_*`, `val_rfa_*`, `val_picks_*`. Live: `prices.csv`, `ours_jordan.csv`.
Tests: `tests/test_valuation.py`.
