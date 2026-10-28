"""Turn the latest league-site snapshot into clean current-state tables.

Run:  PYTHONPATH=src python -m dfo.ingest.site
Outputs data/processed/site_rosters.csv, site_picks.csv, site_dead_money.csv, site_transactions.csv,
each stamped with the snapshot's as-of time.
"""
from __future__ import annotations

import json

from dfo.common import PROCESSED, ROOT, load_config
from dfo.ingest.build_history import write_csv

SNAP = ROOT / "data" / "snapshots" / "site" / "latest"


def _load(table: str) -> tuple[list[dict], str]:
    d = json.load(open(SNAP / f"{table}.json", encoding="utf-8"))
    return d["rows"], d["as_of"]


def team_owner_map() -> dict[str, str]:
    return {v["site_team_id"]: k for k, v in load_config("owners")["owners"].items() if v.get("site_team_id")}


def build() -> dict[str, int]:
    owners = team_owner_map()
    players, as_of = _load("players")
    lineups, _ = _load("team_lineups")
    slot = {r["player_id"]: r["slot"] for r in lineups}
    rosters = []
    for p in players:
        if not p.get("team_id"):
            continue
        sal = [p.get(f"salary_year{i}") or 0 for i in range(1, 6)]
        rosters.append(dict(
            owner=owners.get(p["team_id"], ""), team_id=p["team_id"], player_id=p["id"],
            sleeper_id=p.get("sleeper_id") or "", player=p["name"], pos=p["position"],
            nfl_team=p.get("nfl_team") or "", slot=slot.get(p["id"], ""),
            salary_now=sal[0], salaries=" ".join(str(s) for s in sal),
            salary_start_year=p.get("salary_start_year") or "",
            free_agency_year=p.get("free_agency_year") or "", acquisition=p.get("acquisition_type") or "",
            designation_rights=p.get("extension_eligibility") or "",
            injury_status=p.get("injury_status") or "", as_of=as_of))
    picks_raw, _ = _load("team_draft_picks")
    picks = [dict(owner=owners.get(r["team_id"], ""), team_id=r["team_id"], draft_year=r["draft_year"],
                  round=r["round"], label=r["pick_label"], used=bool(r.get("signed_player_id")), as_of=as_of)
             for r in picks_raw]
    dead_raw, _ = _load("team_dead_money")
    dead = [dict(owner=owners.get(r["team_id"], ""), team_id=r["team_id"], label=r.get("label") or "",
                 start_year=r.get("salary_start_year") or "",
                 amounts=" ".join(str(r.get(f"amount_year{i}") or 0) for i in range(1, 6)), as_of=as_of)
            for r in dead_raw]
    tx_raw, _ = _load("transactions")
    tx = [dict(occurred_at=r["occurred_at"], type=r["type"], player=r.get("player_name") or "",
               player_id=r.get("player_id") or "", from_owner=owners.get(r.get("from_team_id") or "", ""),
               to_owner=owners.get(r.get("to_team_id") or "", ""), amount=r.get("amount", ""),
               details=json.dumps(r.get("details") or {}), as_of=as_of)
          for r in sorted(tx_raw, key=lambda x: x["occurred_at"])]
    out = {
        "site_rosters": (rosters, list(rosters[0].keys())),
        "site_picks": (picks, list(picks[0].keys())),
        "site_dead_money": (dead, list(dead[0].keys())),
        "site_transactions": (tx, list(tx[0].keys())),
    }
    counts = {}
    for name, (rows, fields) in out.items():
        write_csv(name, rows, fields)
        counts[name] = len(rows)
    return counts


if __name__ == "__main__":
    for k, v in build().items():
        print(f"{k:18s} {v:5d} rows -> data/processed/{k}.csv")
