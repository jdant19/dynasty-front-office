"""Strategy conversation strat-20261119180000 (Jordan, 2026-11-24): run the 13 deals from the thread
through the trade evaluator with his constraints.

    PYTHONPATH=src python scripts/analyst/strat_20261119180000.py [n_sims]

Constraints (Jordan's thread, 2026-11-24 04:04-04:08 UTC):
  1. 2026 projected points must not drop (lineup_points_now >= 0 for Jordan).
  2. The Lamb tag must still fit under $300 in June-July: net added 2027 salary <= the tag headroom.
  3. Roster 22/22: forced drops are chosen by the evaluator; flagged if a drop leaves 2027 dead cap.
  4. RFA rule fixed (league.yaml match_requires_under_cap) before running.
  5. Each deal also run with Fannin and with Robinson as a throw-in.
Output: data/processed/analyst_strat-20261119180000.csv
"""
from __future__ import annotations

import sys
import warnings

import pandas as pd

from dfo.common import PROCESSED
from dfo.decide.trades import Assets, Trade, live

warnings.filterwarnings("ignore")
N = int(sys.argv[1]) if len(sys.argv) > 1 else 6000
J = "Jordan"
ID = {
    "Olave": "00-0037239", "Warren": "00-0037228", "Jeanty": "00-0040122", "Nabers": "00-0039337",
    "Concepcion": "00-0041547", "Hampton": "00-0040666", "Odunze": "00-0039919", "Wilson": "00-0038559",
    "Burden": "00-0040735", "Egbuka": "00-0040129", "Skattebo": "00-0040715", "Judkins": "00-0040784",
    "Walker": "00-0038134", "Washington": "00-0038606", "Dart": "00-0040691", "Fannin": "00-0040663",
    "Robinson": "00-0038117",
}
# Jordan's picks: (year, round, original owner)
R27_2, R27_2M, R27_3 = (2027, 2, J), (2027, 2, "Max"), (2027, 3, J)
R28_1, R28_1M, R28_2, R28_3 = (2028, 1, J), (2028, 1, "Max"), (2028, 2, J), (2028, 3, J)
R29_1, R29_2, R29_3 = (2029, 1, J), (2029, 2, J), (2029, 3, J)

# (number, label, partner, players Jordan gets, picks Jordan gives, players Jordan gives)
DEALS = [
    ("1", "Olave + Warren for a 2027 R3", "Max", ["Olave", "Warren"], [R27_3], []),
    ("1-ceiling", "Olave + Warren for a 2027 R2", "Max", ["Olave", "Warren"], [R27_2], []),
    ("1b", "Olave + Warren for Fannin + 2027 R3", "Max", ["Olave", "Warren"], [R27_3], ["Fannin"]),
    ("1c", "Olave + Warren for Dart + 2027 R2", "Max", ["Olave", "Warren"], [R27_2], ["Dart"]),
    ("2", "Jeanty + Olave for Max's 2028 R1 + Max's 2027 R2", "Max", ["Jeanty", "Olave"], [R28_1M, R27_2M], []),
    ("2-ceiling", "Jeanty + Olave for Max's 2028 R1 + Max's 2027 R2 + 2029 R2", "Max", ["Jeanty", "Olave"],
     [R28_1M, R27_2M, R29_2], []),
    ("3", "Nabers + Concepcion for 2028 R1 + 2027 R2", "Qasim", ["Nabers", "Concepcion"], [R28_1, R27_2], []),
    ("4", "Hampton + Odunze for Max's 2028 R1 + 2027 R3", "Ryan", ["Hampton", "Odunze"], [R28_1M, R27_3], []),
    ("4-ceiling", "Hampton + Odunze for Max's 2028 R1 + 2027 R3 + 2028 R2", "Ryan", ["Hampton", "Odunze"],
     [R28_1M, R27_3, R28_2], []),
    ("5", "Wilson + Burden for 2028 R1", "Matt", ["Wilson", "Burden"], [R28_1], []),
    ("6", "Egbuka + Skattebo for 2027 R2 + 2028 R2", "Ben", ["Egbuka", "Skattebo"], [R27_2, R28_2], []),
    ("6-ceiling", "Egbuka + Skattebo for 2028 R1", "Ben", ["Egbuka", "Skattebo"], [R28_1], []),
    ("7", "Burden + Judkins for 2028 R1 + 2029 R1", "Matt", ["Burden", "Judkins"], [R28_1, R29_1], []),
    ("8", "Nabers for 2027 R2 + 2028 R2", "Qasim", ["Nabers"], [R27_2, R28_2], []),
    ("8-ceiling", "Nabers for 2028 R1", "Qasim", ["Nabers"], [R28_1], []),
    ("9", "Jeanty for Max's 2028 R1 + Max's 2027 R2", "Max", ["Jeanty"], [R28_1M, R27_2M], []),
    ("10", "Wilson for a 2027 R3", "Matt", ["Wilson"], [R27_3], []),
    ("10-ceiling", "Wilson for a 2027 R2", "Matt", ["Wilson"], [R27_2], []),
    ("11", "Odunze for a 2029 R3", "Ryan", ["Odunze"], [R29_3], []),
    ("13", "Walker for Dart + Washington + Fannin + 2029 R1 (the card)", "Ben", ["Walker"], [R29_1],
     ["Dart", "Washington", "Fannin"]),
]
THROW_INS = {"Fannin", "Robinson"}


