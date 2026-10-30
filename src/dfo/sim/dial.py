"""The dial: for every team and season, what one more unit of strength is worth in expected payout.

    PYTHONPATH=src python -m dfo.sim.dial       # writes data/processed/dial.csv and sim_2026.csv

Probe: +10 starting-lineup points a week. This season: added to every remaining regular-season week
and to the playoff total (about two playoff games); later seasons: +180 season points. Same random
draws with and without the probe, so the difference is the probe, not luck.

Output, per team and season: payout per point (payout dollars, P15, for one more regular-season
lineup point that season, its playoff effect included); later seasons also after the 0.88-a-year
haircut (P1). A move's worth = points it adds or costs in each season x that season's payout per
point, plus cap it saves or spends at that auction's price per point.

Posture (a reading, not an input to any decision): push when this season's payout per point is the
highest, build when a later season's (after haircut) is, "close" when this season and the best later
season are within 20% of each other.
"""
from __future__ import annotations

import datetime as dt

import numpy as np
import pandas as pd

from dfo.common import PROCESSED
from dfo.sim import future, playoffs, season
from dfo.valuation.auction import haircut

PROBE = 10.0
N_NOW, N_FUTURE = 4000, 8000


def season_payouts(boost=None, state=None, n=N_NOW) -> tuple[pd.DataFrame, dict]:
    s = season.simulate(n, seed=21, boost=boost, state=state)
    place = playoffs.simulate(s["rank"], s["owners"], seed=22, boost=boost)
    reg = season.summarize(s)
    po = playoffs.summarize(place, s["owners"])
    out = reg.merge(po, on="owner")
    out["exp_payout"] = out.exp_regular_payout + out.exp_playoff_payout
    return out, s


def calibration(state) -> pd.DataFrame:
    """Simulated weekly team scores (rest of 2026) against the actual 2026 scores so far."""
    s = season.simulate(2000, seed=31, state=state, return_detail=True)
    res = pd.read_csv(PROCESSED / "results_2026.csv")
    d = res[res.score_a.notna()]
    act = pd.concat([d[["owner_a" if "owner_a" in d else "team_a", "score_a"]].set_axis(["owner", "score"], axis=1),
                     d[["team_b", "score_b"]].set_axis(["owner", "score"], axis=1)])
    W = s["weekly"]
    out = pd.DataFrame([dict(measure="league mean team score", actual=act.score.mean(), simulated=W.mean()),
                        dict(measure="week-to-week spread within a team", actual=act.groupby("owner").score.std().mean(),
                             simulated=W.std(axis=1).mean())]).round(2)
    out["actual_weeks"] = f"1-{int(d.week.max())}"
    out.to_csv(PROCESSED / "sim_calibration.csv", index=False)
    return out


def build() -> pd.DataFrame:
    h = haircut()
    state = season.load_state()
    calibration(state)
    base, s = season_payouts(state=state)
    base.to_csv(PROCESSED / "sim_2026.csv", index=False)
    st = future.strengths()
    st.to_csv(PROCESSED / "sim_future_strength.csv", index=False)
    fut_base = future.odds(st, n=N_FUTURE)
    fut_base.to_csv(PROCESSED / "sim_future_odds.csv", index=False)
    rows = []
    for o in base.owner:
        up, _ = season_payouts(boost={o: PROBE}, state=state)
        now = float(up.set_index("owner").exp_payout[o] - base.set_index("owner").exp_payout[o])
        weeks_left = len(state[2])
        rec = dict(owner=o, payout_2026=float(base.set_index("owner").exp_payout[o]),
                   per_point_2026=now / (PROBE * weeks_left))
        for k, y in enumerate(future.FUTURE, start=1):
            fu = future.odds(st, n=N_FUTURE, boost={(y, o): PROBE * 18})
            b0 = fut_base[(fut_base.season == y) & (fut_base.owner == o)].exp_payout.iloc[0]
            b1 = fu[(fu.season == y) & (fu.owner == o)].exp_payout.iloc[0]
            rec[f"payout_{y}"] = float(b0)
            rec[f"per_point_{y}"] = float(b1 - b0) / (PROBE * 18)
            rec[f"per_point_{y}_haircut"] = float(b1 - b0) / (PROBE * 18) * h ** k
        rows.append(rec)
    d = pd.DataFrame(rows)
    cols = ["per_point_2026"] + [f"per_point_{y}_haircut" for y in future.FUTURE]
    def posture(r):
        now = float(r["per_point_2026"])
        later = float(max(r[c] for c in cols[1:]))
        if max(now, later) <= 0:
            return "no lever"
        lead, top, other = ("push", now, later) if now >= later else ("build", later, now)
        return f"{lead} (close)" if other >= 0.8 * top else lead
    d["posture"] = d.apply(posture, axis=1)
    d["share_now"] = (d.per_point_2026 / d[cols].clip(lower=0).sum(axis=1)).round(3)
    d["probe"] = f"+{PROBE:g} lineup points a week"
    d["made_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    d = d.round(3).sort_values("payout_2026", ascending=False)
    d.to_csv(PROCESSED / "dial.csv", index=False)
    return d


if __name__ == "__main__":
    import warnings
    warnings.filterwarnings("ignore")
    d = build()
    print(d.drop(columns=["made_at", "probe"]).to_string(index=False))
