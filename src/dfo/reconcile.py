"""Compare computed league points with Yahoo's official points.

    PYTHONPATH=src python -m dfo.reconcile
Reads data/raw/league/yahoo_points_*_transcribed.csv and data/processed/points_<season>.csv,
writes data/processed/scoring_reconciliation.csv and prints a summary.
"""
from __future__ import annotations

import csv
from pathlib import Path

from dfo.common import PROCESSED, RAW, player_key

TOLERANCE = 0.011


def load_computed(season: int) -> dict[tuple[int, str], dict]:
    out = {}
    for r in csv.DictReader(open(PROCESSED / f"points_{season}.csv", encoding="utf-8")):
        if r["season_type"] == "REG":
            out[(int(r["week"]), player_key(r["player"]))] = r
    return out


def run(season: int = 2026) -> list[dict]:
    computed = load_computed(season)
    rows = []
    for path in sorted((RAW / "league").glob(f"yahoo_points_{season}_*_transcribed.csv")):
        for y in csv.DictReader(open(path, encoding="utf-8")):
            wk = int(y["week"])
            c = computed.get((wk, player_key(y["player"])))
            got = float(c["points"]) if c else None
            want = float(y["yahoo_points"])
            if c is None:
                status = "did_not_play" if want == 0 else "missing"   # no nflverse row and 0 on Yahoo
            else:
                status = "match" if abs(got - want) <= TOLERANCE else "differs"
            rows.append(dict(season=season, week=wk, owner=y["owner"], slot=y["slot"], player=y["player"],
                             yahoo=want, computed="" if got is None else got,
                             diff="" if got is None else round(got - want, 2), status=status,
                             source=Path(path).name))
    return rows


def main() -> None:
    rows = run()
    out = PROCESSED / "scoring_reconciliation.csv"
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    n = len(rows)
    match = sum(r["status"] in ("match", "did_not_play") for r in rows)
    print(f"{match}/{n} player-weeks match Yahoo exactly")
    for r in rows:
        if r["status"] not in ("match", "did_not_play"):
            print(f"  wk{r['week']} {r['owner']:6s} {r['slot']:4s} {r['player']:24s} yahoo {r['yahoo']:6.2f}"
                  f"  computed {r['computed']}  {r['status']}")


if __name__ == "__main__":
    main()
