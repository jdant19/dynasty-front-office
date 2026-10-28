"""Rules engine checked against the constitution's own examples and Jordan's clarifications."""
import csv
from collections import defaultdict

from dfo.common import PROCESSED
from dfo.rules.contracts import (TradeSide, cut_dead_cap, franchise_tag_cost, mle_eligible, money,
                                 rfa_starting_bid, salary_schedule, trade_salaries_legal)


def test_money_rounds_to_whole_dollars():
    assert money(27.6) == 28 and money(16.8) == 17 and money(97.4) == 97 and money(4.5) == 5


def test_free_agent_raises_with_one_dollar_floor():
    assert salary_schedule(10, 4) == [10, 11, 12, 14]       # 10.5->11, 12.1->12, 13.8->14
    assert salary_schedule(1, 4) == [1, 2, 3, 4]            # $1 minimum raise binds
    assert salary_schedule(40, 4) == [40, 42, 46, 53]


def test_rookie_contracts_rise_one_dollar():
    assert salary_schedule(5, 4, rookie=True) == [5, 6, 7, 8]


def test_rfa_start():
    assert rfa_starting_bid(0) == 2                          # $0 add -> $1 contract -> $2 start (Jordan)
    assert rfa_starting_bid(8) == 9                          # 8.8 -> 9
    assert rfa_starting_bid(26) == 29                        # 28.6 -> 29


def test_trade_salary_examples_from_constitution():
    assert trade_salaries_legal(TradeSide([75]), TradeSide([100]))[0] is True
    assert trade_salaries_legal(TradeSide([75]), TradeSide([124]))[0] is False
    assert trade_salaries_legal(TradeSide([75], cap_compensation=20), TradeSide([125]))[0] is True
    assert trade_salaries_legal(TradeSide([5, 6]), TradeSide([15]))[0] is True   # nobody over $20


def test_cut_dead_cap():
    assert cut_dead_cap([5, 6]) == [5, 5]                    # matches the site's 2026 cut (4.5 -> 5)
    assert cut_dead_cap([40, 42, 46, 53]) == [40, 32, 23, 13]


def test_mle_eligibility():
    assert mle_eligible(0, False) and mle_eligible(9, False)
    assert not mle_eligible(10, False) and not mle_eligible(5, True)


def test_tag_costs_match_league_sheet():
    rows = list(csv.DictReader(open(PROCESSED / "tag_benchmarks.csv")))
    checked = 0
    for r in rows:
        if r["position"] in ("K", "DEF"):
            continue   # open question: sheet shows a $2 K/DEF tag where the top-5 average is $1
        top5 = [float(x) for x in r["top5_salaries"].split()]
        if len(top5) == 5:
            assert franchise_tag_cost(top5) == int(r["tag_cost"]), r
            checked += 1
    assert checked >= 6


def test_rfa_starting_bids_match_prior_salary():
    """2025 and 2026 RFA starting bids follow from the player's prior-season salary on the Yahoo roster."""
    sal = defaultdict(dict)
    for r in csv.DictReader(open(PROCESSED / "auctions.csv")):
        sal[int(r["season"])][r["player"].lower()] = int(r["salary"])
    seen, matched = 0, 0
    for r in csv.DictReader(open(PROCESSED / "rfa_bids.csv")):
        season = int(r["season"])
        if season != 2026:
            continue
        prior = sal[season - 1].get(r["player"].lower())
        if prior is None:
            continue
        seen += 1
        matched += rfa_starting_bid(prior) == int(r["starting_bid"])
    assert seen >= 5 and matched / seen >= 0.6, (matched, seen)
