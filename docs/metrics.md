# Which number answers which question (principle P16)

Audited 2026-11-04 against every phase. "Fits" = the metric is the one built for that question.

| Question | Metric | Where | Status |
| --- | --- | --- | --- |
| How many points will he score; how far apart are two players? | Calibrated projections, gaps shrunk to measured size | projections/, params_gap_shrink.csv | Fits |
| Who should I start / is he worth a roster spot this season? | Points over the next startable player (in season) | prices.csv points_now; ours_jordan.csv lineup_points_now (Jordan's own lineup vs waiver) | Fits |
| What will he cost at the auction or in RFA? | Market price model: auction prices fit to points over the market-implied level; RFA price per point | params_market.csv, prices.csv market_auction, val_rfa_price.csv | Fits (it predicts cost, which is what it was fit to) |
| Is he worth his salary next season (keep, cut, bid, tag, RFA match, take on salary in a trade)? | Expected lineup points over waiver (lineup_value.py) against what that salary normally buys (price curve) | prices.csv lineup_/norm_/surplus_<year>, surplus_2027_29; designation_plan.csv (worth in points) | Fixed 2026-11-05 |
| What is a pick worth to use? | Lineup points over waiver the slot delivers, net of what the rookie salary normally buys, with the chance of no value | val_picks_slots.csv use_lineup; val_picks_odds.csv | Fixed 2026-11-05 (cap $ columns kept as the market lens) |
| What should I pay at the auction; where does an extra dollar buy nothing? | Price tiers: lineup points over waiver by price, step fit | params_price_tiers.csv (price_tiers.py) | New 2026-11-05 (P17) |
| Should I drop him for a pickup? | This season's lineup points and later surplus, each weighted by the dial's payout per point | ours_jordan.csv | Fixed 2026-11-05 (was points plus cap $ summed) |
| What would another owner pay? | FantasyCalc, labeled reference only | prices.csv fc_reference | Fits |
| Contend or rebuild; what is a point worth now vs later? | Payout per lineup point by season (simulator probe) | dial.csv | Fits |
| How did an owner do at the auction / with RFAs / tags / waiver finds? | Lineup points over waiver each season held, minus what the salary normally buys | grades_decisions.csv (grades.py) | Fixed 2026-11-04 (was price vs market level) |
| How well does an owner draft? | Player's points over waiver, first four seasons, minus the slot average | grades_decisions.csv rookie_pick | Fixed 2026-11-04 (was raw points) |
| How did an owner do in trades? | Payout $ actually won or lost: each played season re-run without the trade (finish shifted by the change in lineup rank), picks at expected value; the posture-weighted points grade kept beside it | grades_decisions.csv payout_grade | Fixed 2026-11-05 (Jordan: a title won is definite) |
| Why do some owners win more? | Lineup edge by acquisition channel, injury luck, finish vs lineup | owner_bridge.csv (diagnose.py) | New 2026-11-04 |

## Fixed 2026-11-05

Later seasons, the designation plan and pick use values are valued in lineup points against the price curve. The market model is kept as the cost estimate (what the auction or an RFA bid will charge) and for the simulator's auction clearing.
