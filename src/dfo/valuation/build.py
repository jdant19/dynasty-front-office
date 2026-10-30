"""Rebuild every valuation table in order.

    PYTHONPATH=src python -m dfo.valuation.build

1. rankings at each offseason event   preseason.py   ranks_at_{auction,rfa}.csv
2. gap calibration                    calibrate.py   params_gap_shrink.csv
3. aging curves                       aging.py       params_aging.csv
4. auction fit and backtest           auction.py     params_market.csv, val_auction_*.csv
5. RFA backtest                       rfa.py         val_rfa_*.csv
6. current prices                     prices.py      prices.csv
7. picks                              picks.py       val_picks_*.csv
8. Our $ for Jordan                   ours.py        ours_jordan.csv
"""
from __future__ import annotations

import warnings


def main() -> None:
    warnings.filterwarnings("ignore")
    from dfo.valuation import aging, auction, calibrate, ours, picks, preseason, prices, rfa
    for ev in ("auction", "rfa"):
        preseason.build(ev)
    calibrate.fit()
    aging.fit()
    auction.summarize(auction.backtest())
    rfa.summarize(rfa.backtest())
    prices.build()
    real = picks.realized_slot_values()
    slots, _ = picks.slot_curve(real)
    picks.market_vs_use(slots)
    picks.trade_summary(picks.pick_trades(real))
    ours.build("Jordan")
    print("valuation rebuilt")


if __name__ == "__main__":
    main()
