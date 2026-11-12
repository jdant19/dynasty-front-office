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
- Bids: $1 when the move is worth $1+ of payout, else $0, never more than the team's cap room
  (`rules/cap.py`: cap - counting salary - this season's dead money + traded cap). At or over the cap
  only $0 adds are allowed (league.yaml `cap.at_or_over_cap`). Jordan (2026-11-08): owners have bid
  more than $1 in past seasons, but only 2026 is on record (one $2 win, the rest $0/$1).
- Defenses two weeks out (`decide/streaming.py`, `params_stream_matchup.csv`): the rate is adjusted for
  the opponent (points defenses have scored against it so far, its average implied total so far).
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

### K/DEF matchups beyond the coming week (back-test 2021-2025, `val_stream_backtest.csv`)

At each week 4-14, from the units ranked 11-20 by scoring rate (the free-agent pool), pick one by rate
alone or by rate + opponent; points scored k weeks later, 55 decisions per row:

| Unit | Weeks ahead | Pool average | Pick by rate | Pick by rate + opponent | Gain (se) | Seasons better |
|---|---|---|---|---|---|---|
| DEF | 1 | 6.3 | 6.0 | 6.9 | +0.9 (0.9) | 4 of 5 |
| DEF | 2 | 6.4 | 6.0 | 8.4 | +2.4 (0.9) | 4 of 5 |
| DEF | 3 | 6.2 | 6.8 | 8.1 | +1.2 (0.7) | 5 of 5 |
| K | 1 | 8.2 | 8.4 | 8.2 | -0.2 (0.8) | 2 of 5 |
| K | 2 | 8.3 | 8.5 | 7.5 | -1.1 (0.7) | 2 of 5 |
| K | 3 | 8.4 | 8.4 | 8.8 | +0.4 (0.8) | 3 of 5 |

Used where the gain is more than two standard errors: defenses two weeks out. The coming week already
uses the weekly projection (betting lines). Three weeks out for defenses is positive every season but
under the bar, so it is not used. Kicker schedules add nothing.

Known limits: rivals' bids are not modeled (no bid records before 2026).

## Trades (`decide/trades.py`, `decide/trades_history.py`)

Question: does the deal, with the moves it forces, raise this team's expected payout over skipping it?
Scored with the three stable conversions (P18, `valuation/conversions.py`): $1 of salary buys 1.6
lineup points; a lineup point over a season is 0.011 places; places pay per the payout table.

- This season: the season simulator from the actual standings, rosters before and after the trade
  (both teams changed, same random draws), regular season plus the playoff draft and games. The
  change in each team's finish odds, through the payout table, is the this-season value.
- Later: players' surplus 2027-29 (lineup points over what the salary normally buys, keep/cut each
  season, x0.88 a year), dead cap of forced drops, picks at their slot or expected slot (rookie deal
  points, x0.88 a year), next season's cap at 1.6 points a dollar; at the league-average $0.53 a point
  (finishes do not carry over year to year). Also shown as auction dollars (points / 1.6).
- Over the roster limit, the team drops whoever costs it least; under it, a waiver pickup fills.
- Legal: the 25% salary rule (cap compensation counts with the lower side), legal outside it when the
  team taking on salary has the space (Jordan 2026-11-08); roster limit; Week 14 deadline.
- History (2014-2024): the same parts with what was known then (rates before the trade week, players on
  a run of missed games at the share of the season such players went on to play, contract salaries).
  No weekly results before 2026, so each team's finish odds come from its lineup strength at the trade
  (historical spread around it), and the partner's change is not re-simulated.
- The site's rejected and cancelled trades (3 rejected, 2 cancelled, April-October 2026) were tests
  (Jordan 2026-11-08) and are not evidence of what owners accept.

Exit test: the approved sample (one trade per season, `decide_trade_sample.csv`, approved 2026-11-08)
and the four 2026 site trades, `decide_trades_review_history.csv` and `decide_trades_review_2026.csv`.
Outcome grades (`grades_decisions.csv` payout grades) since 2026-11-09: both teams' lineups rebuilt with
the trade undone (over the next best option), finish moved 0.011 places a point, payouts interpolated.

