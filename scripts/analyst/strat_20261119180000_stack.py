"""Deal 12 (stack): Max's Olave + Warren deal and Qasim's Nabers deal together, Jordan's side.
Run after strat_20261119180000.py; appends rows to analyst_strat-20261119180000.csv."""
import sys
import warnings

import pandas as pd

sys.path.insert(0, "scripts/analyst")
from strat_20261119180000 import ID, J, N, R27_2, R27_3, R28_2  # noqa: E402

from dfo.common import PROCESSED
from dfo.decide.trades import Assets, Trade, live

warnings.filterwarnings("ignore")
st = live()
base = st.season_odds(None, N)
s27 = pd.read_csv(PROCESSED / "prices.csv").set_index("player_id").salary_2027.fillna(0)
rows = []
for num, label, gives, picks in [
        ("12", "Stack: deal 1 (Olave + Warren for a 2027 R3) + deal 8 (Nabers for 2027 R2 + 2028 R2)", [], [R27_3, R27_2, R28_2]),
        ("12c", "Stack: deal 1c (Olave + Warren for Dart + 2027 R2) + deal 8 with Max's 2027 R2 instead of Jordan's",
         ["Dart"], [R27_2, (2027, 2, "Max"), R28_2])]:
    gets = ["Olave", "Warren", "Nabers"]
    t = Trade(J, "Max", Assets(players=[ID[x] for x in gives], picks=picks), Assets(players=[ID[x] for x in gets]), label=label)
    me = st.side(J, t.b_gives, t.a_gives)
    ids = {v: k for k, v in st.names.items()}
    drops = [ids[x] for x in me["forced_drops"].split(", ") if x in ids]
    lat = st.later(t, {J: drops})[J]
    after = st.season_odds({ID[x]: J for x in gets} | {ID[x]: "Max" for x in gives}, N).loc[J]
    now = round(after.exp_payout - base.loc[J].exp_payout)
    later = round(lat["later_points"] * st.conv["dollars_per_point_later"])
    added = sum(float(s27.get(ID[x], 0)) for x in gets) - sum(float(s27.get(ID[x], 0)) for x in gives)
    rows.append(dict(deal=num, trade=label, partner="Max + Qasim", legal=True, jordan_2026_lineup_points=me["lineup_points_now"],
                     ok_2026_points=me["lineup_points_now"] >= -0.5,
                     jordan_strength_2027=lat["delta"][2027], jordan_strength_2028=lat["delta"][2028], jordan_strength_2029=lat["delta"][2029],
                     jordan_payout_now=now, jordan_payout_later=later, jordan_payout=now + later,
                     jordan_p_first=f"{base.loc[J].p_first:.0%} -> {after.p_first:.0%}",
                     jordan_p_title=f"{base.loc[J].p_playoff_champ:.1%} -> {after.p_playoff_champ:.1%}",
                     added_2027_salary=round(added), tag_headroom_after=round(85 - added), tag_fits=85 - added >= 0,
                     jordan_cap_room=f"{int(lat['before'].set_index('season').cap_room[2027])} -> {int(lat['after'].set_index('season').cap_room[2027])}",
                     jordan_forced_drops=me["forced_drops"], inputs=st.made_at, sims=N))
    print(rows[-1])
out = pd.read_csv(PROCESSED / "analyst_strat-20261119180000.csv")
out = pd.concat([out[~out.deal.astype(str).isin([r["deal"] for r in rows])], pd.DataFrame(rows)], ignore_index=True)
out.to_csv(PROCESSED / "analyst_strat-20261119180000.csv", index=False)