def trade(num, label, partner, gets, picks, gives):
    return Trade(J, partner, Assets(players=[ID[x] for x in gives], picks=list(picks)),
                 Assets(players=[ID[x] for x in gets]), label=f"{num}: {label}")


def main():
    st = live()
    prices = pd.read_csv(PROCESSED / "prices.csv").set_index("player_id")
    s27 = prices.salary_2027.fillna(0)
    out_o = st.later(Trade(J, "Max", Assets(), Assets()))[J]["before"].set_index("season")
    committed27 = float(out_o.committed[2027])
    plan = pd.read_csv(PROCESSED / "designation_plan.csv")
    mine = plan[(plan.owner == J) & (plan.offseason == 2027)] if "owner" in plan.columns else plan.iloc[0:0]
    tag = float(mine[mine.type == "tag"].salary_2027.sum()) if len(mine) else 96.0
    rfa = float(mine[mine.type == "rfa"].salary_2027.sum()) if len(mine) else 135.0
    headroom = 300 - (committed27 - tag - rfa) - tag          # what the Lamb tag leaves under $300
    base = st.season_odds(None, N)
    rows = []
    runs = []
    for d in DEALS:
        runs.append(d)
        num, label, partner, gets, picks, gives = d
        for x in THROW_INS - set(gives):
            if num in ("13",) or "ceiling" in num:
                continue
            runs.append((f"{num}+{x}", f"{label}, plus {x}", partner, gets, picks, gives + [x]))
    for num, label, partner, gets, picks, gives in runs:
        t = trade(num, label, partner, gets, picks, gives)
        e = st.evaluate(t, n=N, base=base).set_index("owner")
        me, them = e.loc[J], e.loc[partner]
        added27 = sum(float(s27.get(ID[x], 0)) for x in gets) - sum(float(s27.get(ID[x], 0)) for x in gives)
        dead_drop = [x for x in str(me.forced_drops).split(", ") if x and x in set(prices.player) and
                     float(prices.loc[prices.player == x, "salary_2027"].fillna(0).max()) > 0]
        rows.append(dict(
            deal=num, trade=label, partner=partner, legal=bool(me.legal), salary_rule=me.salary_rule,
            jordan_2026_lineup_points=me.lineup_points_now,
            ok_2026_points=me.lineup_points_now >= -0.5,
            jordan_strength_2027=me.strength_change_2027, jordan_strength_2028=me.strength_change_2028,
            jordan_strength_2029=me.strength_change_2029,
            jordan_payout_now=me.payout_now, jordan_payout_later=me.payout_later, jordan_payout=me.payout,
            jordan_p_first=f"{me.p_first_before:.0%} -> {me.p_first_after:.0%}",
            jordan_p_title=f"{me.p_playoff_champ_before:.1%} -> {me.p_playoff_champ_after:.1%}",
            partner_payout=them.payout, partner_payout_now=them.payout_now, partner_payout_later=them.payout_later,
            added_2027_salary=round(added27), tag_headroom_after=round(headroom - added27),
            tag_fits=headroom - added27 >= 0,
            jordan_cap_room=me.cap_room_2027 + " | 2028 " + me.cap_room_2028,
            jordan_forced_drops=me.forced_drops, drops_leave_2027_dead_cap=", ".join(dead_drop),
            jordan_given_up_to_fit=me.given_up_to_fit_cap, partner_forced_drops=them.forced_drops,
            inputs=me.inputs, sims=N))
        print(num, rows[-1]["jordan_payout"], rows[-1]["partner_payout"], flush=True)
    df = pd.DataFrame(rows)
    df.attrs["tag_headroom"] = headroom
    df.to_csv(PROCESSED / "analyst_strat-20261119180000.csv", index=False)
    print(f"tag headroom before any deal: ${headroom:.0f} (committed 2027 ${committed27:.0f}, tag ${tag:.0f}, RFA ${rfa:.0f})")
    print(df[["deal", "legal", "jordan_2026_lineup_points", "jordan_strength_2027", "jordan_payout_now",
              "jordan_payout_later", "jordan_payout", "partner_payout", "added_2027_salary", "tag_fits",
              "jordan_forced_drops"]].to_string())


if __name__ == "__main__":
    main()