| Trade | At the time (engine) | Outcome |
|---|---|---|
| 2014 Jordan-Matt (Rodgers) | Jordan +$61, Matt -$36 | Jordan +$153, Matt +$57 |
| 2015 Jordan-Ryan (Doug Martin for a 1st + two 2nds) | Jordan -$74, Ryan +$91 | Jordan -$168, Ryan +$514 |
| 2016 Ryan-Ben (Baldwin for a 1st + 3rd) | Ben +$17, Ryan -$18 | Ben +$3, Ryan +$78 |
| 2018 Jordan-Ryan (Gordon, Baldwin for a 1st, Sanders) | Jordan -$30, Ryan +$67 | Jordan -$103, Ryan +$131 |
| 2019 Qasim-Matt (Wilson for a 2nd) | Matt +$17, Qasim +$5 | Matt +$288 (Tee Higgins), Qasim -$74 |
| 2020 Ben-Jordan (Elliott, #21 for Brown, Harris, 2nds, cap) | Jordan +$83, Ben -$81 | Ben +$337, Jordan -$139 |
| 2021 Ben-Qasim (#10, #30 for Hopkins) | Ben +$28, Qasim -$80 | $0 / $0 |
| 2022 Jordan-Ben (Adams, Geno for six players + cap + pick) | Ben +$28, Jordan +$16 | Ben +$229, Jordan +$126 |
| 2023 Jordan-Qasim (#6 for #10 + Doubs) | Jordan +$36, Qasim -$23 | Jordan +$294, Qasim -$70 |
| 2024 Matt-Russ (#10 for $20 cap) | Matt +$18, Russ -$18 | Matt -$6, Russ +$54 |

The engine and the outcome pick the same winner in 6 of the 9 decided trades; the misses are 2016
(Ryan's 1st seed), 2020 (Gibson at #21) and 2024.

| 2026 trade | This season (finish odds before -> after) | Later (auction $) | Total |
|---|---|---|---|
| Jordan-Zach (#8 for Bryant, #27), Aug 2 | Jordan +$2 | Jordan +$46 (+$39) | Jordan +$41, Zach -$39 |
| Jordan-Max (Aiyuk, Herbert, Hubbard, Egbuka, picks for Hurts, Collins, Cooper), Aug 5 | Jordan 1st 23% -> 17% (-$33); Max 1st 20% -> 27% (+$42) | Jordan +$173 (+$148) | Jordan +$115, Max -$106 |
| Russ-Max (Puka, Tuten, $10 for Brown, Pierce), Oct 16 | Russ top 4 27% -> 73% (+$136); Max top 4 85% -> 34% (-$151) | Russ +$152 (+$130) | Russ +$266, Max -$281 |
| Jordan-Ben (Lamb, Mahomes, Robinson for Aiyuk, Herbert, Skattebo, Coleman, Egbuka, 2027 1st, $30 2027), Oct 26 | Jordan 1st 5% -> 16%, top 4 67% -> 87% (+$96); Ben -$1 | Jordan -$89 (-$76) | Jordan +$20, Ben +$75 |

2026 rosters and projections are as of each trade; player values and the pick curve are today's.

## Three-year outlook (`decide/outlook.py`)

Question: what does my team look like in 2027-29, and what does a move do to it?

- Per season: players under contract (signed salaries plus the designation plan), cap committed
  (salaries, dead money, traded cap, rookie salaries for picks owned), cap room, the best lineup from
  those players, what the room buys at that August's auction (the season's clearing price), rookies
  from picks owned; the sum is the team's strength, and the future simulator turns all ten teams'
  strengths into expected payout.
- RFA matches may take a team over the cap (tags and extensions may not), so a plan fits when everything
  but its matches fits; the team then buys nothing at auction. A plan that still does not fit gives
  something up: the one release that leaves the strongest team if one is enough, otherwise planned
  designations first (free to skip), then contracts (75% dead).
- A trade rebuilds both teams' outlooks; the change in strength, x0.88 a year, is the trade's later
  value (replaces adding up each player's surplus, so bench depth and cap with no room count for what
  they really do). Output: `decide_outlook_<owner>.md` and `.csv`.
- Jordan's 2027 plan (Jordan 2026-11-10): tag Lamb ($96) first, then match Gibbs ($98) over the cap.
  Model, 2026-11-10 (expected 2027 payout): both $84 (room -$20), Gibbs only $105, Lamb only $94,
  neither $136 ($172 of room at $0.94 a point). The ranking rests on the auction's clearing price per
  point; scarcity of elite players at auction is not in it.

## Trade search (`decide/trade_search.py`)

Question: which deals raise Jordan's expected payout the most, and what should he offer?

- Quick pass, every partner: each of the partner's players who would add 8+ lineup points to Jordan's
  team this season, for packages of up to three of Jordan's players (the cheapest for him to lose,
  plus his biggest salaries as fillers; three-player packages only when salaries must match); illegal
  packages dropped; the best four per target re-scored through both teams' three-year outlook.
- A partner still chasing the top four (simulator 40%+, or Jordan's dated read in observations.csv)
  must not lose this season in the deal; contenders do not sell for later value.
- Picks are valued by slot tier, not round (`valuation/pick_tiers.py`, `params_pick_tiers.csv`, drafts
  2014-2022): lineup points over what the rookie salary buys, four seasons at 0.88 a year. Picks 1-3:
  227 on average (22% give nothing); picks 4-9: 110 (median 29, 39% nothing); picks 10-30: 44 (median
  0, 58% nothing). A second or third is worth what the last pick of the first round is. A future
  pick averages the slots it could be: next year's from the original team's simulated finish, later
  years every slot in the round; x0.88 a year until the draft.
- Picks settle the gap, because picks are what this league trades in (92% of the 52 trades 2014-2024
  included a pick, cap moved in 54%; Jordan's partners got picks in 76% of his trades; Jordan
  2026-11-10). Every set of up to two of Jordan's picks is tried: opening (the smallest add leaving the
  partner $5 ahead), target (the most even split), walk-away (the most Jordan can add and stay ahead).
  No cap is offered while his 2027 plan is over the cap.
- Full pass: the season re-simulated with both rosters changed. Each idea shows Jordan's cap room and
  strength change for 2027, 2028 and 2029 and anything his plan would have to give up.
- Players Jordan asks about (`--targets`) are always evaluated; ideas where no version leaves both
  sides ahead are listed separately.
- Output: `decide_trade_search.md` and `.csv`, status "idea" until Jordan says a deal was proposed,
  with a draft message each (never sent).

Known limits: one player from the partner per deal; acceptance odds are not modeled (no real declines on
record); a player's planned designation travels with him at the planned salary.

## Other owners' likely plans (`decide/owner_plans.py`)

The model's read of each owner's 2027 designations, from how owners have behaved (2019-2026), for
Jordan to review: `decide_owner_plans_2027.md`; corrections in `config/owner_plans.yaml` win.

- RFA costs nothing to designate (the owner can decline to match) and owners use 1.2-1.9 of their two
  slots a year: each owner designates his two most valuable RFA-eligible players someone would bid on.
- Matching: 90% of RFAs valued $50+ were matched, 76% at $25-50, 69% below (101 RFAs 2020-2026).
- RFA prices ran 1.08x the auction model at $50+, 1.50x at $25-50, 1.79x below, and bids are limited
  to the bidder's cap room.
- Tags: used in 44-86% of offseasons, on stars, usually at a loss; an owner tags his best tag-eligible
  player not otherwise designated when his market value is at least half the tag cost.
- Auction stars: $50+ players sold at 0.89-1.02 of the model's price 2020-2025 (Jefferson and Lamb $93
  vs $93, McCaffrey $108 vs $105 and $114), so the model already prices the premium; in scenarios a
  star never goes below his market price unless no team that needs players can pay it.

## Offseason plan scenarios (`decide/offseason.py`)

Question: which designations (RFA match, tag, let go) give the best team and payout?

- Each scenario is played through the 2027 auction player by player: other teams follow their likely
  plans (`designation_plan.csv`); everyone not kept is the pool; prices are each player's market price
  scaled so the league's room is spent; Jordan buys the set that adds most to his lineup within his
  room; a team over the cap (through RFA matches only) is locked out and takes $0 leftovers; the rest
  buy in price order. Ten strengths go through the future simulator; 2028-29 from the outlook.
- Output: `decide_offseason_2027.md`, `.csv`, `decide_offseason_2027_auction_prices.csv`.
- 2026-11-12, with the behavioral owner plans: no other team is likely to have more than about $80 of
  2027 room once its own designations are paid, so Gibbs's RFA price tops out near $78-80. Expected
  2027 payout: match Gibbs and buy at auction (Lamb to the auction) $131; let both go $125; tag Lamb +
  match Gibbs $121; tag Lamb and let Gibbs go $111. Range over other owners' match outcomes: about $5.
- History (2014-2025, 115 team-seasons): teams that spent $0-25 at auction (15) finished 4.7 on average;
  $26-175 finished 5.4-5.8; $176+ (5) finished 5.0.

Known limits: other teams' plans are the model's, not confirmed; RFA prices for other teams' players
are market prices; one auction path per scenario (no bidding randomness yet).
