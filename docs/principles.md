# Principles

Strategy beliefs the engine applies. Each has a rationale and the evidence behind it; changing one needs
evidence across seasons and Jordan's sign-off. Add the date and reason to the change log at the bottom.

| # | Principle | Rationale | Evidence / status |
| --- | --- | --- | --- |
| P1 | Maximize expected payout over the current season and the next three, haircut 0.88 per future year. | Regular-season 1st ($400) is the largest prize; future titles count equally but are less predictable. | Jordan, 2026-10 |
| P2 | Rebuild when future seasons offer more expected payout than the current one, judged by what added strength buys in each. | Raw odds can mislead: a strong young team can have better odds next year yet gain most by pushing now. | Design doc s1 |
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
| P13 | Projection gaps are shrunk to their measured size before pricing; later seasons follow position aging curves from next season's projection. | Projections spread players too far apart (rest of season: RB/WR 0.84, QB 0.78, TE 0.61) and per-horizon fits jumped around for single players. | params_gap_shrink.csv, params_aging.csv (fit 2026-10-30) |

## Change log
- 2026-10-27: initial set from the design doc.
- 2026-10-30: P7 tested; P10-P13 added with the valuation engine (Phase 4).
