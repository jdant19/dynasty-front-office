# Principles

Strategy beliefs the engine applies. Each has a rationale and the evidence behind it; changing one needs
evidence across seasons and Jordan's sign-off. Add the date and reason to the change log at the bottom.

| # | Principle | Rationale | Evidence / status |
| --- | --- | --- | --- |
| P1 | Maximize expected payout over the current season and the next three, haircut 0.88 per future year. | Regular-season 1st ($400) is the largest prize; future titles count equally but are less predictable. | Jordan, 2026-10 |
| P2 | Rebuild when future seasons offer more expected payout than the current one, judged by what added strength buys in each. | Raw odds can mislead: a strong young team can have better odds next year yet gain most by pushing now. | Design doc s1; computed by the dial (dial.csv) as payout per lineup point by season, from 2026-10-30 |
| P3 | Value an acquisition as the gain over the current starter after re-optimizing the lineup, not over zero. | A filled slot is not an adequate one; a 3-for-2 can be free if a replacement is available. | Design doc s2, s8 |
| P4 | Score decisions on end states: the move plus follow-on moves, against the best plan that skips it. | Single-move scoring misses replacements, cuts and chained designations. | Design doc s2 |
| P5 | In-season cap is worth close to nothing; next season's cap is priced at what it buys. | No carryover; FAAB buys only this season. | Jordan |
| P6 | Rights and designation history travel with the player and are priced as part of him. | A player with RFA or extension rights is control, not a rental. | Jordan; v1 Walker error |
| P7 | Picks are valued on use value and market value; sell where the market pays more than use. | Hit odds by slot come from this league's own drafts; the price that matters is the one a buyer will actually pay. | Tested 2026-10-30 (docs/valuation.md): in this league's 20 scoreable pick trades the pick buyer came out ahead 13-17 times (+$29 to +$63 a trade, depending on how the trade season is counted), so owners here have sold picks cheap; FantasyCalc prices mid 1sts above what that slot has produced here ($79 vs $35). Hypothesis not supported inside the league; selling is right only at outside-market prices. |
| P8 | A declined offer costs nothing; re-offer freely, but track per-owner offer fatigue. | Repeated game with nine counterparties. | Jordan |
| P9 | Weeks 17–18 are the most volatile stretch; plan depth and variance for them. | NFL rest decisions and seeding races; season leads can flip in two weeks. | Jordan |
| P10 | Kickers and defenses are valued against weekly streaming, with no option value. | Their seasons are mostly noise (rest-of-season gaps are 65-72% noise), so a bad one cannot be spotted early enough to cut; the best unrostered one each week is the real alternative. | 2020-2025 auction backtest: $1 K/DEF buys returned $2-4 over streaming |
| P11 | Replacement depends on when a player would have to be replaced: in season, the next startable player; for later seasons, the level the auction prices imply. | With 18-man rosters waivers are thin in season, but every August mid-tier production (and any QB outside the top few) sells for $1-2. | Auction fit 2020-2025: QB +100, TE +40, WR +30, RB +25 points over the first non-starter; one price per point ($1.12) then fits stars and mid-tier alike (MAE $7.4 vs $10.4) |
| P12 | A season is worth E[max(0, points - replacement)], never below zero. | A season below replacement is benched or cut, so upside counts and downside stops at replacement. | Coming-season values built this way match what auction buys returned |
| P14 | Keep this-season value and contract value (later seasons) as two numbers; weight them by the dial, never sum them blindly. | Pushing now and building need different answers from the same player; a single total hides which one a move serves. | Jordan, 2026-10-30 |
| P15 | Points for what a player adds; dollars only for real cap money (salaries, bids, tag prices, cap space, auction prices, cap saved). Payout dollars only for the dial's answer. | One "$" for three different things confused the review; points are what win a season, cap saved is what buys more points. | Jordan, 2026-10-30 |
| P13 | Projection gaps are shrunk to their measured size before pricing; later seasons follow position aging curves from next season's projection. | Projections spread players too far apart (rest of season: RB/WR 0.84, QB 0.78, TE 0.61) and per-horizon fits jumped around for single players. | params_gap_shrink.csv, params_aging.csv (fit 2026-10-30) |
| P16 | Every number answers one question; use the metric built for that question. Cap decisions (bid, keep, cut, tag, RFA, take on salary): what the player adds to the starting lineup over the waiver level (or the next-best starter) against what his salary normally buys. Drafting skill: the pick's production against the expected value of that slot. Why owners win: each owner's lineup edge split by where the starters came from, plus injury luck. Trades: both sides, every season affected (trade season included), weighted by each owner's posture then. | A metric built for one question gives wrong answers to another: judging owners by price against a market replacement level graded half of all auction buys at $0 (69 of them started 8+ weeks) and ranked the most successful owners last. | Jordan, 2026-11-04; owner grades rebuilt on it (grades.py). Price curve from 2014-2025 auctions: a $1 skill buy starts for 31-64 points over waiver, about 1.6 points per extra $. Phase 4 later-season values, the designation plan and pick use values moved to lineup points 2026-11-05 (lineup_value.py); the market model stays only as the cost estimate. Trades graded in payout $ from what actually happened (Jordan 2026-11-05: a title won is definite, a pick only a chance). |
| P17 | Buy in price tiers. Within a tier an extra auction dollar buys no more lineup points; between tiers the value steps up. Bid at the bottom of a tier, or skip the tier and take several cheap chances. | 2014-2025 auctions, lineup points over waiver started in the season bought: RB $1-27 average 56, $29-47 113, $48+ 187; WR $1-15 47, $16-41 92, $42+ 136; TE $1-16 39, $19+ 85; QB $1-12 69, $13+ 149 (params_price_tiers.csv; both halves of the history agree except TE tier 2, stronger since 2020). | Jordan, 2026-11-05 (asked whether cliffs exist); fit by price_tiers.py. |

## Change log
- 2026-10-27: initial set from the design doc.
- 2026-10-30: P7 tested; P10-P13 added with the valuation engine (Phase 4); P14 and P15 from Jordan's review; P2 now computed by the dial.
- 2026-11-04: P16 from Jordan (right metric for each decision); owner grades rebuilt on it.
- 2026-11-05: P16 extended to Phase 4 and trades (payout $); P17 price tiers from Jordan's cliff question.

## P18. Decisions are scored with three stable conversions (Jordan, 2026-11-08)

1. Salary <-> points: what $1 of salary normally buys in lineup points over waiver (1.6 points, the
   auction price curve). This is the only price per point.
2. Points <-> finish: 0.011 places in the final standings per lineup point over a season (2014-2025,
   se 0.0009), the same for every team.
3. Finish <-> payout: the payout table ($400/$200/$100 regular season, a top-four finish also carries
   the $75 average playoff payout).

Where a team stands enters only through its finish odds: this season from the season simulator run
from the actual standings (both rosters changed for a trade, so the partner's gain counts, and
players' NFL playoff games count through the playoff draft); later seasons at the league-average
value of a place ($47.50, so $0.53 a point), because finishes do not carry over in this league (rank
correlation 0.09 year to year, 2013-2025). Replaces the per-team payout-per-point dial for later
seasons and for trades (`valuation/conversions.py`, `params_conversions.csv`). For small in-season
moves (waivers, drops) the simulator's slope at the current standings is the same conversion in
small form. Evidence: Jordan's review of the trade exit test; the Baldwin 2016 grade fell from $300
to about $160 once whole-place rounding was replaced by the points slope.
