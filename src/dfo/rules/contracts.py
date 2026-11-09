"""Contract, designation, cut and trade-legality math. All rules come from config/league.yaml."""
from __future__ import annotations

import math
from dataclasses import dataclass

from dfo.common import load_config


def _rules() -> dict:
    return load_config("league")


def money(x: float) -> int:
    """League money is whole dollars, rounded to nearest (half up)."""
    return int(math.floor(x + 0.5))


def salary_schedule(first_year: int, years: int, rookie: bool = False) -> list[int]:
    """Salaries for each year of a new contract.

    Free-agent contracts raise 5% / 10% / 15% on the prior year, at least $1 a year.
    Rookie-draft contracts rise $1 a year.
    """
    r = _rules()["contracts"]
    lo, hi = r["years"]
    if not lo <= years <= hi:
        raise ValueError(f"contract length {years} outside {lo}-{hi}")
    out = [int(first_year)]
    if rookie:
        step = r["rookie"]["raise_per_year"]
        while len(out) < years:
            out.append(out[-1] + step)
        return out
    factors = [r["raises"]["year2"], r["raises"]["year3"], r["raises"]["year4"]]
    for f in factors[: years - 1]:
        out.append(max(money(out[-1] * f), out[-1] + r["min_raise_per_year"]))
    return out


def rfa_starting_bid(last_salary: int) -> int:
    """RFA bidding opens at 110% of last salary, at least last salary + $1.

    An in-season $0 add becomes a $1 contract in the offseason, so its RFA start is $2.
    """
    conv = _rules()["in_season"]["offseason_conversion"]["zero_dollar_contracts_become"]
    base = max(int(last_salary), conv)
    return max(money(base * 1.10), base + 1)


def franchise_tag_cost(top5_salaries: list[float]) -> int:
    if len(top5_salaries) != 5:
        raise ValueError("franchise tag needs the five highest salaries at the position")
    floor = _rules()["designations"]["franchise_tag"].get("minimum", 0)
    return max(floor, money(sum(top5_salaries) / 5))


def mle_eligible(prior_salary: int, on_rookie_draft_contract: bool) -> bool:
    e = _rules()["designations"]["mid_level_extension"]["eligible_if"]
    return prior_salary < e["prior_salary_lt"] and not on_rookie_draft_contract


def cut_dead_cap(remaining_salaries: list[int]) -> list[int]:
    """Dead cap by season for cutting a player whose remaining salaries start with the current season."""
    c = _rules()["cuts"]
    pct = [c["current_year_dead_cap"], c["future_year_dead_cap"]["year2"],
           c["future_year_dead_cap"]["year3"], c["future_year_dead_cap"]["year4"]]
    return [money(s * p) for s, p in zip(remaining_salaries, pct)]


@dataclass
class TradeSide:
    player_salaries: list[int]
    cap_compensation: int = 0      # this season's cap this side SENDS in the trade
    cap_room: int | None = None    # this side's cap space before the trade, if known


def trade_salaries_legal(a: TradeSide, b: TradeSide) -> tuple[bool, str]:
    """Salary-matching rule (league.yaml trades.salary_match): if any player is over $20, combined
    salaries must be within 25% of the higher side. Cap moved as compensation is counted with the
    lower side (the constitution's example: $75 + $20 sent vs $125; Jordan 2026-11-08: the Puka trade,
    $19 + $10 received vs $47). Even outside 25%, the trade is legal when the team taking on more salary
    has the cap space to absorb the difference (Jordan 2026-11-08). Picks and considerations are outside it."""
    t = _rules()["trades"]["salary_match"]
    all_salaries = a.player_salaries + b.player_salaries
    if not any(s > t["applies_if_any_player_salary_gt"] for s in all_salaries):
        return True, "no player over the threshold; matching not required"
    sa, sb = sum(a.player_salaries), sum(b.player_salaries)
    comp = a.cap_compensation + b.cap_compensation
    hi, lo = max(sa, sb), min(sa, sb) + comp
    hi, lo = max(hi, lo), min(hi, lo)
    if lo >= hi * (1 - t["tolerance"]):
        return True, f"${lo} vs ${hi}: within {int(t['tolerance'] * 100)}% of the higher side"
    # the team taking on salary: receives the higher side
    taker, gets, sends, cap_in = (b, sa, sb, a.cap_compensation) if sa > sb else (a, sb, sa, b.cap_compensation)
    need = gets - sends - cap_in + taker.cap_compensation
    if taker.cap_room is not None and taker.cap_room >= need:
        return True, (f"${lo} vs ${hi}: outside {int(t['tolerance'] * 100)}%, but the team taking on salary "
                      f"has ${taker.cap_room} of space for the ${need} difference")
    room = "unknown" if taker.cap_room is None else f"${taker.cap_room}"
    return False, (f"${lo} vs ${hi}: outside {int(t['tolerance'] * 100)}% of the higher side, and the team "
                   f"taking on salary has {room} of space for the ${need} difference")
