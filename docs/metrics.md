# Which number answers which question (principle P16)

Audited 2026-11-04 against every phase. "Fits" = the metric is the one built for that question.

| Question | Metric | Where | Status |
| --- | --- | --- | --- |
| How many points will he score; how far apart are two players? | Calibrated projections, gaps shrunk to measured size | projections/, params_gap_shrink.csv | Fits |
| Who should I start / is he worth a roster spot this season? | Points over the next startable player (in season) | prices.csv points_now; ours_jordan.csv lineup_points_now (Jordan's own lineup vs waiver) | Fits |
| What will he cost at the auction or in RFA? | Market price model: auction prices fit to points over the market-implied level; RFA price per point | params_market.csv, prices.csv market_auction, val_rfa_price.csv | Fits (it predicts cost, which is what it was fit to) |
| Is he worth his salary next season (keep, cut, bid, tag, RFA match, take on salary in a trade)? | Should be: expected lineup points over waiver against what that salary normally buys (price curve) | prices.csv points_2027-29, cap_saved; designation_plan.csv; picks.py use value | **Mismatch**: later seasons use points over the market-implied level (a price model's zero point) as the value. Proposed fix below |
| What is a pick worth to use? | Should be: lineup points over waiver the slot delivers, net of rookie salary, with bust odds | val_picks_*.csv | **Same mismatch** (market level); bust odds fit |
| What would another owner pay? | FantasyCalc, labeled reference only | prices.csv fc_reference | Fits |
| Contend or rebuild; what is a point worth now vs later? | Payout per lineup point by season (simulator probe) | dial.csv | Fits |
| How did an owner do at the auction / with RFAs / tags / waiver finds? | Lineup points over waiver each season held, minus what the salary normally buys | grades_decisions.csv (grades.py) | Fixed 2026-11-04 (was price vs market level) |
| How well does an owner draft? | Player's points over waiver, first four seasons, minus the slot average | grades_decisions.csv rookie_pick | Fixed 2026-11-04 (was raw points) |
| How did an owner do in trades? | Both sides' lineup points net of salary, trade season + 2, posture-weighted, picks and cap included | grades_decisions.csv trade | Fixed 2026-11-04 (trade season was left out) |
| Why do some owners win more? | Lineup edge by acquisition channel, injury luck, finish vs lineup | owner_bridge.csv (diagnose.py) | New 2026-11-04 |

## Proposed fix for the mismatch (needs Jordan's OK)

Value later seasons the way grades.py judges past decisions: expected lineup points over waiver for
a typical team (fit from preseason projection to realized started points, 2014-2025) minus the price
curve at his salary. The market model stays as the cost estimate (what the auction will charge),
not the value. This changes cap_saved, the designation plan (Gibbs, Bijan) and pick use values.
