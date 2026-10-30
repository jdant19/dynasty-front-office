# Valuation (Phase 4) — method and backtest report

As of 2026-10-30. Rebuild everything with `PYTHONPATH=src python -m dfo.valuation.build` (about 90
seconds). Every backtest scores a season with models fit without that season.

## How a dollar value is built

1. **Expected points for a season.** This season: the calibrated rest-of-season rate (Phase 3).
   Next season: the Phase 3 projection. 2028-2029: next season's points per game carried forward by
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
| QB | 243 | +100 | 343 (about the 4th QB) |
| RB | 132 | +25 | 157 |
| WR | 134 | +30 | 164 |
| TE | 113 | +40 | 153 |

At that level the league pays **$1.12 per expected point** for the coming season (in-season level:
$0.66), and one price fits stars and mid-tier players alike: average pricing error $7.4, against
$10.4 with the first-non-starter level, where mid-tier players cost $0.31 a point and stars $0.70.

## The four prices (`prices.csv`, `ours_jordan.csv`)

- **Season $**: rest of this season. Current salary is not subtracted (owed either way; in-season cap buys almost nothing).
- **Lifetime $**: Season $ plus each later contract season through 2029 (value minus salary, x0.88 a
  year), keeping or cutting each offseason, whichever is better (dead cap per the constitution), plus
  the best designation when the contract ends: tag at the site's tag price, mid-level extension
  ($15/$30/$45), or RFA at the going RFA price.
- **Market $**: auction (what next season would fetch at the auction) and trade (FantasyCalc's
  ordering, 10-team 1QB half PPR, put on our dollar scale, minus the cost of holding him through 2029:
  his salaries, then the auction price once his contract ends).
- **Our $**: Jordan's lineup gain this season (lineup re-picked each remaining week with byes; a
  waiver pickup always available at each position; the cheapest current player dropped to make room),
  plus Lifetime $ beyond this season. Season weight is 1.0 until the Phase 5 dial exists.

## Backtest 1: auctions 2020-2025 (258 skill-position buys)

| Predictor of what the coming season returned | Average miss | Ranking |
| --- | --- | --- |
| League price | $20.3 | 0.460 |
| Engine | $21.9 | 0.459 |
| 50/50 | $20.9 | 0.473 |

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

- RFAs cost **35% more per point** than the auction: $1.51 vs $1.12 per point of coming-season value.
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

The smoothed curve has a cliff after the first two picks (1.01 $183, 1.02 $141, 1.03-1.09 $54).

**Use or sell, today's prices** (2027 picks, one year out; FantasyCalc on our dollar scale vs use
value with its 90% band): mid 1st sell ($79 vs $35, band $32-65) and late 3rd sell ($11 vs $1);
everything else is a close call inside the band.

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
- The trade-market price leans on FantasyCalc alone; KTC and DynastyProcess values are not wired in.
- Our $ uses expected points; injury risk beyond position availability, and the dial, come in Phase 5.
- Designation slots are limited (2 RFA, 1 tag, 1 extension per team); rights are valued one player
  at a time, so a team with several tag-worthy players is overstated until the simulator allocates them.

## Files

Parameters: `params_market.csv`, `params_gap_shrink.csv`, `params_aging.csv`. Backtests:
`val_auction_*`, `val_rfa_*`, `val_picks_*`. Live: `prices.csv`, `ours_jordan.csv`.
Tests: `tests/test_valuation.py`.
