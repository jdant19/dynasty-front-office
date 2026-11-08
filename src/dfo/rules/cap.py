"""Cap room this season, from the site snapshot (rules: config/league.yaml `cap`, `in_season`).

Room = cap - salaries that count - this season's dead money + cap received in trades (year 1).
$0 in-season adds are cap-exempt (they take nothing from FAAB or cap space). At or over the cap a
team may only make $0 adds, and FAAB bids are limited to the room.
"""
from __future__ import annotations

import json

import pandas as pd

from dfo.common import PROCESSED, ROOT, load_config


def _exempt() -> set[tuple[str, str]]:
    s = pd.read_csv(PROCESSED / "site_transactions.csv")
    f = s[s.type == "fa_signing"]
    ex = f.details.map(lambda d: bool(json.loads(d).get("cap_exempt")) if isinstance(d, str) else False)
    return set(zip(f[ex].to_owner, f[ex].player_id))


def cap_room() -> pd.DataFrame:
    """One row per owner: salary that counts, dead money, traded cap, room, max bid, as-of."""
    cap = load_config("league")["cap"]["amount"]
    r = pd.read_csv(PROCESSED / "site_rosters.csv")
    ex = _exempt()
    r = r[[(o, p) not in ex for o, p in zip(r.owner, r.player_id)]]
    d = pd.read_csv(PROCESSED / "site_dead_money.csv")
    d["now"] = d.amounts.astype(str).str.split().str[0].astype(float)
    tc = json.loads((ROOT / "data/snapshots/site/latest/traded_cap_space.json").read_text())
    team = pd.read_csv(PROCESSED / "site_rosters.csv").drop_duplicates("owner").set_index("team_id").owner
    traded = pd.Series({}, dtype=float)
    for row in tc["rows"]:
        o = team.get(row["team_id"])
        if o:
            traded[o] = traded.get(o, 0.0) + float(row.get("amount_year1") or 0)
    out = pd.DataFrame(dict(salary=r.groupby("owner").salary_now.sum(), dead=d.groupby("owner").now.sum()))
    out["traded_cap"] = traded
    out = out.fillna(0)
    out["room"] = (cap - out.salary - out.dead + out.traded_cap).round().astype(int)
    out["max_bid"] = out.room.clip(lower=0)              # at or over the cap: only $0 adds
    out["as_of"] = str(pd.read_csv(PROCESSED / "site_rosters.csv").as_of.max())
    return out.reset_index().rename(columns={"index": "owner"})


if __name__ == "__main__":
    print(cap_room().to_string(index=False))
